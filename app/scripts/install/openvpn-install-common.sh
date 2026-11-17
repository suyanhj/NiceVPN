#!/usr/bin/env bash
# OpenVPN 安装公共函数（由 debian/rhel 入口脚本 source）
# 环境变量见各入口脚本头部说明；未设置时使用下列默认值。

# 直接 source 本文件的调用方也自动获得原子写入 helper；远端内联时已预加载则跳过。
if ! declare -F nicevpn_atomic_write_stdin >/dev/null 2>&1; then
    _OPENVPN_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../common" && pwd)"
    # shellcheck source=../common/atomic-files.sh
    source "${_OPENVPN_COMMON_DIR}/atomic-files.sh"
fi

: "${OPENVPN_MIN_VERSION:=2.7.0}"
: "${OPENVPN_TAG:=v2.7.0}"
: "${OPENVPN_ASSET:=openvpn-2.7.0.tar.gz}"
: "${OPENVPN_ROOT_DIR:=/opt/openvpn}"
: "${OPENVPN_PREFIX:=${OPENVPN_ROOT_DIR}/current}"
: "${OPENVPN_SBIN_DIR:=${OPENVPN_PREFIX}/sbin}"
: "${OPENVPN_BIN:=${OPENVPN_SBIN_DIR}/openvpn}"
: "${OPENVPN_CACHE:=/tmp/${OPENVPN_ASSET}}"
: "${OPENVPN_SRC_ROOT:=/tmp/ovpn-mgmt-openvpn-src}"
: "${EASYRSA_INSTALL_DIR:=/opt/ezrsa}"
: "${EASYRSA_LEGACY_INSTALL_DIR:=/opt/easy-rsa}"
: "${OPENVPN_SYSTEMD_UNIT_DIR:=/etc/systemd/system}"
: "${OPENVPN_CLI_LINK:=/usr/local/sbin/openvpn}"
: "${EASYRSA_CLI_LINK:=/usr/local/bin/easyrsa}"
: "${EASYRSA_TAG:=v3.2.4}"
: "${EASYRSA_ASSET:=EasyRSA-3.2.4.tgz}"
: "${EASYRSA_CACHE:=/tmp/${EASYRSA_ASSET}}"
: "${INSTALL_EASYRSA:=0}"
: "${INSTALL_SERVER_SYSTEMD:=0}"
: "${INSTALL_CLIENT_SYSTEMD:=0}"
: "${INSTALL_SERVER_OVERRIDE:=0}"
: "${OPENVPN_CREATE_SYMLINK:=1}"
: "${OPENVPN_FORCE_SOURCE:=0}"
: "${OPENVPN_STAGE_ONLY:=0}"
: "${OPENVPN_STAGE_DIR:=}"
: "${OPENVPN_INSTALL_MODE_FILE:=/etc/openvpn/mgmt/openvpn-install-mode}"
: "${OPENVPN_DCO_MIN_LIBNL_VERSION:=3.4.0}"
: "${EASYRSA_FORCE_INSTALL:=0}"
: "${EASYRSA_INSTALL_LOCK_FILE:=/run/lock/nicevpn-easyrsa-install.lock}"

: "${OPENVPN_SRC_DIR:=${OPENVPN_SRC_ROOT}/${OPENVPN_ASSET%.tar.gz}}"

version_ge() {
    # 用法: version_ge <ver> <min>
    [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n 1)" = "$2" ]
}

_log() {
    echo "[openvpn-install] $*"
}

# 返回系统中已有且可执行的 EasyRSA，不限定 NiceVPN 自己的安装目录。
easyrsa_find_existing_binary() {
    local candidate
    local -a candidates=(
        "${EASYRSA_INSTALL_DIR}/current/easyrsa"
        "${OPENVPN_PREFIX}/share/easy-rsa/easyrsa"
        "${OPENVPN_PREFIX}/share/easy-rsa/3/easyrsa"
        "/usr/share/easy-rsa/easyrsa"
        "/usr/share/easy-rsa/3/easyrsa"
        "/usr/share/easy-rsa/3.0/easyrsa"
        "/usr/local/share/easy-rsa/easyrsa"
    )
    for candidate in "${candidates[@]}"; do
        if [[ -f "${candidate}" && -x "${candidate}" ]]; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done

    candidate="$(command -v easyrsa 2>/dev/null || true)"
    if [[ -n "${candidate}" && -f "${candidate}" && -x "${candidate}" ]]; then
        printf '%s\n' "${candidate}"
        return 0
    fi
    return 1
}

# EasyRSA 安装使用独立锁，避免同时下载、发布和切换 current。
easyrsa_acquire_install_lock() {
    local lock_parent
    if declare -F nicevpn_ensure_commands >/dev/null 2>&1; then
        nicevpn_ensure_commands flock util-linux
    fi
    command -v flock >/dev/null 2>&1 || {
        echo "缺少 flock，无法保证 EasyRSA 安装互斥" >&2
        return 1
    }
    lock_parent="$(dirname -- "${EASYRSA_INSTALL_LOCK_FILE}")"
    mkdir -p -- "${lock_parent}"
    exec 8>"${EASYRSA_INSTALL_LOCK_FILE}"
    if ! flock -n 8; then
        echo "已有 EasyRSA 安装任务正在执行，请稍后重试" >&2
        return 1
    fi
}

# 已有 EasyRSA 默认复用；只有显式 EASYRSA_FORCE_INSTALL=1 才允许覆盖当前安装入口。
easyrsa_reuse_existing() {
    [[ "${INSTALL_EASYRSA}" == "1" ]] || return 0
    [[ "${EASYRSA_FORCE_INSTALL}" == "1" ]] && return 0

    if [[ -e "${EASYRSA_INSTALL_DIR}/current" && ! -L "${EASYRSA_INSTALL_DIR}/current" ]]; then
        echo "EasyRSA current 必须是软链接: ${EASYRSA_INSTALL_DIR}/current" >&2
        return 1
    fi
    if [[ -L "${EASYRSA_INSTALL_DIR}/current" ]]; then
        local current_target
        current_target="$(readlink -f -- "${EASYRSA_INSTALL_DIR}/current" 2>/dev/null || true)"
        case "${current_target}" in
            "${EASYRSA_INSTALL_DIR}/"*) ;;
            *) echo "EasyRSA current 指向根目录外的版本目录，拒绝继续: ${current_target}" >&2; return 1 ;;
        esac
        [[ -x "${current_target}/easyrsa" ]] || {
            echo "EasyRSA current 目标缺少可执行脚本: ${current_target}" >&2
            return 1
        }
    fi
    if [[ -f "${EASYRSA_INSTALL_DIR}/easyrsa" && ! -L "${EASYRSA_INSTALL_DIR}/easyrsa" ]]; then
        echo "检测到旧版 EasyRSA 平铺目录，当前安装仅支持版本目录 + current 结构: ${EASYRSA_INSTALL_DIR}" >&2
        return 1
    fi

    local existing_bin
    if existing_bin="$(easyrsa_find_existing_binary)"; then
        _log "检测到已有 EasyRSA: ${existing_bin}，跳过下载、安装和覆盖"
        INSTALL_EASYRSA=0
    fi
}

# DCO 是可选性能优化：libnl-genl 不满足要求时，不应阻断 OpenVPN 基础安装。
openvpn_detect_dco_support() {
    local pkg_config_bin="${PKG_CONFIG:-pkg-config}"
    local libnl_version=""

    if ! command -v "${pkg_config_bin}" >/dev/null 2>&1; then
        _log "可选功能 DCO：未找到 ${pkg_config_bin}，编译时禁用 DCO"
        return 1
    fi

    if ! libnl_version="$("${pkg_config_bin}" --modversion libnl-genl-3.0 2>/dev/null)" \
        || [ -z "${libnl_version}" ]; then
        _log "可选功能 DCO：未找到 libnl-genl-3.0 开发包，编译时禁用 DCO"
        return 1
    fi

    if ! version_ge "${libnl_version}" "${OPENVPN_DCO_MIN_LIBNL_VERSION}"; then
        _log "可选功能 DCO：libnl-genl-3.0 ${libnl_version} 低于要求 ${OPENVPN_DCO_MIN_LIBNL_VERSION}，编译时禁用 DCO"
        return 1
    fi

    _log "可选功能 DCO：libnl-genl-3.0 ${libnl_version} 满足要求，保留 DCO 编译支持"
    return 0
}

