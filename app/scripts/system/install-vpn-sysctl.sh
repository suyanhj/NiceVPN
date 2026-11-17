#!/usr/bin/env bash
# 安装 NiceVPN VPN 网关 sysctl drop-in 并立即加载本文件。
set -euo pipefail

: "${SYSCTL_SOURCE:?必须设置 SYSCTL_SOURCE}"
: "${SYSCTL_TARGET:=/etc/sysctl.d/99-openvpn-gateway.conf}"

if [ ! -f "${SYSCTL_SOURCE}" ]; then
    echo "缺少 sysctl 模板: ${SYSCTL_SOURCE}" >&2
    exit 2
fi

# sysctl.d 配置也复用项目共享原子写入，避免进程中断时留下半写入文件。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../common/atomic-files.sh
source "${SCRIPT_DIR}/../common/atomic-files.sh"
nicevpn_atomic_write_command "${SYSCTL_TARGET}" 0644 cat "${SYSCTL_SOURCE}"
echo "[sysctl] 已安装 ${SYSCTL_TARGET}"

SYSCTL_BIN="$(command -v sysctl || true)"
if [ -z "${SYSCTL_BIN}" ]; then
    for candidate in /sbin/sysctl /usr/sbin/sysctl; do
        if [ -x "${candidate}" ]; then
            SYSCTL_BIN="${candidate}"
            break
        fi
    done
fi
if [ -z "${SYSCTL_BIN}" ]; then
    echo "未找到 sysctl 可执行文件" >&2
    exit 3
fi

set +e
"${SYSCTL_BIN}" -p "${SYSCTL_TARGET}"
rc=$?
set -e
if [ "${rc}" -ne 0 ]; then
    echo "[sysctl] 警告：sysctl -p 退出码 ${rc}；配置已写入，部分依赖内核模块的键可能暂时无法加载" >&2
else
    echo "[sysctl] 已加载 ${SYSCTL_TARGET}"
fi
exit 0
