#!/usr/bin/env bash
# 仅在已测试环境执行一次：将 OpenVPN / EasyRSA 的旧 releases 布局调整为根目录平铺版本目录。
# 正常安装、升级和应用启动不会调用本脚本。
set -euo pipefail

[[ "$EUID" -eq 0 || "${MIGRATE_ALLOW_NON_ROOT:-0}" == "1" ]] || {
    echo "请使用 root 执行: bash $0" >&2
    exit 1
}

OPENVPN_ROOT="${OPENVPN_ROOT_DIR:-/opt/openvpn}"
OPENVPN_OLD_RELEASES="${OPENVPN_OLD_RELEASES_DIR:-/opt/openvpn-releases}"
EASYRSA_ROOT="${EASYRSA_INSTALL_DIR:-/opt/ezrsa}"
EASYRSA_OLD_ROOT="${EASYRSA_LEGACY_ROOT_DIR:-/opt/easy-rsa}"
STAMP="$(date +%Y%m%d%H%M%S)-$$"

openvpn_binary() {
    [[ -x "$1/sbin/openvpn" ]] && { echo "$1/sbin/openvpn"; return; }
    [[ -x "$1/bin/openvpn" ]] && { echo "$1/bin/openvpn"; return; }
    return 1
}

easyrsa_binary() {
    [[ -x "$1/easyrsa" ]] && { echo "$1/easyrsa"; return; }
    return 1
}

copy_openvpn_dir() {
    local source="$1" target="$2" source_bin target_bin
    source_bin="$(openvpn_binary "$source")" || return 0
    if [[ -e "$target" ]]; then
        target_bin="$(openvpn_binary "$target")" || {
            echo "OpenVPN 目标目录异常，拒绝覆盖: $target" >&2
            return 1
        }
        [[ "$(sha256sum "$source_bin" | awk '{print $1}')" == \
           "$(sha256sum "$target_bin" | awk '{print $1}')" ]] || {
            echo "OpenVPN 版本目录内容冲突，拒绝覆盖: $target" >&2
            return 1
        }
    else
        cp -a -- "$source" "$target"
    fi
}

migrate_openvpn() {
    local active="" active_id="" binary version hash source stage backup part
    if [[ -L "$OPENVPN_ROOT" ]]; then
        active="$(readlink -f -- "$OPENVPN_ROOT")"
    elif [[ -L "$OPENVPN_ROOT/current" ]]; then
        active="$(readlink -f -- "$OPENVPN_ROOT/current")"
    elif [[ -d "$OPENVPN_ROOT/current" ]] && openvpn_binary "$OPENVPN_ROOT/current" >/dev/null 2>&1; then
        active="$OPENVPN_ROOT/current"
    elif [[ -d "$OPENVPN_ROOT" ]] && openvpn_binary "$OPENVPN_ROOT" >/dev/null 2>&1; then
        active="$OPENVPN_ROOT"
    fi
    [[ -n "$active" && -d "$active" ]] || {
        echo "无法识别当前 OpenVPN 目录: $OPENVPN_ROOT" >&2
        return 1
    }

    stage="$(mktemp -d "$(dirname -- "$OPENVPN_ROOT")/.openvpn-layout.XXXXXX")"
    # 先把旧 releases 中的版本平铺复制到 staging。
    if [[ -d "$OPENVPN_OLD_RELEASES" ]]; then
        for source in "$OPENVPN_OLD_RELEASES"/*; do
            [[ -d "$source" && ! -L "$source" ]] || continue
            copy_openvpn_dir "$source" "$stage/$(basename "$source")"
        done
    fi
    # 兼容已经有部分版本目录位于 /opt/openvpn 的情况。
    if [[ -d "$OPENVPN_ROOT" && ! -L "$OPENVPN_ROOT" ]]; then
        for source in "$OPENVPN_ROOT"/*; do
            [[ -d "$source" && ! -L "$source" ]] || continue
            [[ "$source" == "$OPENVPN_ROOT/current" ]] && continue
            openvpn_binary "$source" >/dev/null 2>&1 || continue
            copy_openvpn_dir "$source" "$stage/$(basename "$source")"
        done
    fi

    if [[ "$active" == "$OPENVPN_ROOT" || "$active" == "$OPENVPN_ROOT/current" ]]; then
        binary="$(openvpn_binary "$active")"
        version="$("$binary" --version 2>&1 | sed -n '1{s/.*OpenVPN[[:space:]]\+\([0-9][0-9.]*\).*/\1/p;}')"
        [[ -n "$version" ]] || { rm -rf -- "$stage"; echo "无法识别 OpenVPN 版本: $binary" >&2; return 1; }
        hash="$(sha256sum "$binary" | awk '{print $1}' | cut -c1-12)"
        active_id="legacy-$version-$hash"
        copy_openvpn_dir "$active" "$stage/$active_id"
    else
        active_id="$(basename "$active")"
        if [[ ! -d "$stage/$active_id" ]]; then
            copy_openvpn_dir "$active" "$stage/$active_id"
        fi
    fi

    [[ -d "$stage/$active_id" ]] || {
        rm -rf -- "$stage"
        echo "当前 OpenVPN 版本未整理成功: $active_id" >&2
        return 1
    }
    ln -s "$active_id" "$stage/current"
    for part in sbin bin share lib lib64 include libexec; do
        [[ -d "$stage/$active_id/$part" ]] && ln -s "current/$part" "$stage/$part"
    done

    backup="$OPENVPN_ROOT.legacy-layout.$STAMP"
    if [[ -e "$OPENVPN_ROOT" || -L "$OPENVPN_ROOT" ]]; then
        mv -T -- "$OPENVPN_ROOT" "$backup"
    fi
    if ! mv -T -- "$stage" "$OPENVPN_ROOT"; then
        [[ -e "$backup" || -L "$backup" ]] && mv -T -- "$backup" "$OPENVPN_ROOT" || true
        echo "OpenVPN 新目录发布失败，已尝试恢复旧入口" >&2
        return 1
    fi
    echo "[完成] OpenVPN current -> $active_id"
    [[ ! -e "$backup" && ! -L "$backup" ]] || echo "[备份] 原入口: $backup"
    [[ ! -d "$OPENVPN_OLD_RELEASES" ]] || echo "[保留] 旧版本仓库: $OPENVPN_OLD_RELEASES"
}