openvpn_configure_with_optional_features() {
    local -a configure_args=(
        "--prefix=${OPENVPN_PREFIX}"
        --enable-async-push
        --enable-systemd
    )
    local configure_status=0

    if openvpn_detect_dco_support; then
        if ./configure "${configure_args[@]}"; then
            return 0
        else
            configure_status=$?
        fi

        # 处理预检与 configure 实际检测结果不一致的情况，只对明确的 DCO 依赖错误回退。
        if [ -f config.log ] && grep -Eqi \
            'libnl-genl-3\.0 package not found or too old|3\.4\.0 or newer for DCO' config.log; then
            _log "警告：configure 检测到 DCO 依赖不可用，自动禁用 DCO 后重试"
            ./configure "${configure_args[@]}" --disable-dco
            return $?
        fi
        return "${configure_status}"
    fi

    _log "继续编译 OpenVPN 基础功能（DCO 已禁用）"
    ./configure "${configure_args[@]}" --disable-dco
}

openvpn_download_source() {
    # 需要 OPENVPN_DOWNLOAD_URLS（换行分隔的候选 URL 列表）
    if [ -s "${OPENVPN_CACHE}" ] && ! tar -tzf "${OPENVPN_CACHE}" >/dev/null 2>&1; then
        _log "OpenVPN 源码缓存校验失败，删除后重新下载: ${OPENVPN_CACHE}"
        rm -f -- "${OPENVPN_CACHE}"
    fi
    if [ ! -s "${OPENVPN_CACHE}" ]; then
        local ok=0 url
        while IFS= read -r url; do
            [ -n "${url}" ] || continue
            _log "下载 OpenVPN 源码包: ${url}"
            if curl -fL --retry 3 --retry-delay 2 "${url}" -o "${OPENVPN_CACHE}.part" \
                && tar -tzf "${OPENVPN_CACHE}.part" >/dev/null; then
                mv -f "${OPENVPN_CACHE}.part" "${OPENVPN_CACHE}"
                ok=1
                break
            fi
            rm -f "${OPENVPN_CACHE}.part"
        done <<EOF
${OPENVPN_DOWNLOAD_URLS}
EOF
        [ "${ok}" = "1" ] || { echo "下载 OpenVPN 源码包失败" >&2; exit 1; }
    else
        _log "复用 OpenVPN 源码缓存: ${OPENVPN_CACHE}"
        tar -tzf "${OPENVPN_CACHE}" >/dev/null
    fi
}

# 校验 current 入口和目录结构；不自动迁移旧布局。
openvpn_recover_source_install() {
    local root="${OPENVPN_ROOT_DIR}" current="${OPENVPN_PREFIX}"
    local target part stage_root
    [[ ! -L "${root}" ]] || {
        echo "OpenVPN 安装根目录不能是软链接: ${root}；预期为普通目录，版本由 ${current} 管理" >&2
        return 1
    }
    [[ ! -e "${root}" || -d "${root}" ]] || {
        echo "OpenVPN 安装根目录不是目录: ${root}" >&2
        return 1
    }
    mkdir -p -- "${root}"

    if [[ -e "${current}" && ! -L "${current}" ]]; then
        echo "OpenVPN current 必须是软链接: ${current}" >&2
        return 1
    fi
    if [[ -L "${current}" ]]; then
        target="$(readlink -f -- "${current}" 2>/dev/null || true)"
        [[ -n "${target}" && -d "${target}" ]] || {
            echo "OpenVPN current 是失效软链接: ${current}" >&2
            return 1
        }
        case "${target}" in
            "${root}/"*) ;;
            *) echo "OpenVPN current 指向根目录外的版本目录，拒绝继续: ${target}" >&2; return 1 ;;
        esac
        [[ -x "${target}/sbin/openvpn" || -x "${target}/bin/openvpn" ]] || {
            echo "OpenVPN current 目标缺少可执行 binary: ${target}" >&2
            return 1
        }
    fi

    for part in sbin bin share lib lib64 include libexec; do
        if [[ -e "${root}/${part}" && ! -L "${root}/${part}" ]]; then
            echo "检测到旧版 OpenVPN 目录: ${root}/${part}；当前安装仅支持版本目录 + current 结构" >&2
            return 1
        fi
        if [[ -L "${root}/${part}" && "$(readlink -- "${root}/${part}")" != "current/${part}" ]]; then
            echo "OpenVPN 兼容入口指向异常: ${root}/${part}" >&2
            return 1
        fi
    done

    for stage_root in "${root}"/.stage.* "${root}"/.nicevpn-openvpn-upgrade.*; do
        [[ -d "${stage_root}" ]] || continue
        rm -rf -- "${stage_root}"
    done
}


# 仅在启动检查时迁移历史目录；正常升级仍然只切换 current 软链接。
openvpn_migrate_legacy_openvpn_root() {
    local root="${OPENVPN_ROOT_DIR%/}" current="${OPENVPN_PREFIX%/}"
    local binary="" version_output="" version="" release_dir part

    [[ -d "${root}" && ! -L "${root}" ]] || return 0
    if [[ -e "${current}" || -L "${current}" ]]; then
        [[ -L "${current}" ]] || {
            echo "OpenVPN current 必须是软链接，保留现有目录不做覆盖: ${current}" >&2
            return 1
        }
        return 0
    fi

    binary="${root}/sbin/openvpn"
    [[ -x "${binary}" ]] || binary="${root}/bin/openvpn"
    if [[ ! -x "${binary}" ]]; then
        for part in sbin bin include lib lib64 libexec share; do
            if [[ -e "${root}/${part}" && ! -L "${root}/${part}" ]]; then
                echo "检测到旧版 OpenVPN 目录但没有可执行二进制，保留原目录: ${root}" >&2
                return 1
            fi
        done
        return 0
    fi

    version_output="$("${binary}" --version 2>&1)" || {
        echo "无法读取旧版 OpenVPN 版本，保留原目录: ${binary}" >&2
        return 1
    }
    version="$(printf '%s\n' "${version_output}" | sed -n '1{s/.*OpenVPN[[:space:]]\+\([0-9][0-9.]*\).*/\1/p;}')"
    [[ "${version}" =~ ^[0-9]+([.][0-9]+)+$ ]] || {
        echo "无法识别旧版 OpenVPN 版本，保留原目录: ${binary}" >&2
        return 1
    }

    release_dir="${root}/${version}"
    [[ ! -e "${release_dir}" && ! -L "${release_dir}" ]] || {
        echo "OpenVPN 迁移目标已存在，拒绝覆盖: ${release_dir}" >&2
        return 1
    }

    mkdir -- "${release_dir}" || return 1
    if ! find "${root}" -mindepth 1 -maxdepth 1 ! -name "${version}" \
        -exec mv -t "${release_dir}" -- {} +; then
        find "${release_dir}" -mindepth 1 -maxdepth 1 -exec mv -t "${root}" -- {} + || true
        rmdir -- "${release_dir}" 2>/dev/null || true
        echo "迁移旧版 OpenVPN 目录失败，已尽量恢复原目录: ${root}" >&2
        return 1
    fi

    if ! nicevpn_atomic_symlink "${version}" "${current}"; then
        find "${release_dir}" -mindepth 1 -maxdepth 1 -exec mv -t "${root}" -- {} + || true
        rmdir -- "${release_dir}" 2>/dev/null || true
        echo "创建 OpenVPN current 软链接失败，已尽量恢复原目录: ${current}" >&2
        return 1
    fi

    _log "已将旧版 OpenVPN ${version} 迁移到 ${release_dir}，current -> ${version}"
}

