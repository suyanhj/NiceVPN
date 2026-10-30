#!/usr/bin/env bash
# 安装已构建 OpenVPN 源码对应的 server systemd unit/override。
# 供安全升级在完成 binary 切换后复用；实际模板处理逻辑与正常安装共用 common.sh。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=openvpn-install-common.sh
source "${SCRIPT_DIR}/openvpn-install-common.sh"

: "${OPENVPN_SRC_DIR:?必须设置 OPENVPN_SRC_DIR}"
: "${OPENVPN_TAG:?必须设置 OPENVPN_TAG}"

INSTALL_SERVER_SYSTEMD=1
INSTALL_SERVER_OVERRIDE=1

openvpn_install_server_systemd_from_src
openvpn_install_server_systemd_override
