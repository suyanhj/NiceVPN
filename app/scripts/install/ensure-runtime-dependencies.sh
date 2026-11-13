#!/usr/bin/env bash
# NiceVPN 本机运行时依赖预检；具体发行版和软件包映射由共享函数维护。
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../common/linux-packages.sh
source "${SCRIPT_DIR}/../common/linux-packages.sh"

profile="${NICEVPN_DEPENDENCY_PROFILE:-openvpn-runtime}"
case "${profile}" in
    openvpn-runtime)
        nicevpn_ensure_openvpn_runtime_dependencies
        ;;
    easyrsa-runtime)
        nicevpn_ensure_easyrsa_runtime_dependencies
        ;;
    easyrsa-install)
        nicevpn_ensure_easyrsa_install_dependencies
        ;;
    *)
        echo "[dependencies] 未知依赖配置: ${profile}" >&2
        exit 2
        ;;
esac
