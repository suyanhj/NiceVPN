#!/usr/bin/env bash
# 对端防火墙工具依赖检查：iptables 必需；标签 ipset 功能启用时一并安装 ipset。
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

if ! declare -F nicevpn_ensure_commands >/dev/null; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # shellcheck source=../common/linux-packages.sh
    source "${SCRIPT_DIR}/../common/linux-packages.sh"
fi

if [[ "${NICEVPN_INCLUDE_IPSET:-0}" == "1" ]]; then
    # 缺少任一命令时，一次性安装整组依赖；apt/dnf/yum 均可安全重复执行。
    nicevpn_ensure_commands iptables iptables iptables-save iptables iptables-restore iptables iptables-save iptables iptables-restore iptables ipset ipset
else
    nicevpn_ensure_commands iptables iptables
fi
