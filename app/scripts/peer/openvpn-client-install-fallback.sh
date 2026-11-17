#!/usr/bin/env bash
# 对端无官方 openvpn-client@ 模板时，写入兜底 systemd unit
#
# 必需环境变量:
#   OPENVPN_BIN, CONFIG_PATH, UNIT_TEMPLATE（含 @OPENVPN_BIN@ / @CONFIG_PATH@ 占位符）
#
# 可选:
#   SERVICE_NAME — 默认 openvpn-client.service

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../common/atomic-files.sh
source "${SCRIPT_DIR}/../common/atomic-files.sh"

: "${OPENVPN_BIN:?必须设置 OPENVPN_BIN}"
: "${CONFIG_PATH:?必须设置 CONFIG_PATH}"
: "${UNIT_TEMPLATE:?必须设置 UNIT_TEMPLATE}"
: "${SERVICE_NAME:=openvpn-client.service}"

UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}"

printf '%s' "${UNIT_TEMPLATE}" \
    | sed -e "s#@OPENVPN_BIN@#${OPENVPN_BIN}#g" \
          -e "s#@CONFIG_PATH@#${CONFIG_PATH}#g" \
    | nicevpn_atomic_write_stdin "${UNIT_PATH}" 0644

systemctl daemon-reload
echo "[peer-client] 已安装兜底 systemd unit: ${UNIT_PATH} openvpn=${OPENVPN_BIN} config=${CONFIG_PATH}"
