#!/usr/bin/env bash
# 对端 OpenVPN client：安装 systemd drop-in 与 logrotate，允许写入 /etc/openvpn/log
#
# 必需环境变量:
#   SERVICE_NAME  — 如 openvpn-client@client.service 或 openvpn-client.service
#
# 可选:
#   CLIENT_LOG_DIR   — 默认 /etc/openvpn/log
#   LOG_ROTATE_DAYS  — 默认 7

set -euo pipefail

: "${SERVICE_NAME:?必须设置 SERVICE_NAME}"
: "${CLIENT_LOG_DIR:=/etc/openvpn/log}"
: "${LOG_ROTATE_DAYS:=7}"

if [[ "${SERVICE_NAME}" == openvpn-client@*.service ]]; then
    OVERRIDE_DIR="/etc/systemd/system/openvpn-client@.service.d"
else
    OVERRIDE_DIR="/etc/systemd/system/${SERVICE_NAME}.d"
fi
OVERRIDE_PATH="${OVERRIDE_DIR}/10-ovpn-mgmt-log.conf"
ROTATE_PATH="/etc/logrotate.d/ovpn-mgmt-openvpn-client"

echo "[peer-client] 配置 client 日志目录与 systemd drop-in: service=${SERVICE_NAME}"

mkdir -p "${CLIENT_LOG_DIR}" "${OVERRIDE_DIR}"
cat > "${OVERRIDE_PATH}" <<'EOF'
[Service]
ReadWritePaths=/etc/openvpn/log
EOF

if command -v logrotate >/dev/null 2>&1; then
    cat > "${ROTATE_PATH}" <<EOF
${CLIENT_LOG_DIR}/client.log {
    daily
    rotate ${LOG_ROTATE_DAYS}
    missingok
    notifempty
    compress
    copytruncate
}
EOF
fi

systemctl daemon-reload
echo "[peer-client] client 日志 drop-in 已安装: ${OVERRIDE_PATH}"
