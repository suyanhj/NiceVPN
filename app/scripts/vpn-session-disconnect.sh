#!/bin/bash
# ============================================================
# OpenVPN client-disconnect 会话审计脚本
#
# 输出 vpn_session_end JSONL，供 Fluent Bit 采集到 VictoriaLogs。
# ============================================================
OVPN_ETC="/etc/openvpn"
SESSION_AUDIT_LOG="__VPN_SESSION_AUDIT_LOG_FILE__"
case "$SESSION_AUDIT_LOG" in __VPN_SESSION_*)
    SESSION_AUDIT_LOG="${OVPN_ETC}/log/openvpn-session-audit.jsonl"
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION_AUDIT_PY="${SCRIPT_DIR}/vpn_session_audit.py"

# 同步时使用 NiceVPN 已验证的 Python 解释器；可由 PY3_AUDIT 覆盖。
# 断开钩子只负责审计：任何 Python/日志故障都不阻塞 OpenVPN 清理流程。
warn() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [session-audit] 警告: $1" >&2
}

if [ ! -f "${SESSION_AUDIT_PY}" ]; then
    warn "未找到 ${SESSION_AUDIT_PY}，跳过断开审计"
    exit 0
fi

if [ -z "${PY3_AUDIT:-}" ]; then
    PY3_AUDIT=__NICEVPN_PYTHON_BIN__
fi
if ! command -v "${PY3_AUDIT}" >/dev/null 2>&1; then
    warn "会话审计 Python 不可用（PY3_AUDIT=${PY3_AUDIT}），跳过断开审计"
    exit 0
fi

if ! "${PY3_AUDIT}" "${SESSION_AUDIT_PY}" \
    --event-type vpn_session_end \
    --log-file "${SESSION_AUDIT_LOG}"; then
    warn "写入 vpn_session_end 失败（Python 或日志路径故障）"
fi
exit 0
