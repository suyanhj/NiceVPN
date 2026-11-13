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

nicevpn_ensure_commands() {
    # 参数按「命令 包名」成对传入。任一命令缺失时，一次安装整组包。
    if (( $# < 2 || $# % 2 != 0 )); then
        echo "[linux-packages] 用法: nicevpn_ensure_commands command package [command package ...]" >&2
        return 2
    fi

    local -a commands=() packages=()
    local missing=0 binary package
    while (( $# )); do
        binary="$1"
        package="$2"
        shift 2
        commands+=("${binary}")
        packages+=("${package}")
        if ! command -v "${binary}" >/dev/null 2>&1; then
            missing=1
        fi
    done

    # 全部命令已存在时不调用包管理器；有缺项时一次补齐整组依赖。
    (( missing == 1 )) || return 0

    local manager
    manager="$(nicevpn_package_manager)" || {
        echo "[linux-packages] 不支持的发行版或找不到包管理器，缺少命令: ${commands[*]}" >&2
        return 1
    }
    if [[ "${manager}" == "apt-get" ]]; then
        apt-get update -qq
    fi
    nicevpn_install_packages "${manager}" "${packages[@]}" || return 1

    for binary in "${commands[@]}"; do
        if ! command -v "${binary}" >/dev/null 2>&1; then
            echo "[linux-packages] 安装后仍找不到 ${binary}" >&2
            return 1
        fi
    done
}

nicevpn_ensure_command() {
    local binary="${1:?必须指定待检查命令}"
    local package="${2:?必须指定对应软件包}"
    nicevpn_ensure_commands "${binary}" "${package}"
}
