#!/usr/bin/env bash
# 测试环境一次性整理：把 <version>-<hash> 目录改为纯版本号目录。
# 正常安装、升级和启动流程不会调用本脚本。
set -euo pipefail

[[ "${EUID}" -eq 0 || "${MIGRATE_ALLOW_NON_ROOT:-0}" == "1" ]] || {
    echo "请使用 root 执行: bash $0" >&2
    exit 1
}

OPENVPN_ROOT="${OPENVPN_ROOT_DIR:-/opt/openvpn}"
EASYRSA_ROOT="${EASYRSA_INSTALL_DIR:-/opt/ezrsa}"

normalize_version_root() {
    local root="${1:?}" binary_rel="${2:?}" label="${3:?}"
    local current="${root}/current" active_target active_name active_version
    local source name version target tmp actual_version

    [[ -d "${root}" && ! -L "${root}" ]] || {
        echo "${label} 安装目录不存在或不是普通目录: ${root}" >&2
        return 1
    }
    [[ -L "${current}" ]] || {
        echo "${label} current 不存在或不是软链接: ${current}" >&2
        return 1
    }

    active_target="$(readlink -f -- "${current}" 2>/dev/null || true)"
    [[ -n "${active_target}" && -d "${active_target}" ]] || {
        echo "${label} current 指向无效目录: ${current}" >&2
        return 1
    }
    case "${active_target}" in
        "${root}/"*) ;;
        *) echo "${label} current 指向安装根目录之外，拒绝自动移动: ${active_target}" >&2; return 1 ;;
    esac

    active_name="$(basename -- "${active_target}")"
    if [[ "${active_name}" =~ ^([0-9]+(\.[0-9]+){1,2})-[[:xdigit:]]{8,64}$ ]]; then
        active_version="${BASH_REMATCH[1]}"
    elif [[ "${active_name}" =~ ^[0-9]+(\.[0-9]+){1,2}$ ]]; then
        active_version="${active_name}"
    else
        echo "${label} 当前目录名称不是版本目录: ${active_name}" >&2
        return 1
    fi

    # 先检查重名目录，避免整理到一半才发现内容冲突。
    for source in "${root}"/*; do
        [[ -d "${source}" && ! -L "${source}" ]] || continue
        name="$(basename -- "${source}")"
        [[ "${name}" =~ ^([0-9]+(\.[0-9]+){1,2})-[[:xdigit:]]{8,64}$ ]] || continue
        version="${BASH_REMATCH[1]}"
        target="${root}/${version}"
        [[ "${source}" != "${target}" ]] || continue
        if [[ -e "${target}" || -L "${target}" ]]; then
            [[ -d "${target}" && ! -L "${target}" ]] || {
                echo "${label} 目标路径被占用: ${target}" >&2
                return 1
            }
            if ! diff -qr -- "${source}" "${target}" >/dev/null; then
                echo "${label} 同版本目录内容不一致，拒绝覆盖: ${source} -> ${target}" >&2
                return 1
            fi
        fi
    done

    # 版本目录直接平铺到安装根目录，不复制、不改动目录内部文件。
    for source in "${root}"/*; do
        [[ -d "${source}" && ! -L "${source}" ]] || continue
        name="$(basename -- "${source}")"
        [[ "${name}" =~ ^([0-9]+(\.[0-9]+){1,2})-[[:xdigit:]]{8,64}$ ]] || continue
        version="${BASH_REMATCH[1]}"
        target="${root}/${version}"
        if [[ -e "${target}" ]]; then
            rm -rf -- "${source}"
        else
            mv -T -- "${source}" "${target}"
        fi
    done

    [[ -x "${root}/${active_version}/${binary_rel}" ]] || {
        echo "${label} 整理后缺少当前版本可执行文件: ${root}/${active_version}/${binary_rel}" >&2
        return 1
    }
    if [[ "${label}" == "OpenVPN" ]]; then
        actual_version="$("${root}/${active_version}/${binary_rel}" --version 2>&1 | sed -n '1{s/.*OpenVPN[[:space:]]\+\([0-9][0-9.]*\).*/\1/p;}')"
    else
        actual_version="$("${root}/${active_version}/${binary_rel}" --version 2>&1 | grep -Eo '[0-9]+(\.[0-9]+){1,2}' | sed -n '1p')"
    fi
    [[ "${actual_version}" == "${active_version}" ]] || {
        echo "${label} 当前版本校验失败：目录=${active_version}，程序=${actual_version:-unknown}" >&2
        return 1
    }

    tmp="${root}/.current.link.$$"
    rm -f -- "${tmp}"
    ln -s "${active_version}" "${tmp}"
    mv -Tf -- "${tmp}" "${current}"

    if [[ "${label}" == "OpenVPN" ]]; then
        local part
        for part in sbin bin share lib lib64 include libexec; do
            if [[ -L "${root}/${part}" && "$(readlink -- "${root}/${part}")" == "current/${part}" ]]; then
                rm -f -- "${root}/${part}"
            fi
        done
    fi

    echo "[完成] ${label}: ${current} -> ${active_version}"
}

normalize_version_root "${OPENVPN_ROOT}" "sbin/openvpn" "OpenVPN"

if [[ -L "${EASYRSA_ROOT}/current" ]]; then
    normalize_version_root "${EASYRSA_ROOT}" "easyrsa" "EasyRSA"
else
    echo "[跳过] 未检测到 NiceVPN 管理的 EasyRSA current"
fi

echo
echo "目录整理完成。请检查："
"${OPENVPN_ROOT}/current/sbin/openvpn" --version | head -n 1
if [[ -x "${EASYRSA_ROOT}/current/easyrsa" ]]; then
    "${EASYRSA_ROOT}/current/easyrsa" --version
fi