migrate_easyrsa() {
    local current="$EASYRSA_ROOT/current" active="" active_id="" source version
    local new_link backup tmp target source_bin target_bin

    mkdir -p "$EASYRSA_ROOT"
    if [[ -L "$current" ]]; then
        active="$(readlink -f -- "$current")"
    elif [[ -d "$current" ]] && easyrsa_binary "$current" >/dev/null 2>&1; then
        active="$current"
    elif easyrsa_binary "$EASYRSA_ROOT" >/dev/null 2>&1; then
        active="$EASYRSA_ROOT"
    elif [[ -L "$EASYRSA_OLD_ROOT/current" ]]; then
        active="$(readlink -f -- "$EASYRSA_OLD_ROOT/current")"
    elif easyrsa_binary "$EASYRSA_OLD_ROOT/current" >/dev/null 2>&1; then
        active="$EASYRSA_OLD_ROOT/current"
    elif easyrsa_binary "$EASYRSA_OLD_ROOT" >/dev/null 2>&1; then
        active="$EASYRSA_OLD_ROOT"
    fi
    if [[ -z "$active" || ! -d "$active" ]]; then
        echo "[跳过] 未检测到 EasyRSA"
        return 0
    fi

    if [[ "$active" == "$EASYRSA_ROOT" || "$active" == "$EASYRSA_ROOT/current" || "$active" == "$EASYRSA_OLD_ROOT" ]]; then
        source_bin="$(easyrsa_binary "$active")"
        version="$("$source_bin" --version 2>&1 | grep -Eo '[0-9]+(\.[0-9]+){1,2}' | sed -n '1p')"
        [[ -n "$version" ]] || { echo "无法识别 EasyRSA 版本: $source_bin" >&2; return 1; }
        active_id="legacy-$version"
    else
        active_id="$(basename "$active")"
    fi

    # 把 releases 下的版本目录直接移到 /opt/ezrsa/，不改动其内容。
    if [[ -d "$EASYRSA_ROOT/releases" ]]; then
        for source in "$EASYRSA_ROOT"/releases/*; do
            [[ -d "$source" && ! -L "$source" ]] || continue
            target="$EASYRSA_ROOT/$(basename "$source")"
            if [[ -e "$target" ]]; then
                source_bin="$(easyrsa_binary "$source")" || continue
                target_bin="$(easyrsa_binary "$target")" || {
                    echo "EasyRSA 目标目录异常: $target" >&2
                    return 1
                }
                [[ "$(sha256sum "$source_bin" | awk '{print $1}')" == \
                   "$(sha256sum "$target_bin" | awk '{print $1}')" ]] || {
                    echo "EasyRSA 版本目录冲突，拒绝覆盖: $target" >&2
                    return 1
                }
            else
                mv -T -- "$source" "$target"
            fi
        done
    fi

    target="$EASYRSA_ROOT/$active_id"
    if [[ ! -d "$target" ]]; then
        tmp="$(mktemp -d "$(dirname -- "$EASYRSA_ROOT")/.ezrsa-layout.XXXXXX")"
        cp -a -- "$active/." "$tmp/"
        mv -T -- "$tmp" "$target"
    fi
    if [[ -d "$current" && ! -L "$current" ]]; then
        backup="$EASYRSA_ROOT/current.legacy-layout.$STAMP"
        mv -T -- "$current" "$backup"
        echo "[备份] 原 EasyRSA current: $backup"
    fi
    new_link="$EASYRSA_ROOT/.current.link.$STAMP"
    ln -s "$active_id" "$new_link"
    mv -Tf -- "$new_link" "$current"
    "$current/easyrsa" --version >/dev/null
    if [[ ! -e /usr/local/bin/easyrsa || -L /usr/local/bin/easyrsa ]]; then
        new_link="/usr/local/bin/.easyrsa.link.$STAMP"
        ln -s "$current/easyrsa" "$new_link"
        mv -Tf -- "$new_link" /usr/local/bin/easyrsa
    fi

    if [[ -d "$EASYRSA_ROOT/releases" ]]; then
        backup="$EASYRSA_ROOT/releases.legacy-layout.$STAMP"
        mv -T -- "$EASYRSA_ROOT/releases" "$backup"
        echo "[备份] 原 EasyRSA releases: $backup"
    fi
    echo "[完成] EasyRSA current -> $active_id"
    [[ ! -d "$EASYRSA_OLD_ROOT" ]] || echo "[保留] 旧 EasyRSA 目录: $EASYRSA_OLD_ROOT"
}

migrate_openvpn
migrate_easyrsa

echo
echo "目录调整完成。请检查 /opt/openvpn/current/sbin/openvpn --version、/opt/ezrsa/current/easyrsa --version 和 VPN 服务状态。"
echo "确认正常后，再手动清理 .legacy-layout 备份及旧 /opt/openvpn-releases。"
