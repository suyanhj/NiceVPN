#!/usr/bin/env bash
# EasyRSA 独立安装入口：不安装、不升级、不修改 OpenVPN。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=openvpn-install-common.sh
source "${SCRIPT_DIR}/openvpn-install-common.sh"
# shellcheck source=../common/linux-packages.sh
source "${SCRIPT_DIR}/../common/linux-packages.sh"

: "${EASYRSA_DOWNLOAD_URLS:?必须设置 EASYRSA_DOWNLOAD_URLS}"

ensure_easyrsa_tools() {
    local missing=()
    local tool
    for tool in curl tar gzip openssl find cp ln mktemp; do
        if ! command -v "${tool}" >/dev/null 2>&1; then
            missing+=("${tool}")
        fi
    done

    [ "${#missing[@]}" -gt 0 ] || return 0

    _log "缺少 EasyRSA 安装工具: ${missing[*]}，尝试通过系统包管理器补齐"
    local manager
    manager="$(nicevpn_package_manager)" || {
        echo "无法自动补齐 EasyRSA 安装工具：发行版或包管理器不受支持" >&2
        return 1
    }
    if [ "${manager}" = "apt-get" ]; then
        apt-get update -qq
    fi
    nicevpn_install_packages "${manager}" curl tar gzip openssl findutils coreutils

    for tool in "${missing[@]}"; do
        command -v "${tool}" >/dev/null 2>&1 || {
            echo "EasyRSA 安装工具仍不可用: ${tool}" >&2
            return 1
        }
    done
}

ensure_easyrsa_tools
INSTALL_EASYRSA=1
export INSTALL_EASYRSA
easyrsa_download_and_install

installed_bin="${EASYRSA_INSTALL_DIR}/current/easyrsa"
[ -f "${installed_bin}" ] || {
    echo "EasyRSA 安装后未找到脚本: ${installed_bin}" >&2
    exit 1
}
_log "EasyRSA 已就绪: ${installed_bin}"
