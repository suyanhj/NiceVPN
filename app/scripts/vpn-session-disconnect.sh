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

# 同步时写入 NiceVPN Python；必要时可用 PY3_AUDIT 覆盖。
init_py3_for_audit() {
    if [ -z "${PY3_AUDIT:-}" ]; then
        PY3_AUDIT=__NICEVPN_PYTHON_BIN__
    fi
    command -v "${PY3_AUDIT}" >/dev/null 2>&1 || return 1
    "${PY3_AUDIT}" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

fail() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [session-audit] 错误: $1" >&2
    exit 1
}

[ -f "${SESSION_AUDIT_PY}" ] || fail "未找到 ${SESSION_AUDIT_PY}"
init_py3_for_audit || fail "Python >= 3.10 不可用（请检查 PY3_AUDIT 与 NiceVPN 虚拟环境）"

"${PY3_AUDIT}" "${SESSION_AUDIT_PY}" \
    --event-type vpn_session_end \
    --log-file "${SESSION_AUDIT_LOG}" || fail "写入 vpn_session_end 失败"