easyrsa_migrate_legacy_root() {
    local legacy="${EASYRSA_LEGACY_INSTALL_DIR%/}" root="${EASYRSA_INSTALL_DIR%/}"
    local current_target="" version="" release="" release_version=""

    [[ -d "${legacy}" && ! -L "${legacy}" ]] || return 0
    if [[ -e "${root}" || -L "${root}" ]]; then
        if [[ -L "${root}/current" && -x "${root}/current/easyrsa" ]]; then
            _log "新 EasyRSA 目录已就绪，保留旧目录不做覆盖: ${legacy}"
            return 0
        fi
        echo "新旧 EasyRSA 目录同时存在，拒绝覆盖: ${legacy} / ${root}" >&2
        return 1
    fi

    if [[ ! -L "${legacy}/current" ]]; then
        if [[ -d "${legacy}/releases" ]]; then
            echo "旧 EasyRSA releases 目录存在但 current 不是软链接，保留原目录: ${legacy}" >&2
            return 1
        fi
        return 0
    fi

    current_target="$(readlink -f -- "${legacy}/current" 2>/dev/null || true)"
    [[ -n "${current_target}" && -d "${current_target}" ]] || {
        echo "旧 EasyRSA current 软链接无效，保留原目录: ${legacy}/current" >&2
        return 1
    }
    [[ "$(dirname -- "${current_target}")" == "${legacy}/releases" ]] || {
        echo "旧 EasyRSA current 未指向 releases/<version>，拒绝自动迁移: ${current_target}" >&2
        return 1
    }
    [[ -x "${current_target}/easyrsa" ]] || {
        echo "旧 EasyRSA 当前版本缺少可执行 easyrsa: ${current_target}" >&2
        return 1
    }
    version="${current_target##*/}"

    # 先检查所有目标，避免迁移到一半才发现版本目录冲突。
    for release in "${legacy}"/releases/*; do
        [[ -d "${release}" && ! -L "${release}" ]] || continue
        release_version="${release##*/}"
        [[ ! -e "${legacy}/${release_version}" && ! -L "${legacy}/${release_version}" ]] || {
            echo "EasyRSA 平铺版本目录已存在，拒绝迁移: ${legacy}/${release_version}" >&2
            return 1
        }
    done

    mv -T -- "${legacy}" "${root}" || {
        echo "迁移 EasyRSA 根目录失败: ${legacy} -> ${root}" >&2
        return 1
    }

    for release in "${root}"/releases/*; do
        [[ -d "${release}" && ! -L "${release}" ]] || continue
        release_version="${release##*/}"
        mv -T -- "${release}" "${root}/${release_version}" || {
            echo "整理 EasyRSA 版本目录失败: ${release}" >&2
            return 1
        }
    done
    rmdir -- "${root}/releases" 2>/dev/null || true

    nicevpn_atomic_symlink "${version}" "${root}/current" || {
        echo "创建 EasyRSA current 软链接失败: ${root}/current" >&2
        return 1
    }
    _log "已将旧版 EasyRSA 迁移到 ${root}/${version}，current -> ${version}"
}

