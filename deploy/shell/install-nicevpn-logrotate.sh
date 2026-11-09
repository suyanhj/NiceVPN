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

[[ "$EUID" -eq 0 ]] || fail "写入 /etc/logrotate.d 需要 root 权限"
command -v logrotate >/dev/null 2>&1 || fail "未安装 logrotate，请先通过系统包管理器安装"
id nobody >/dev/null 2>&1 || fail "不存在 nobody 用户，无法保证 OpenVPN 钩子写入权限"
[[ "$LOG_DIR" == /* ]] || fail "NICEVPN_OPENVPN_LOG_DIR 必须是绝对路径"
[[ "$LOG_DIR" != *[[:space:]]* ]] || fail "日志目录不支持空白字符"
[[ "$LOG_DIR" != *"["* && "$LOG_DIR" != *"]"* &&
   "$LOG_DIR" != *"*"* && "$LOG_DIR" != *"?"* &&
   "$LOG_DIR" != *";"* ]] || fail "日志目录包含不支持的 logrotate 特殊字符"
[[ -f "$CONF_TEMPLATE" ]] ||
    fail "仓库中缺少轮转配置模板"

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
tmp_check="$(mktemp)"
trap 'rm -f -- "$tmp_conf" "$tmp_check"' EXIT

sed \
    -e "s|@OPENVPN_LOG_DIR@|$(escape_sed "$LOG_DIR")|g" \
    -e 's|@LOG_OWNER@|nobody|g' \
    -e "s|@LOG_GROUP@|$(escape_sed "$LOG_GROUP")|g" \
    "$CONF_TEMPLATE" > "$tmp_conf"

# 确保系统调度执行的主配置确实包含 /etc/logrotate.d。
# 只校验独立模板是不够的：系统其他配置可能已管理相同文件路径。
[[ -f /etc/logrotate.conf ]] || fail "系统缺少 /etc/logrotate.conf"
grep -Eq '^[[:space:]]*include[[:space:]]+/etc/logrotate[.]d([[:space:]]|$)' /etc/logrotate.conf ||
    fail "/etc/logrotate.conf 未 include /etc/logrotate.d，新策略不会自动运行"

if ! "$LOGROTATE_BIN" -d "$tmp_conf" > "$tmp_check" 2>&1; then
    tail -n 12 "$tmp_check" >&2
    fail "logrotate 独立策略校验失败，没有变更系统配置"
fi

install -d -m 0755 /etc/logrotate.d
# 保存现有策略以便全局校验失败后恢复，不破坏已有部署。
tmp_backup="$(mktemp)"
trap 'rm -f -- "$tmp_conf" "$tmp_backup" "$tmp_check"' EXIT
had_previous=0
if [[ -e "$CONF_TARGET" ]]; then
    [[ -f "$CONF_TARGET" && ! -L "$CONF_TARGET" ]] ||
        fail "目标路径不是普通文件：$CONF_TARGET"
    cp -p -- "$CONF_TARGET" "$tmp_backup"
    had_previous=1
fi

install -m 0644 "$tmp_conf" "$CONF_TARGET"
if ! "$LOGROTATE_BIN" -d /etc/logrotate.conf > "$tmp_check" 2>&1; then
    if [[ "$had_previous" = "1" ]]; then
        install -m 0644 "$tmp_backup" "$CONF_TARGET"
    else
        rm -f -- "$CONF_TARGET"
    fi
    tail -n 12 "$tmp_check" >&2
    fail "系统全局 logrotate 校验失败（可能有重复路径配置），已恢复原策略"
fi

# 系统原生 timer 可用时确保启用；没有 timer 的发行版沿用 cron.daily。
if command -v systemctl >/dev/null 2>&1 &&
   [[ -d /run/systemd/system ]] &&
   systemctl list-unit-files logrotate.timer --no-legend 2>/dev/null | grep -q '^logrotate.timer'; then
    if ! systemctl enable --now logrotate.timer >/dev/null 2>&1; then
        log "WARNING: 系统 logrotate.timer 无法启用，请检查发行版 cron.daily 配置"
    fi
fi

log "已安装 $CONF_TARGET，随系统 logrotate.timer / cron 自动检查"
log "设备绑定 16MiB，会话审计 64MiB，连接审计 128MiB，OpenVPN server.log 128MiB"
log "旧日志按日期和大小轮转并压缩，保留 14 份 / 最多 7 天"
log "server-status.log 不轮转；默认系统检查周期通常为每天一次"
