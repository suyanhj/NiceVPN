#!/bin/bash
# ============================================================
# OpenVPN client-disconnect 会话审计脚本
#
# 输出 vpn_session_end JSONL，供 Fluent Bit 采集到 VictoriaLogs。
# ============================================================
OVPN_ETC="/etc/openvpn"
SESSION_AUDIT_LOG="__VPN_SESSION_AUDIT_LOG_FILE__"
case "$SESSION_AUDIT_LOG" in *"__VPN_SESSION_AUDIT_LOG_FILE__"*)
    SESSION_AUDIT_LOG="${OVPN_ETC}/log/openvpn-session-audit.jsonl"
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION_AUDIT_PY="${SCRIPT_DIR}/vpn_session_audit.py"

init_py3_for_audit() {
    [ -n "${PY3_AUDIT:-}" ] && return 0
    local v name
    for v in 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6; do
        name="python3.${v}"
        if command -v "$name" >/dev/null 2>&1; then
            PY3_AUDIT="$name"
            return 0
        fi
    done
    if command -v python3 >/dev/null 2>&1; then
        PY3_AUDIT="python3"
        return 0
    fi
    return 1
}

fail() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [session-audit] 错误: $1" >&2
    exit 1
}

[ -f "${SESSION_AUDIT_PY}" ] || fail "未找到 ${SESSION_AUDIT_PY}"
init_py3_for_audit || fail "未找到 python3 / python3.x"

"${PY3_AUDIT}" "${SESSION_AUDIT_PY}" \
    --event-type vpn_session_end \
    --log-file "${SESSION_AUDIT_LOG}" || fail "写入 vpn_session_end 失败"
