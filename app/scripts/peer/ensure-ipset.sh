#!/usr/bin/env bash
# SSH 对端 ipset 依赖检查/安装入口；不修改任何 ipset 集合或防火墙规则。
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

# 本机运行时 source 共享函数；SSH 内联执行时共用函数已预先加载。
if ! declare -F nicevpn_ensure_command >/dev/null; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # shellcheck source=../common/linux-packages.sh
    source "${SCRIPT_DIR}/../common/linux-packages.sh"
fi

if command -v ipset >/dev/null 2>&1; then
    echo "[peer-ipset] ipset 已安装，跳过"
    exit 0
fi

echo "[peer-ipset] ipset 未安装，开始检查发行版与包管理器"
nicevpn_ensure_command ipset ipset

echo "[peer-ipset] ipset 已就绪"
