#!/usr/bin/env bash
# RHEL/CentOS/Rocky 系 OpenVPN 安装入口
#
# 可选环境变量见 openvpn-install-common.sh
#
# 用法:
#   bash openvpn-install-rhel.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=openvpn-install-common.sh
source "${SCRIPT_DIR}/openvpn-install-common.sh"

if command -v dnf >/dev/null 2>&1; then
    PM=dnf
elif command -v yum >/dev/null 2>&1; then
    PM=yum
else
    echo "未找到 dnf/yum" >&2
    exit 1
fi

_log "RHEL 系安装：pm=${PM} min_version=${OPENVPN_MIN_VERSION}"

PKG_VERSION="$(${PM} -q info openvpn 2>/dev/null \
    | awk -F: '/^Version[[:space:]]*:/ {gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2}' \
    | sort -V | tail -n 1)"

if [ -n "${PKG_VERSION}" ] && version_ge "${PKG_VERSION}" "${OPENVPN_MIN_VERSION}"; then
    _log "仓库 openvpn ${PKG_VERSION} 满足 ${OPENVPN_MIN_VERSION}+，使用系统包安装"
    ${PM} install -y openvpn conntrack-tools
    openvpn_install_server_systemd_override
    if [ "${INSTALL_EASYRSA}" = "1" ]; then
        easyrsa_download_and_install
    fi
    exit 0
fi

_log "仓库 openvpn 版本 ${PKG_VERSION:-unknown} 低于 ${OPENVPN_MIN_VERSION}，改为源码编译安装"
${PM} install -y \
    gcc gcc-c++ make tar gzip curl ca-certificates pkgconfig autoconf automake libtool \
    openssl-devel libnl3-devel pam-devel libcap-ng-devel lzo-devel lz4-devel \
    pkcs11-helper-devel systemd-devel which conntrack-tools

openvpn_compile_install
openvpn_post_compile_setup
