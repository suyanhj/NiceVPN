#!/usr/bin/env bash
# 统一部署 NiceVPN 三个审计日志及 OpenVPN server.log 的轮转策略。
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
CONF_TEMPLATE="$ROOT_DIR/deploy/logrotate/nicevpn-openvpn.conf.in"

CONF_TARGET="/etc/logrotate.d/nicevpn-openvpn"
LOG_DIR="/etc/openvpn/log"
if [[ -v NICEVPN_OPENVPN_LOG_DIR ]]; then LOG_DIR="$NICEVPN_OPENVPN_LOG_DIR"; fi

fail() { printf '[NiceVPN logrotate] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[NiceVPN logrotate] %s\n' "$*"; }

[[ "$EUID" -eq 0 ]] || fail "安装 systemd 定时器必须使用 root"
command -v logrotate >/dev/null 2>&1 || fail "未安装 logrotate，请先通过系统包管理器安装"
id nobody >/dev/null 2>&1 || fail "不存在 nobody 用户，无法保证 OpenVPN 钩子写入权限"
[[ "$LOG_DIR" == /* ]] || fail "NICEVPN_OPENVPN_LOG_DIR 必须是绝对路径"
[[ "$LOG_DIR" != *[[:space:]]* ]] || fail "日志目录不支持空白字符"
[[ "$LOG_DIR" != *"["* && "$LOG_DIR" != *"]"* &&
   "$LOG_DIR" != *"*"* && "$LOG_DIR" != *"?"* &&
   "$LOG_DIR" != *";"* ]] || fail "日志目录包含不支持的 logrotate 特殊字符"
[[ -f "$CONF_TEMPLATE" ]] ||
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
trap 'rm -f -- "$tmp_conf"' EXIT

sed \
    -e "s|@OPENVPN_LOG_DIR@|$(escape_sed "$LOG_DIR")|g" \
    -e 's|@LOG_OWNER@|nobody|g' \
    -e "s|@LOG_GROUP@|$(escape_sed "$LOG_GROUP")|g" \
    "$CONF_TEMPLATE" > "$tmp_conf"

# 校验配置后再覆盖系统策略；-d 不实际轮转。
"$LOGROTATE_BIN" -d "$tmp_conf" >/dev/null ||
    fail "logrotate 配置校验失败，没有变更系统文件"

install -d -m 0755 /etc/logrotate.d
install -m 0644 "$tmp_conf" "$CONF_TARGET"

# 兼容此前独立 timer 试验版，防止两个定时器同时操作同一文件。
if [[ -f /etc/systemd/system/nicevpn-audit-logrotate.timer ]] &&
   command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now nicevpn-audit-logrotate.timer >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/nicevpn-audit-logrotate.timer \
          /etc/systemd/system/nicevpn-audit-logrotate.service \
          /etc/nicevpn/logrotate-audit.conf
    systemctl daemon-reload
fi

log "已安装 $CONF_TARGET，随系统 logrotate.timer / cron 自动检查"
log "设备绑定 16MiB，会话审计 64MiB，连接审计 128MiB，OpenVPN server.log 128MiB"
log "旧日志按日期和大小轮转并压缩，保留 14 份 / 最多 7 天"
log "server-status.log 不轮转；默认系统检查周期通常为每天一次"