openvpn_update_legacy_paths() {
    local root="${OPENVPN_ROOT_DIR%/}" current="${OPENVPN_PREFIX%/}"
    local unit changed=0 target

    # 只改 NiceVPN 管理的 systemd 模板文件，不碰发行版包管理器的 unit。
    for unit in \
        "${OPENVPN_SYSTEMD_UNIT_DIR}/openvpn-server@.service" \
        "${OPENVPN_SYSTEMD_UNIT_DIR}/openvpn-client@.service"; do
        [[ -f "${unit}" && ! -L "${unit}" ]] || continue
        if grep -Fq "${root}/sbin/" "${unit}" || grep -Fq "${root}/bin/" "${unit}"; then
            nicevpn_atomic_write_command "${unit}" 0644 sed \
                -e "s#${root}/sbin/#${current}/sbin/#g" \
                -e "s#${root}/bin/#${current}/bin/#g" \
                "${unit}" || return 1
            changed=1
            _log "已更新 systemd 二进制路径: ${unit}"
        fi
    done
    if [[ "${changed}" == "1" ]] && command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload
    fi

    if [[ -L "${OPENVPN_CLI_LINK}" ]]; then
        target="$(readlink -- "${OPENVPN_CLI_LINK}")"
        case "${target}" in
            "${root}/sbin/openvpn")
                [[ -x "${current}/sbin/openvpn" ]] &&
                    nicevpn_atomic_symlink "${current}/sbin/openvpn" "${OPENVPN_CLI_LINK}" || return 1
                ;;
            "${root}/bin/openvpn")
                [[ -x "${current}/bin/openvpn" ]] &&
                    nicevpn_atomic_symlink "${current}/bin/openvpn" "${OPENVPN_CLI_LINK}" || return 1
                ;;
        esac
    fi

    if [[ -L "${EASYRSA_CLI_LINK}" && -x "${EASYRSA_INSTALL_DIR}/current/easyrsa" ]]; then
        target="$(readlink -- "${EASYRSA_CLI_LINK}")"
        case "${target}" in
            "${EASYRSA_LEGACY_INSTALL_DIR}"/*|"${EASYRSA_INSTALL_DIR}/releases/"*)
                nicevpn_atomic_symlink "${EASYRSA_INSTALL_DIR}/current/easyrsa" "${EASYRSA_CLI_LINK}" || return 1
                ;;
        esac
    fi
}

openvpn_migrate_legacy_layout() {
    openvpn_migrate_legacy_openvpn_root || return 1
    easyrsa_migrate_legacy_root || return 1
    openvpn_update_legacy_paths || return 1

    # 仅在源码入口存在时校验；系统包安装不需要创建 /opt/openvpn。
    if [[ -e "${OPENVPN_PREFIX}" || -L "${OPENVPN_PREFIX}" ]]; then
        openvpn_recover_source_install || return 1
    fi
}

# 源码安装事务快照放在 prefix 同级，避免 /tmp 清理使回滚材料丢失。
# 固定名称与安装 flock 配合，异常退出或 SIGKILL 后可在下次重试前恢复。
openvpn_source_transaction_root() {
    local prefix="${OPENVPN_PREFIX%/}"
    [[ "${prefix}" == /* && "${prefix}" != "/" ]] || {
        echo "无效的 OPENVPN_PREFIX: ${OPENVPN_PREFIX}" >&2
        return 1
    }
    printf '%s/.%s.install-transaction\n' "$(dirname -- "${prefix}")" "$(basename -- "${prefix}")"
}

openvpn_source_transaction_paths() {
    printf '%s\n' \
        /etc/systemd/system/openvpn-server@.service \
        /etc/systemd/system/openvpn-client@.service \
        /etc/systemd/system/openvpn-server@.service.d/10-ovpn-mgmt.conf \
        /usr/local/sbin/openvpn \
        "${OPENVPN_INSTALL_MODE_FILE}"
    if [[ "${INSTALL_EASYRSA}" == "1" ]]; then
        printf '%s\n' "${EASYRSA_INSTALL_DIR}/current" /usr/local/bin/easyrsa
    fi
}

# 失败时恢复原 prefix + systemd 文件 + CLI symlink；独立恢复各文件，
# 任何一项失败均保留快照供人工排查或下次安装继续恢复。
openvpn_restore_source_transaction() {
    local root prefix backup="" new_prefix="" failed=0 idx=0 path mode
    root="$(openvpn_source_transaction_root)" || return 1
    [[ -d "${root}" && ! -L "${root}" ]] || {
        [[ ! -e "${root}" && ! -L "${root}" ]] && return 0
        echo "安装事务目录不安全: ${root}" >&2
        return 1
    }
    if [[ ! -f "${root}/ready" ]]; then
        # ready 标志最后写入；此前仅保存快照，没有触碰已安装文件。
        rm -rf -- "${root}"
        return 0
    fi

    prefix="${OPENVPN_PREFIX%/}"
    if [[ -f "${root}/switch-intent" ]]; then
        if [[ -f "${root}/backup-prefix" ]]; then
            IFS= read -r backup < "${root}/backup-prefix" || true
        fi
        if [[ -n "${backup}" ]]; then
            if [[ -d "${backup}" || -L "${backup}" ]]; then
                if [[ -d "${prefix}" && ! -L "${prefix}" ]]; then
                    echo "回滚拒绝用版本软链接覆盖普通目录: ${prefix}" >&2
                    failed=1
                elif ! nicevpn_atomic_symlink "$(basename -- "${backup}")" "${prefix}"; then
                    echo "无法恢复 OpenVPN 旧版本入口: ${backup} -> ${prefix}" >&2
                    failed=1
                fi
            elif [[ -d "${prefix}" && ! -L "${prefix}" ]]; then
                # 事务意图已记录，但 current 仍是普通目录，说明软链接切换尚未执行。
                _log "检测到 current 尚未切换，保留原 OpenVPN 目录: ${prefix}"
            else
                echo "OpenVPN 回滚目标不存在，无法恢复: ${backup}" >&2
                failed=1
            fi
        elif [[ ! -f "${root}/original-prefix" ]]; then
            # 首次安装此前没有 prefix；只移除本事务创建的稳定软链接，不删除目录或 release。
            if [[ -L "${prefix}" ]]; then
                rm -f -- "${prefix}" || failed=1
            elif [[ -e "${prefix}" ]]; then
                echo "首次安装回滚拒绝删除非软链接路径: ${prefix}" >&2
                failed=1
            fi
        elif [[ -d "${prefix}" && ! -L "${prefix}" ]]; then
            _log "旧 OpenVPN 普通目录仍在，保留原目录: ${prefix}"
        else
            echo "原 OpenVPN 入口存在但事务未记录旧版本目标，拒绝猜测回滚: ${prefix}" >&2
            failed=1
        fi

        if [[ -f "${root}/new-prefix" ]]; then
            IFS= read -r new_prefix < "${root}/new-prefix" || true
            if [[ -n "${new_prefix}" && -d "${new_prefix}" && "${new_prefix}" != "${backup}" ]]; then
                : > "${new_prefix}/.nicevpn-failed" || {
                    echo "无法标记失败的 OpenVPN release: ${new_prefix}" >&2
                    failed=1
                }
            fi
        fi
    fi

    while IFS= read -r path; do
        if [[ -f "${root}/${idx}.protected" ]]; then
            # 历史安装可能把 current 设为实体目录；安装器不会将其替换为 symlink。
            :
        elif [[ -f "${root}/${idx}.missing" ]]; then
            if [[ -f "${path}" || -L "${path}" ]]; then
                rm -f -- "${path}" || failed=1
            elif [[ -e "${path}" ]]; then
                echo "回滚拒绝删除非文件路径: ${path}" >&2
                failed=1
            fi
        elif [[ -L "${root}/${idx}" ]]; then
            if [[ -f "${path}" && ! -L "${path}" ]]; then
                echo "回滚拒绝用 symlink 覆盖普通文件: ${path}" >&2
                failed=1
            else
                nicevpn_atomic_symlink "$(readlink -- "${root}/${idx}")" "${path}" || failed=1
            fi
        elif [[ -f "${root}/${idx}" ]]; then
            mode="$(stat -c %a -- "${root}/${idx}")" || { failed=1; idx=$((idx+1)); continue; }
            nicevpn_atomic_write_command "${path}" "${mode}" cat "${root}/${idx}" || failed=1
        else
            echo "安装快照缺失: ${root}/${idx}" >&2
            failed=1
        fi
        idx=$((idx+1))
    done < "${root}/paths"

    if [[ -f "${root}/systemd-required" ]]; then
        systemctl daemon-reload || failed=1
    fi
    if [[ -f "${root}/active-instances" ]] && command -v systemctl >/dev/null 2>&1; then
        local -a saved_active_instances=()
        while IFS= read -r path; do
            [[ -n "${path}" ]] || continue
            case "${path}" in
                openvpn-server@*.service|openvpn-client@*.service|openvpn@*.service|openvpn-client.service)
                    saved_active_instances+=("${path}")
                    ;;
                *)
                    echo "安装事务中的活动服务名称无效，拒绝执行: ${path}" >&2
                    failed=1
                    ;;
            esac
        done < "${root}/active-instances"
        if ! openvpn_restart_and_validate_units "${saved_active_instances[@]}"; then
            echo "恢复原 OpenVPN 实例运行状态失败" >&2
            failed=1
        fi
    fi
    if [[ "${failed}" != "0" ]]; then
        echo "OpenVPN 安装回滚不完整，已保留事务快照: ${root}" >&2
        return 1
    fi
    if ! rm -rf -- "${root}"; then
        echo "OpenVPN 已恢复，但清理事务快照失败: ${root}" >&2
        return 1
    fi
    _log "已恢复 OpenVPN 稳定入口、systemd 配置和安装模式记录"
}

openvpn_source_transaction_on_exit() {
    local status="${1:-1}"
    trap - EXIT
    # 构建阶段的未发布 stage 也应立即清理；SIGKILL 留下的由下一次预检清理。
    if [[ -n "${OPENVPN_BUILD_STAGE_ROOT:-}" && -d "${OPENVPN_BUILD_STAGE_ROOT}" ]]; then
        rm -rf -- "${OPENVPN_BUILD_STAGE_ROOT}" || true
    fi
    # 正常路径由 commit 解除 trap；未提交的事务即使 exit 0 也不算成功。
    if ! openvpn_restore_source_transaction; then
        echo "严重：OpenVPN 安装失败且自动回滚不完整，请检查事务快照" >&2
    fi
    [[ "${status}" != "0" ]] || status=1
    exit "${status}"
}

openvpn_begin_source_transaction() {
    local mode="${1:-source}" root path idx=0
    openvpn_restore_source_transaction || return 1
    if [[ "${mode}" == "source" ]]; then
        openvpn_recover_source_install || return 1
    elif [[ "${mode}" != "package" ]]; then
        echo "未知 OpenVPN 安装事务类型: ${mode}" >&2
        return 1
    fi
    root="$(openvpn_source_transaction_root)" || return 1
    # 系统包模式可能根本没有自编译 prefix，先保证快照所在父目录存在。
    mkdir -p -- "$(dirname -- "${root}")" || return 1
    mkdir -m 0700 -- "${root}" || return 1
    openvpn_source_transaction_paths > "${root}/paths" || return 1
    while IFS= read -r path; do
        if [[ -L "${path}" || -f "${path}" ]]; then
            cp -a -- "${path}" "${root}/${idx}" || return 1
        elif [[ "${path}" == "${EASYRSA_INSTALL_DIR}/current" && -d "${path}" ]]; then
            # 历史安装可能把 current 设为实体目录；不会被原子 symlink 改写。
            : > "${root}/${idx}.protected"
        elif [[ -e "${path}" ]]; then
            echo "系统路径不是普通文件或 symlink，拒绝备份: ${path}" >&2
            return 1
        else
            : > "${root}/${idx}.missing"
        fi
        idx=$((idx+1))
    done < <(openvpn_source_transaction_paths)
    if [[ -e "${OPENVPN_PREFIX%/}" || -L "${OPENVPN_PREFIX%/}" ]]; then
        : > "${root}/original-prefix"
    fi
    if [[ "${INSTALL_SERVER_SYSTEMD}" == "1" || "${INSTALL_CLIENT_SYSTEMD}" == "1" || "${INSTALL_SERVER_OVERRIDE}" == "1" ]]; then
        : > "${root}/systemd-required"
    fi
    if (declare -p OPENVPN_ACTIVE_INSTANCES >/dev/null 2>&1); then
        printf '%s\n' "${OPENVPN_ACTIVE_INSTANCES[@]}" > "${root}/active-instances"
    else
        : > "${root}/active-instances"
    fi
    : > "${root}/ready"
    trap 'openvpn_source_transaction_on_exit "$?"' EXIT
}

openvpn_commit_source_transaction() {
    local root
    root="$(openvpn_source_transaction_root)" || return 1
    rm -rf -- "${root}" || return 1
    OPENVPN_BUILD_STAGE_ROOT=""
    trap - EXIT
}

openvpn_write_install_mode() {
    local mode="${1:?必须指定安装模式}"
    # 仅中心服务端安装写入本机标记；对端 client 安装不应改写中心 NiceVPN 状态。
    if [[ "${INSTALL_SERVER_SYSTEMD}" != "1" && "${INSTALL_SERVER_OVERRIDE}" != "1" ]]; then
        return 0
    fi
    mkdir -p -- "$(dirname -- "${OPENVPN_INSTALL_MODE_FILE}")"
    printf '%s\n' "${mode}" | nicevpn_atomic_write_stdin "${OPENVPN_INSTALL_MODE_FILE}" 0644
}

openvpn_run_source_install() {
    if [[ "${OPENVPN_STAGE_ONLY}" == "1" ]]; then
        # 升级候选只编译到临时目录，不迁移当前 prefix、不改 systemd/CLI/EasyRSA。
        trap 'if [[ -n "${OPENVPN_BUILD_STAGE_ROOT:-}" && -d "${OPENVPN_BUILD_STAGE_ROOT}" ]]; then rm -rf -- "${OPENVPN_BUILD_STAGE_ROOT}"; fi' EXIT
        openvpn_compile_install
        trap - EXIT
        return 0
    fi

    OPENVPN_INSTALL_MODE=source
    openvpn_begin_source_transaction
    openvpn_compile_install
    openvpn_post_compile_setup
    _openvpn_post_install_validate_impl
    openvpn_write_install_mode source
    openvpn_commit_source_transaction
}

# 系统包的安装与回退由包管理器负责；NiceVPN 只回滚自己修改的
# systemd override / EasyRSA 链接 / 安装模式标记。
openvpn_run_package_postflight() {
    local use_easyrsa_script="${1:-0}"
    OPENVPN_INSTALL_MODE=package
    openvpn_begin_source_transaction package
    openvpn_install_server_systemd_override
    if [[ "${use_easyrsa_script}" == "1" && "${INSTALL_EASYRSA}" == "1" ]]; then
        easyrsa_download_and_install
    fi
    _openvpn_post_install_validate_impl
    openvpn_write_install_mode package
    openvpn_commit_source_transaction
}

openvpn_compile_install() {
    local stage_parent stage_root staged_prefix staged_bin
    local version_output version expected_version release_id release_dir
    local existing_version old_target="" txn_root

    OPENVPN_ROOT_DIR="${OPENVPN_ROOT_DIR%/}"
    OPENVPN_PREFIX="${OPENVPN_PREFIX%/}"
    case "${OPENVPN_ROOT_DIR}" in
        /*) ;;
        *) echo "OPENVPN_ROOT_DIR 必须是绝对路径: ${OPENVPN_ROOT_DIR}" >&2; return 1 ;;
    esac
    [[ "${OPENVPN_PREFIX}" == "${OPENVPN_ROOT_DIR}/current" ]] || {
        echo "源码安装稳定入口必须为 ${OPENVPN_ROOT_DIR}/current: ${OPENVPN_PREFIX}" >&2
        return 1
    }
    case "${OPENVPN_BIN}" in
        "${OPENVPN_PREFIX}/"*) ;;
        *) echo "OPENVPN_BIN 必须位于 OPENVPN_PREFIX 内: ${OPENVPN_BIN}" >&2; return 1 ;;
    esac

    expected_version="${OPENVPN_TAG#v}"

    if [[ "${OPENVPN_STAGE_ONLY}" == "1" ]]; then
        [[ "${OPENVPN_STAGE_DIR}" == /* && "${OPENVPN_STAGE_DIR}" != "/" ]] || {
            echo "OPENVPN_STAGE_DIR 必须是非根绝对路径" >&2
            return 1
        }
        if [[ -e "${OPENVPN_STAGE_DIR}" || -L "${OPENVPN_STAGE_DIR}" ]]; then
            echo "OpenVPN staging 目标已存在，拒绝覆盖: ${OPENVPN_STAGE_DIR}" >&2
            return 1
        fi
        stage_parent="$(dirname -- "${OPENVPN_STAGE_DIR}")"
        mkdir -p -- "${stage_parent}"
        stage_root="$(mktemp -d "${stage_parent}/.nicevpn-openvpn-stage.XXXXXX")"
    else
        openvpn_recover_source_install || return 1
        mkdir -p -- "${OPENVPN_ROOT_DIR}"
        stage_root="$(mktemp -d "${OPENVPN_ROOT_DIR}/.stage.XXXXXX")"
    fi

    OPENVPN_BUILD_STAGE_ROOT="${stage_root}"
    staged_prefix="${stage_root}${OPENVPN_PREFIX}"
    staged_bin="${staged_prefix}/sbin/openvpn"

    openvpn_download_source
    rm -rf -- "${OPENVPN_SRC_ROOT}"
    mkdir -p -- "${OPENVPN_SRC_ROOT}"
    tar -xzf "${OPENVPN_CACHE}" -C "${OPENVPN_SRC_ROOT}"
    if [ ! -d "${OPENVPN_SRC_DIR}" ]; then
        OPENVPN_SRC_DIR="$(find "${OPENVPN_SRC_ROOT}" -mindepth 1 -maxdepth 1 -type d -print -quit)"
    fi
    [ -n "${OPENVPN_SRC_DIR}" ] && [ -d "${OPENVPN_SRC_DIR}" ] || {
        echo "无法识别 OpenVPN 源码目录: ${OPENVPN_SRC_ROOT}" >&2
        rm -rf -- "${stage_root}"
        OPENVPN_BUILD_STAGE_ROOT=""
        return 1
    }

    cd "${OPENVPN_SRC_DIR}"
    openvpn_configure_with_optional_features
    make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
    # 编译 prefix 固定为稳定入口 current，DESTDIR 只用于隔离 staging。
    make install DESTDIR="${stage_root}"

    [ -f "${staged_bin}" ] && [ -x "${staged_bin}" ] || {
        echo "OpenVPN staged binary 不存在或不可执行: ${staged_bin}" >&2
        rm -rf -- "${stage_root}"
        OPENVPN_BUILD_STAGE_ROOT=""
        return 1
    }
    version_output="$("${staged_bin}" --version 2>&1)" || {
        echo "OpenVPN staged binary 无法执行 --version: ${staged_bin}" >&2
        rm -rf -- "${stage_root}"
        OPENVPN_BUILD_STAGE_ROOT=""
        return 1
    }
    version="$(printf '%s\n' "${version_output}" | sed -n '1{s/.*OpenVPN[[:space:]]\+\([0-9][0-9.]*\).*/\1/p;}')"
    if [[ -z "${version}" || "${version}" != "${expected_version}" ]]; then
        echo "OpenVPN staged 版本校验失败：期望 ${expected_version}，实际 ${version:-unknown}" >&2
        rm -rf -- "${stage_root}"
        OPENVPN_BUILD_STAGE_ROOT=""
        return 1
    fi

    if [[ "${OPENVPN_STAGE_ONLY}" == "1" ]]; then
        mv -T -- "${staged_prefix}" "${OPENVPN_STAGE_DIR}" || {
            echo "发布 OpenVPN staging 目录失败: ${OPENVPN_STAGE_DIR}" >&2
            rm -rf -- "${stage_root}"
            OPENVPN_BUILD_STAGE_ROOT=""
            return 1
        }
        rm -rf -- "${stage_root}"
        OPENVPN_BUILD_STAGE_ROOT=""
        _log "OpenVPN ${version} 候选构建完成，隔离目录: ${OPENVPN_STAGE_DIR}"
        return 0
    fi

    release_id="${version}"
    release_dir="${OPENVPN_ROOT_DIR}/${release_id}"
    if [[ -e "${release_dir}" || -L "${release_dir}" ]]; then
        [[ -d "${release_dir}" && ! -L "${release_dir}" ]] || {
            echo "OpenVPN 版本目录不是普通目录，拒绝覆盖: ${release_dir}" >&2
            return 1
        }
        [[ -x "${release_dir}/sbin/openvpn" ]] || {
            echo "已有 OpenVPN 版本目录缺少可执行 binary: ${release_dir}" >&2
            return 1
        }
        existing_version="$("${release_dir}/sbin/openvpn" --version 2>&1 | sed -n '1{s/.*OpenVPN[[:space:]]\+\([0-9][0-9.]*\).*/\1/p;}')"
        [[ "${existing_version}" == "${version}" ]] || {
            echo "已有 OpenVPN 版本目录版本不匹配: ${release_dir} (${existing_version:-unknown})" >&2
            return 1
        }
        rm -f -- "${release_dir}/.nicevpn-failed"
        _log "复用已校验的 OpenVPN 版本目录: ${release_dir}"
    else
        mv -T -- "${staged_prefix}" "${release_dir}" || {
            echo "发布 OpenVPN 版本目录失败: ${release_dir}" >&2
            return 1
        }
    fi

    if [[ -L "${OPENVPN_PREFIX}" ]]; then
        old_target="$(readlink -f -- "${OPENVPN_PREFIX}" 2>/dev/null || true)"
        [[ -n "${old_target}" && -d "${old_target}" ]] || {
            echo "无法解析当前 OpenVPN 版本入口: ${OPENVPN_PREFIX}" >&2
            return 1
        }
    elif [[ -e "${OPENVPN_PREFIX}" ]]; then
        echo "OpenVPN current 已存在但不是软链接，拒绝覆盖: ${OPENVPN_PREFIX}" >&2
        return 1
    fi

    txn_root="$(openvpn_source_transaction_root)"
    if [[ -f "${txn_root}/ready" ]]; then
        if [[ -n "${old_target}" && ! -f "${txn_root}/backup-prefix" ]]; then
            printf '%s\n' "${old_target}" > "${txn_root}/backup-prefix"
        fi
        printf '%s\n' "${release_dir}" > "${txn_root}/new-prefix"
        : > "${txn_root}/switch-intent"
    fi

    nicevpn_atomic_symlink "${release_id}" "${OPENVPN_PREFIX}"
    OPENVPN_LAST_BACKUP_PREFIX="${old_target}"

    rm -rf -- "${stage_root}"
    OPENVPN_BUILD_STAGE_ROOT=""

    if [[ "${OPENVPN_CREATE_SYMLINK}" == "1" ]]; then
        if ! nicevpn_atomic_symlink "${OPENVPN_BIN}" /usr/local/sbin/openvpn; then
            _log "警告：无法安全更新 /usr/local/sbin/openvpn，保留现状"
        fi
    fi
    if [[ -n "${old_target}" ]]; then
        _log "OpenVPN ${version} 已切换到 ${release_dir}；旧版本保留在 ${old_target}"
    else
        _log "OpenVPN ${version} 已安装到 ${release_dir}"
    fi
}

