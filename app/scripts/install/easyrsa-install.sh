#!/usr/bin/env bash
# EasyRSA 独立安装入口：不安装、不升级、不修改 OpenVPN。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=openvpn-install-common.sh
source "${SCRIPT_DIR}/openvpn-install-common.sh"
# shellcheck source=../common/linux-packages.sh
source "${SCRIPT_DIR}/../common/linux-packages.sh"

INSTALL_EASYRSA=1
export INSTALL_EASYRSA
easyrsa_reuse_existing

if [[ "${INSTALL_EASYRSA}" != "1" ]]; then
    # 已有 EasyRSA 只检查其运行时 OpenSSL，不重新下载、不覆盖版本目录。
    nicevpn_ensure_easyrsa_runtime_dependencies
    existing_bin="$(easyrsa_find_existing_binary)"
    _log "EasyRSA 已存在，复用: ${existing_bin}"
    exit 0
fi

# 仅在确实缺少 EasyRSA 或显式要求强制重装时，准备下载/解压工具。
nicevpn_ensure_easyrsa_install_dependencies
: "${EASYRSA_DOWNLOAD_URLS:?必须设置 EASYRSA_DOWNLOAD_URLS}"

easyrsa_download_and_install

installed_bin="$(easyrsa_find_existing_binary || true)"
if [[ -z "${installed_bin}" ]]; then
    installed_bin="${EASYRSA_INSTALL_DIR}/current/easyrsa"
fi
[[ -f "${installed_bin}" && -x "${installed_bin}" ]] || {
    echo "EasyRSA 安装后未找到可执行脚本: ${installed_bin}" >&2
    exit 1
}
_log "EasyRSA 已就绪: ${installed_bin}"
