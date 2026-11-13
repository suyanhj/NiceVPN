#!/usr/bin/env bash
# Debian/Ubuntu 系 OpenVPN 安装入口
#
# 必需环境变量:
#   DEBIAN_CODENAME  — apt 源代号（如 jammy、bookworm）
#
# 可选（见 openvpn-install-common.sh）:
#   OPENVPN_MIN_VERSION, OPENVPN_DOWNLOAD_URLS, INSTALL_EASYRSA, INSTALL_SERVER_SYSTEMD, ...
#
# 用法:
#   DEBIAN_CODENAME=jammy bash openvpn-install-debian.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=openvpn-install-common.sh
source "${SCRIPT_DIR}/openvpn-install-common.sh"

: "${APT_KEY_URL:=https://swupdate.openvpn.net/repos/repo-public.gpg}"
: "${APT_KEYRING:=/usr/share/keyrings/openvpn-archive-keyring.gpg}"
: "${DEBIAN_CODENAME:?必须设置 DEBIAN_CODENAME}"

export DEBIAN_FRONTEND=noninteractive

install_optional_dco_dependencies() {
    # DCO 依赖不是 OpenVPN 基础安装条件；老发行版可能没有足够新的 libnl-genl。
    if apt install -y libnl-3-dev libnl-genl-3-dev; then
        _log "已安装可选 DCO 开发依赖"
    else
        _log "警告：无法安装可选 libnl-genl 开发包，继续安装；编译前将检测并按需禁用 DCO"
    fi
}

install_source_dependencies() {
    local -a packages=(
        build-essential curl ca-certificates pkg-config autoconf automake libtool
        libssl-dev libpam0g-dev libcap-ng-dev
        liblzo2-dev liblz4-dev libpkcs11-helper1-dev libsystemd-dev
        conntrack iptables ipset
    )
    if [ "${INSTALL_SERVER_SYSTEMD}" = "1" ]; then
        packages+=(logrotate)
    fi
    # 基础依赖一次性安装；DCO 开发包可选，单独尝试且失败不阻断编译。
    apt install -y "${packages[@]}"
    install_optional_dco_dependencies
}

_log "Debian 系安装：codename=${DEBIAN_CODENAME} min_version=${OPENVPN_MIN_VERSION}"

if [ "${OPENVPN_FORCE_SOURCE}" = "1" ]; then
    _log "强制源码构建模式：跳过 OpenVPN 系统包安装"
    apt update -y
    install_source_dependencies
    openvpn_compile_install
    openvpn_post_compile_setup
    exit 0
fi

curl -fsSL "${APT_KEY_URL}" | gpg --dearmor -o "${APT_KEYRING}"
echo "deb [signed-by=${APT_KEYRING}] https://build.openvpn.net/debian/openvpn/release/${DEBIAN_CODENAME} ${DEBIAN_CODENAME} main" \
    > /etc/apt/sources.list.d/openvpn.list
apt update -y

PKG_VERSION="$(apt-cache policy openvpn 2>/dev/null \
    | awk '/Candidate:/ {print $2; exit}' \
    | sed 's/.*://' \
    | sed 's/-.*//')"

if [ -n "${PKG_VERSION}" ] && [ "${PKG_VERSION}" != "(none)" ] \
    && version_ge "${PKG_VERSION}" "${OPENVPN_MIN_VERSION}"; then
    _log "仓库 openvpn ${PKG_VERSION} 满足 ${OPENVPN_MIN_VERSION}+，使用 apt 安装"
    pkgs=(openvpn conntrack)
    if [ "${INSTALL_SERVER_SYSTEMD}" = "1" ]; then
        pkgs+=(logrotate)
    fi
    if [ "${INSTALL_EASYRSA}" = "1" ]; then
        pkgs=(openvpn easy-rsa conntrack)
    fi
    apt install -y "${pkgs[@]}"
    openvpn_install_server_systemd_override
    exit 0
fi

_log "仓库 openvpn 版本 ${PKG_VERSION:-unknown} 低于 ${OPENVPN_MIN_VERSION}，改为源码编译安装"
install_source_dependencies

openvpn_compile_install
openvpn_post_compile_setup