_parse_openvpn_version_parts() {
    local raw="${1#v}"
    local major minor
    major="${raw%%.*}"
    raw="${raw#*.}"
    minor="${raw%%.*}"
    if [[ "${major}" =~ ^[0-9]+$ && "${minor}" =~ ^[0-9]+$ ]]; then
        echo "${major} ${minor}"
    else
        echo "2 6"
    fi
}

openvpn_install_client_systemd_from_src() {
    [ "${INSTALL_CLIENT_SYSTEMD}" = "1" ] || return 0
    local parts major minor template unit_path
    template="${OPENVPN_SRC_DIR}/distro/systemd/openvpn-client@.service.in"
    if [ ! -f "${template}" ]; then
        _log "源码无 client systemd 模板，跳过"
        return 0
    fi
    parts="$(_parse_openvpn_version_parts "${OPENVPN_TAG}")"
    major="${parts%% *}"
    minor="${parts#* }"
    unit_path="/etc/systemd/system/openvpn-client@.service"
    nicevpn_atomic_write_command "${unit_path}" 0644 sed \
        -e "s#@sbindir@#${OPENVPN_SBIN_DIR}#g" \
        -e "s#@OPENVPN_VERSION_MAJOR@#${major}#g" \
        -e "s#@OPENVPN_VERSION_MINOR@#${minor}#g" \
        "${template}"
    systemctl daemon-reload
    _log "已原子安装 openvpn-client@.service"
}

