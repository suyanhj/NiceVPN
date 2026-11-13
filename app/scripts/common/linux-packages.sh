#!/usr/bin/env bash
# NiceVPN Linux 通用依赖管理函数。
# 可 source 到本机安装脚本，也可内联到 SSH 远端命令。
# 不在加载时执行任何安装或修改系统操作。

nicevpn_linux_family() {
    local release_file="${NICEVPN_OS_RELEASE_FILE:-/etc/os-release}"
    [[ -r "${release_file}" ]] || return 1

    local ID="" ID_LIKE=""
    # shellcheck source=/dev/null
    source "${release_file}"

    local token
    for token in "${ID:-}" ${ID_LIKE:-}; do
        case "${token,,}" in
            debian|ubuntu) printf 'debian\n'; return 0 ;;
            rhel|centos|rocky|almalinux|fedora) printf 'rhel\n'; return 0 ;;
        esac
    done
    return 1
}

nicevpn_package_manager() {
    local family
    family="$(nicevpn_linux_family)" || return 1
    case "${family}" in
        debian)
            command -v apt-get >/dev/null 2>&1 || return 1
            printf 'apt-get\n'
            ;;
        rhel)
            if command -v dnf >/dev/null 2>&1; then
                printf 'dnf\n'
            elif command -v yum >/dev/null 2>&1; then
                printf 'yum\n'
            else
                return 1
            fi
            ;;
        *) return 1 ;;
    esac
}

nicevpn_install_packages() {
    local manager="${1:?必须指定包管理器}"
    shift
    case "${manager}" in
        apt-get) DEBIAN_FRONTEND=noninteractive apt-get install -y "$@" ;;
        dnf|yum) "${manager}" install -y "$@" ;;
        *) echo "[linux-packages] 不支持的包管理器: ${manager}" >&2; return 1 ;;
    esac
}

nicevpn_ensure_command() {
    local binary="${1:?必须指定待检查命令}"
    shift
    command -v "${binary}" >/dev/null 2>&1 && return 0

    local manager
    manager="$(nicevpn_package_manager)" || {
        echo "[linux-packages] 不支持的发行版或找不到包管理器，无法安装 ${binary}" >&2
        return 1
    }
    if [[ "${manager}" == "apt-get" ]]; then
        apt-get update -qq
    fi
    nicevpn_install_packages "${manager}" "$@" || return 1
    if ! command -v "${binary}" >/dev/null 2>&1; then
        echo "[linux-packages] 安装后仍找不到 ${binary}" >&2
        return 1
    fi
}
