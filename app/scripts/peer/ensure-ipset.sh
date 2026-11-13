#!/usr/bin/env bash
# 经 SSH 执行的对端 ipset 依赖检查/安装；无需预先上传文件。
# 仅处理系统依赖，不管理任何 ipset 集合或防火墙规则。
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

if command -v ipset >/dev/null 2>&1; then
    echo "[peer-ipset] ipset 已安装，跳过"
    exit 0
fi

if [[ ! -r /etc/os-release ]]; then
    echo "[peer-ipset] 未找到 /etc/os-release，无法自动安装 ipset" >&2
    exit 1
fi
# shellcheck source=/dev/null
source /etc/os-release

family=""
read -r -a distro_tokens <<< "${ID:-} ${ID_LIKE:-}"
for distro in "${distro_tokens[@]}"; do
    case "${distro,,}" in
        debian|ubuntu) family="debian"; break ;;
        rhel|centos|rocky|almalinux|fedora) family="rhel"; break ;;
    esac
done

case "$family" in
    debian)
        echo "[peer-ipset] Debian 系，安装 ipset"
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y ipset
        ;;
    rhel)
        echo "[peer-ipset] RHEL 系，安装 ipset"
        if command -v dnf >/dev/null 2>&1; then
            dnf install -y ipset
        elif command -v yum >/dev/null 2>&1; then
            yum install -y ipset
        else
            echo "[peer-ipset] 未找到 dnf/yum，无法安装 ipset" >&2
            exit 1
        fi
        ;;
    *)
        echo "[peer-ipset] 不支持的发行版 ID=${ID:-unknown} ID_LIKE=${ID_LIKE:-none}，请手动安装 ipset" >&2
        exit 1
        ;;
esac

if ! command -v ipset >/dev/null 2>&1; then
    echo "[peer-ipset] 安装命令已完成，但 ipset 不在 PATH 中" >&2
    exit 1
fi

echo "[peer-ipset] ipset 已就绪"