openvpn_install_server_systemd_from_src() {
    [ "${INSTALL_SERVER_SYSTEMD}" = "1" ] || return 0
    local parts major minor template unit_path exec_old exec_new
    template="${OPENVPN_SRC_DIR}/distro/systemd/openvpn-server@.service.in"
    if [ ! -f "${template}" ]; then
        echo "OpenVPN 源码缺少 server systemd 模板: ${template}" >&2
        return 1
    fi
    parts="$(_parse_openvpn_version_parts "${OPENVPN_TAG}")"
    major="${parts%% *}"
    minor="${parts#* }"
    unit_path="/etc/systemd/system/openvpn-server@.service"
    exec_old="ExecStart=${OPENVPN_SBIN_DIR}/openvpn --status %t/openvpn-server/status-%i.log --status-version 2 --suppress-timestamps --config %i.conf"
    exec_new="ExecStart=${OPENVPN_BIN} --suppress-timestamps --config %i.conf"
    nicevpn_atomic_write_command "${unit_path}" 0644 sed \
        -e "s#@sbindir@#${OPENVPN_SBIN_DIR}#g" \
        -e "s#@OPENVPN_VERSION_MAJOR@#${major}#g" \
        -e "s#@OPENVPN_VERSION_MINOR@#${minor}#g" \
        -e "s#${exec_old}#${exec_new}#" \
        -e 's/ProtectSystem=true/ProtectSystem=true\nReadWritePaths=\/etc\/openvpn/' \
        "${template}"
    systemctl daemon-reload
    _log "已原子安装 openvpn-server@.service"
}

openvpn_install_server_systemd_override() {
    [ "${INSTALL_SERVER_OVERRIDE}" = "1" ] || return 0
    local override_dir="/etc/systemd/system/openvpn-server@.service.d"
    nicevpn_atomic_write_stdin "${override_dir}/10-ovpn-mgmt.conf" 0644 <<'EOF'
[Service]
ReadWritePaths=/etc/openvpn
EOF
    systemctl daemon-reload
    _log "已原子安装 openvpn-server systemd override"
}

easyrsa_binary_version() {
    local binary="${1:?必须指定 EasyRSA 可执行脚本}"
    local output
    output="$("${binary}" --version 2>&1)" || return 1
    printf '%s\n' "${output}" | grep -Eo '[0-9]+(\.[0-9]+){1,2}' | sed -n '1p'
}

easyrsa_validate_binary() {
    local binary="${1:?必须指定 EasyRSA 可执行脚本}"
    local expected_version="${2:-}"
    local actual_version
    [ -f "${binary}" ] && [ -x "${binary}" ] || {
        echo "EasyRSA 脚本不存在或不可执行: ${binary}" >&2
        return 1
    }
    actual_version="$(easyrsa_binary_version "${binary}" || true)"
    [ -n "${actual_version}" ] || {
        echo "无法读取 EasyRSA 版本: ${binary}" >&2
        return 1
    }
    if [ -n "${expected_version}" ] && [ "${actual_version}" != "${expected_version}" ]; then
        echo "EasyRSA 版本不匹配：期望 ${expected_version}，实际 ${actual_version}" >&2
        return 1
    fi
    printf '%s\n' "${actual_version}"
}

