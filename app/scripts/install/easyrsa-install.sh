#!/usr/bin/env bash
# EasyRSA 独立安装入口：不安装、不升级、不修改 OpenVPN。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=openvpn-install-common.sh
source "${SCRIPT_DIR}/openvpn-install-common.sh"
# shellcheck source=../common/linux-packages.sh
source "${SCRIPT_DIR}/../common/linux-packages.sh"

: "${EASYRSA_DOWNLOAD_URLS:?必须设置 EASYRSA_DOWNLOAD_URLS}"

# EasyRSA 解压、安装所需命令通过共享函数批量检查；缺失时只调用一次包管理器。
nicevpn_ensure_commands \
    curl curl \
    tar tar \
    gzip gzip \
    openssl openssl \
    find findutils \
    cp coreutils \
    ln coreutils \
    mktemp coreutils \
    mkdir coreutils \
    rm coreutils \
    mv coreutils \
    head coreutils
INSTALL_EASYRSA=1
export INSTALL_EASYRSA
easyrsa_download_and_install

installed_bin="${EASYRSA_INSTALL_DIR}/current/easyrsa"
[ -f "${installed_bin}" ] || {
    echo "EasyRSA 安装后未找到脚本: ${installed_bin}" >&2
    exit 1
}
_log "EasyRSA 已就绪: ${installed_bin}"
