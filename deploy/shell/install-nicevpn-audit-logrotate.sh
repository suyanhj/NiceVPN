#!/usr/bin/env bash
# 管理 NiceVPN 的三个审计日志，不涉及 OpenVPN 自己的 server.log。
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
CONF_TEMPLATE="$ROOT_DIR/deploy/logrotate/nicevpn-audit.conf.in"
UNIT_TEMPLATE="$ROOT_DIR/deploy/systemd/nicevpn-audit-logrotate.service.in"
TIMER_TEMPLATE="$ROOT_DIR/deploy/systemd/nicevpn-audit-logrotate.timer"

CONF_TARGET="/etc/nicevpn/logrotate-audit.conf"
UNIT_TARGET="/etc/systemd/system/nicevpn-audit-logrotate.service"
TIMER_TARGET="/etc/systemd/system/nicevpn-audit-logrotate.timer"
LOG_DIR="/etc/openvpn/log"
if [[ -v NICEVPN_AUDIT_LOG_DIR ]]; then LOG_DIR="$NICEVPN_AUDIT_LOG_DIR"; fi

fail() { printf '[NiceVPN logrotate] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[NiceVPN logrotate] %s\n' "$*"; }

[[ "$EUID" -eq 0 ]] || fail "安装 systemd 定时器必须使用 root"
command -v systemctl >/dev/null 2>&1 || fail "未找到 systemctl，不能注册定时器"
command -v logrotate >/dev/null 2>&1 || fail "未安装 logrotate，请先通过系统包管理器安装"
id nobody >/dev/null 2>&1 || fail "不存在 nobody 用户，无法保证 OpenVPN 钩子写入权限"
[[ -d /run/systemd/system ]] || fail "当前环境没有运行 systemd；Docker 请在宿主机安装轮转定时器"
[[ "$LOG_DIR" == /* ]] || fail "NICEVPN_AUDIT_LOG_DIR 必须是绝对路径"
[[ "$LOG_DIR" != *[[:space:]]* ]] || fail "日志目录不支持空白字符"
[[ "$LOG_DIR" != *"["* && "$LOG_DIR" != *"]"* &&
   "$LOG_DIR" != *"*"* && "$LOG_DIR" != *"?"* &&
   "$LOG_DIR" != *";"* ]] || fail "日志目录包含不支持的 logrotate 特殊字符"
[[ -f "$CONF_TEMPLATE" && -f "$UNIT_TEMPLATE" && -f "$TIMER_TEMPLATE" ]] ||
    fail "仓库中缺少轮转配置或 systemd 模板"

LOG_DIR="$(printf '%s' "$LOG_DIR" | sed 's|/*$||')"
LOGROTATE_BIN="$(command -v logrotate)"
LOG_GROUP="$(id -gn nobody)"

# logrotate 会以 nobody 执行 rename/create；目录本身也必须可写。
# 如果 OpenVPN 初始化尚未创建该目录，后续初始化按现有权限流程创建。
if [[ -d "$LOG_DIR" ]] && command -v runuser >/dev/null 2>&1; then
    runuser -u nobody -- test -w "$LOG_DIR" ||
        fail "日志目录不可由 nobody 写入/重命名：$LOG_DIR；请先修复 OpenVPN 日志目录权限"
fi

escape_sed() {
    printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'
}

tmp_conf="$(mktemp)"
tmp_unit="$(mktemp)"
trap 'rm -f -- "$tmp_conf" "$tmp_unit"' EXIT

sed \
    -e "s|@OPENVPN_LOG_DIR@|$(escape_sed "$LOG_DIR")|g" \
    -e 's|@LOG_OWNER@|nobody|g' \
    -e "s|@LOG_GROUP@|$(escape_sed "$LOG_GROUP")|g" \
    "$CONF_TEMPLATE" > "$tmp_conf"

sed -e "s|@LOGROTATE_BIN@|$(escape_sed "$LOGROTATE_BIN")|g" \
    "$UNIT_TEMPLATE" > "$tmp_unit"

# -d 只检查策略，不轮转生产日志。
"$LOGROTATE_BIN" -d "$tmp_conf" >/dev/null ||
    fail "logrotate 配置校验失败，没有变更系统服务"

install -d -m 0755 /etc/nicevpn /var/lib/logrotate
install -m 0644 "$tmp_conf" "$CONF_TARGET"
install -m 0644 "$tmp_unit" "$UNIT_TARGET"
install -m 0644 "$TIMER_TEMPLATE" "$TIMER_TARGET"

systemctl daemon-reload
systemctl enable --now nicevpn-audit-logrotate.timer >/dev/null
log "已安装：$CONF_TARGET"
log "每 10 分钟检查：设备绑定 16M、会话审计 64M、连接审计 128M"
log "旧文件压缩，保留最多 14 份 / 7 天；新文件由 nobody:$LOG_GROUP 写入"
log "下次执行：systemctl list-timers nicevpn-audit-logrotate.timer"