# EasyRSA 同样采用 /opt/ezrsa/<version> + current -> <version>。
# 版本目录只按版本号命名；已存在时校验版本并复用，不覆盖。
easyrsa_download_and_install() (
    easyrsa_acquire_install_lock
    easyrsa_reuse_existing
    [ "${INSTALL_EASYRSA}" = "1" ] || exit 0
    : "${EASYRSA_DOWNLOAD_URLS:?必须设置 EASYRSA_DOWNLOAD_URLS}"

    mkdir -p "${EASYRSA_INSTALL_DIR}"

    find "${EASYRSA_INSTALL_DIR}" -mindepth 1 -maxdepth 1 \
        -type d -name '.*.stage.*' -exec rm -rf -- {} + 2>/dev/null || true
    find "${EASYRSA_INSTALL_DIR}" -mindepth 1 -maxdepth 1 \
        -type d -name '.extract.*' -exec rm -rf -- {} + 2>/dev/null || true

    if [ -s "${EASYRSA_CACHE}" ] && ! tar -tzf "${EASYRSA_CACHE}" >/dev/null 2>&1; then
        _log "EasyRSA 下载缓存校验失败，删除后重新下载: ${EASYRSA_CACHE}"
        rm -f -- "${EASYRSA_CACHE}"
    fi

    if [ ! -s "${EASYRSA_CACHE}" ]; then
        local ok=0 url
        rm -f -- "${EASYRSA_CACHE}.part"
        while IFS= read -r url; do
            [ -n "${url}" ] || continue
            _log "下载 Easy-RSA: ${url}"
            if curl -fL --retry 3 --retry-delay 2 "${url}" -o "${EASYRSA_CACHE}.part" \
                && tar -tzf "${EASYRSA_CACHE}.part" >/dev/null; then
                if mv -f -- "${EASYRSA_CACHE}.part" "${EASYRSA_CACHE}"; then
                    ok=1
                    break
                fi
            fi
            rm -f -- "${EASYRSA_CACHE}.part"
        done <<EOF
${EASYRSA_DOWNLOAD_URLS}
EOF
        [ "${ok}" = "1" ] || { echo "下载 Easy-RSA 失败" >&2; exit 1; }
    else
        _log "复用已校验的 Easy-RSA 缓存: ${EASYRSA_CACHE}"
    fi

    local tmp_dir stage_dir source_dir expected_version actual_version
    local release_id install_dir current_link old_current
    tmp_dir="$(mktemp -d "${EASYRSA_INSTALL_DIR}/.extract.XXXXXX")"
    stage_dir=""
    cleanup_easyrsa_install() {
        rm -rf -- "${tmp_dir}"
        if [ -n "${stage_dir}" ]; then
            rm -rf -- "${stage_dir}"
        fi
    }
    trap cleanup_easyrsa_install EXIT

    tar -xzf "${EASYRSA_CACHE}" -C "${tmp_dir}"
    source_dir="$(find "${tmp_dir}" -mindepth 1 -maxdepth 2 -type d -name easyrsa3 -print -quit)"
    if [ -z "${source_dir}" ]; then
        source_dir="$(find "${tmp_dir}" -mindepth 1 -maxdepth 1 -type d -print -quit)"
    fi
    [ -n "${source_dir}" ] && [ -f "${source_dir}/easyrsa" ] || {
        echo "无法识别 EasyRSA 解压目录或缺少 easyrsa 脚本: ${tmp_dir}" >&2
        exit 1
    }

    expected_version="${EASYRSA_TAG#v}"
    chmod +x "${source_dir}/easyrsa"
    actual_version="$(easyrsa_validate_binary "${source_dir}/easyrsa" "${expected_version}")"
    release_id="${actual_version}"
    install_dir="${EASYRSA_INSTALL_DIR}/${release_id}"
    current_link="${EASYRSA_INSTALL_DIR}/current"

    if [ -e "${install_dir}" ] || [ -L "${install_dir}" ]; then
        [ -d "${install_dir}" ] && [ ! -L "${install_dir}" ] || {
            echo "EasyRSA 版本目录不是普通目录，拒绝覆盖: ${install_dir}" >&2
            exit 1
        }
        local existing_version
        existing_version="$(easyrsa_validate_binary "${install_dir}/easyrsa" "${expected_version}")"
        _log "复用已校验的 EasyRSA 版本目录: ${install_dir} (${existing_version})"
    else
        stage_dir="$(mktemp -d "${EASYRSA_INSTALL_DIR}/.${release_id}.stage.XXXXXX")"
        cp -a "${source_dir}/." "${stage_dir}/"
        chmod +x "${stage_dir}/easyrsa"
        easyrsa_validate_binary "${stage_dir}/easyrsa" "${expected_version}" >/dev/null
        mv -T -- "${stage_dir}" "${install_dir}"
        stage_dir=""
    fi

    old_current="$(readlink "${current_link}" 2>/dev/null || true)"
    nicevpn_atomic_symlink "${release_id}" "${current_link}"
    if ! easyrsa_validate_binary "${current_link}/easyrsa" "${expected_version}" >/dev/null; then
        echo "EasyRSA current 切换后版本校验失败，恢复旧版本入口" >&2
        if [ -n "${old_current}" ]; then
            nicevpn_atomic_symlink "${old_current}" "${current_link}" || {
                echo "严重：EasyRSA current 版本校验失败且旧入口恢复失败: ${current_link}" >&2
                exit 1
            }
        else
            rm -f -- "${current_link}"
        fi
        exit 1
    fi
    if ! nicevpn_atomic_symlink "${current_link}/easyrsa" /usr/local/bin/easyrsa; then
        _log "警告：无法更新 /usr/local/bin/easyrsa；NiceVPN 将优先使用 ${current_link}/easyrsa"
    fi

    _log "EasyRSA 已切换到 ${install_dir}，版本=${actual_version}，旧 current=${old_current:-无}"
)

openvpn_post_compile_setup() {
    openvpn_install_server_systemd_from_src || return 1
    openvpn_install_client_systemd_from_src || return 1
    easyrsa_download_and_install || return 1
    openvpn_install_server_systemd_override || return 1
}

OPENVPN_ACTIVE_INSTANCES=()
OPENVPN_LAST_BACKUP_PREFIX=""

# 回滚只切换稳定软链接；新旧版本目录均保留，绝不移动或删除 release。
openvpn_rollback_previous_prefix() {
    local reason="${1:-安装后校验失败}"
    local backup_prefix="${OPENVPN_LAST_BACKUP_PREFIX:-}"
    [ -n "${backup_prefix}" ] && [ -d "${backup_prefix}" ] || return 0

    if ! nicevpn_atomic_symlink "$(basename -- "${backup_prefix}")" "${OPENVPN_PREFIX}"; then
        _log "旧 OpenVPN 版本回滚失败，旧版本仍保留在 ${backup_prefix}"
        return 1
    fi
    OPENVPN_LAST_BACKUP_PREFIX=""
    _log "${reason}，已将稳定入口恢复到旧版本: ${backup_prefix}"
    return 0
}

openvpn_acquire_install_lock() {
    nicevpn_ensure_commands flock util-linux
    mkdir -p /run/lock
    exec 9>/run/lock/nicevpn-install.lock
    if ! flock -n 9; then
        echo "已有 NiceVPN/OpenVPN 安装任务正在执行，请稍后重试" >&2
        return 1
    fi
}

openvpn_capture_active_instances() {
    OPENVPN_ACTIVE_INSTANCES=()
    if [ "${INSTALL_SERVER_SYSTEMD}" != "1" ] && \
        [ "${INSTALL_CLIENT_SYSTEMD}" != "1" ] && \
        [ "${INSTALL_SERVER_OVERRIDE}" != "1" ]; then
        return 0
    fi
    if ! command -v systemctl >/dev/null 2>&1; then
        return 0
    fi
    local output unit
    output="$(systemctl list-units --type=service --state=active --no-legend --no-pager 2>/dev/null || true)"
    while IFS= read -r unit; do
        case "${unit}" in
            openvpn-server@*.service|openvpn-client@*.service|openvpn@*.service|openvpn-client.service)
                OPENVPN_ACTIVE_INSTANCES+=("${unit}")
                ;;
        esac
    done < <(awk 'NF {print $1}' <<< "${output}")
}

# 对已经运行的实例执行 restart，并验证 systemd 最终状态。
# 逐个处理且不中断剩余实例，便于失败时完整报告并触发事务回滚。
openvpn_restart_and_validate_units() {
    local binary="" unit failed=0 pid running_inode installed_inode
    if [ "$#" -gt 0 ] && [[ "$1" == /* || "$1" == ./* ]]; then
        binary="$1"
        shift
    fi
    [ "$#" -gt 0 ] || return 0
    command -v systemctl >/dev/null 2>&1 || {
        echo "存在运行中的 OpenVPN 实例，但未找到 systemctl" >&2
        return 1
    }
    for unit in "$@"; do
        case "${unit}" in
            openvpn-server@*.service|openvpn-client@*.service|openvpn@*.service|openvpn-client.service) ;;
            *)
                echo "拒绝操作未知的 OpenVPN systemd unit: ${unit}" >&2
                failed=1
                continue
                ;;
        esac

        # 包管理器可能已经自动重启服务；比较进程 inode，避免无意义的二次断连。
        if [ -n "${binary}" ]; then
            pid="$(systemctl show --property=MainPID --value "${unit}" 2>/dev/null || true)"
            if [[ "${pid}" =~ ^[1-9][0-9]*$ ]]; then
                running_inode="$(stat -Lc '%d:%i' "/proc/${pid}/exe" 2>/dev/null || true)"
                installed_inode="$(stat -Lc '%d:%i' "${binary}" 2>/dev/null || true)"
                if [ -n "${running_inode}" ] && [ "${running_inode}" = "${installed_inode}" ]; then
                    _log "OpenVPN 实例已运行当前 binary，无需重复重启: ${unit}"
                    if ! systemctl is-active --quiet "${unit}"; then
                        echo "OpenVPN 实例状态异常: ${unit}" >&2
                        failed=1
                    fi
                    continue
                fi
            fi
        fi

        if ! systemctl restart "${unit}"; then
            echo "重启 OpenVPN 实例失败: ${unit}" >&2
            failed=1
            continue
        fi
        if ! systemctl is-active --quiet "${unit}"; then
            echo "OpenVPN 实例重启后未处于 active 状态: ${unit}" >&2
            failed=1
        else
            _log "OpenVPN 实例运行状态校验通过: ${unit}"
        fi
    done
    [ "${failed}" = "0" ]
}

openvpn_validate_systemd_unit() {
    local unit="${1:?必须指定 systemd unit}"
    local output unit_state fragment_path
    command -v systemctl >/dev/null 2>&1 || {
        echo "未找到 systemctl，无法验证服务单元: ${unit}" >&2
        return 1
    }
    output="$(systemctl list-unit-files --type=service --no-legend --no-pager 2>/dev/null)" || {
        echo "systemctl 无法读取服务单元列表: ${unit}" >&2
        return 1
    }
    unit_state="$(awk -v unit="${unit}" '$1 == unit { print $2; exit }' <<< "${output}")"
    if [ -z "${unit_state}" ] || [ "${unit_state}" = "masked" ]; then
        echo "systemd 服务单元不存在或已屏蔽: ${unit} state=${unit_state:-not-found}" >&2
        return 1
    fi

    # systemd-analyze 检查 unit 与其 drop-in 的语法和依赖；精简系统没有该命令时，
    # 仍保留 systemctl 的基础校验。
    if command -v systemd-analyze >/dev/null 2>&1; then
        fragment_path="/etc/systemd/system/${unit}"
        if [ ! -f "${fragment_path}" ]; then
            fragment_path="$(systemctl show --property=FragmentPath --value "${unit}" 2>/dev/null || true)"
        fi
        if [ -n "${fragment_path}" ] && [ -f "${fragment_path}" ]; then
            systemd-analyze verify "${fragment_path}" || {
                echo "systemd unit 语法/依赖校验失败: ${unit} (${fragment_path})" >&2
                return 1
            }
        fi
    fi
    return 0
}
_openvpn_post_install_validate_impl() {
    local candidate="" binary="" version_output="" version="" expected_version=""
    if [[ "${OPENVPN_INSTALL_MODE:-}" == "package" ]]; then
        # 系统包安装必须验证包管理器路径，不能被历史 /opt/openvpn 源码目录抢先命中。
        for candidate in /usr/sbin/openvpn /usr/bin/openvpn; do
            if [ -f "${candidate}" ] && [ -x "${candidate}" ]; then
                binary="${candidate}"
                break
            fi
        done
    else
        for candidate in "${OPENVPN_BIN}" "${OPENVPN_PREFIX}/bin/openvpn"; do
            if [ -f "${candidate}" ] && [ -x "${candidate}" ]; then
                binary="${candidate}"
                break
            fi
        done
    fi
    if [ -z "${binary}" ] && [ "${OPENVPN_INSTALL_MODE:-}" = "package" ]; then
        candidate="$(command -v openvpn 2>/dev/null || true)"
        case "${candidate}" in
            /usr/sbin/openvpn|/usr/bin/openvpn)
                if [ -f "${candidate}" ] && [ -x "${candidate}" ]; then
                    binary="${candidate}"
                fi
                ;;
        esac
    fi
    [ -n "${binary}" ] || {
        echo "OpenVPN 安装后未找到与安装模式匹配的可执行二进制" >&2
        return 1
    }

    version_output="$("${binary}" --version 2>&1)" || {
        echo "OpenVPN 二进制无法执行 --version: ${binary}" >&2
        return 1
    }
    version="$(printf '%s\n' "${version_output}" | sed -n '1{s/.*OpenVPN[[:space:]]\+\([0-9][0-9.]*\).*/\1/p;}')"
    [ -n "${version}" ] || {
        echo "无法识别 OpenVPN 二进制版本: ${binary}" >&2
        return 1
    }
    version_ge "${version}" "${OPENVPN_MIN_VERSION}" || {
        echo "OpenVPN 安装后版本不满足要求：${version} < ${OPENVPN_MIN_VERSION}，binary=${binary}" >&2
        return 1
    }
    if [ "${OPENVPN_INSTALL_MODE:-}" = "source" ]; then
        expected_version="${OPENVPN_TAG#v}"
        [ "${version}" = "${expected_version}" ] || {
            echo "OpenVPN 源码安装版本不匹配：期望 ${expected_version}，实际 ${version}" >&2
            return 1
        }
    fi

    if [ "${INSTALL_SERVER_SYSTEMD}" = "1" ] || \
        [ "${INSTALL_CLIENT_SYSTEMD}" = "1" ] || \
        [ "${INSTALL_SERVER_OVERRIDE}" = "1" ]; then
        systemctl daemon-reload || {
            echo "systemd daemon-reload 失败，OpenVPN 安装后校验中止" >&2
            return 1
        }
    fi
    if [ "${INSTALL_SERVER_SYSTEMD}" = "1" ]; then
        openvpn_validate_systemd_unit "openvpn-server@.service" || return 1
    fi
    if [ "${INSTALL_CLIENT_SYSTEMD}" = "1" ]; then
        openvpn_validate_systemd_unit "openvpn-client@.service" || return 1
    fi
    if [ "${INSTALL_SERVER_OVERRIDE}" = "1" ]; then
        local override_file="/etc/systemd/system/openvpn-server@.service.d/10-ovpn-mgmt.conf"
        if [ ! -f "${override_file}" ] || ! grep -Fq 'ReadWritePaths=/etc/openvpn' "${override_file}"; then
            echo "OpenVPN server systemd override 安装后不存在或内容不完整" >&2
            return 1
        fi
    fi

    # 若包管理器已自动重启，按 /proc/<pid>/exe inode 判断并跳过二次重启；
    # 源码切换后旧进程 inode 不同，则必须重启到新 release。
    openvpn_restart_and_validate_units "${binary}" "${OPENVPN_ACTIVE_INSTANCES[@]}" || return 1
}


openvpn_post_install_validate() {
    if _openvpn_post_install_validate_impl; then
        return 0
    fi
    openvpn_rollback_previous_prefix "OpenVPN 安装后校验失败" || true
    return 1
}
