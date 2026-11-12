#!/usr/bin/env bash
# 当系统已安装 OpenVPN 二进制、但没有 server systemd 模板单元时补齐。
# 不编译、不安装、不替换 OpenVPN 二进制，也不覆盖发行版已有的 unit。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=openvpn-install-common.sh
source "${SCRIPT_DIR}/openvpn-install-common.sh"

: "${OPENVPN_BIN:?必须设置 OPENVPN_BIN}"

UNIT_NAME="openvpn-server@.service"
UNIT_PATH="/etc/systemd/system/${UNIT_NAME}"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TEMPLATE="${PROJECT_ROOT}/deploy/systemd/openvpn-server@.service.in"

if [[ "${EUID}" -ne 0 ]]; then
    echo "安装 OpenVPN systemd 服务单元需要 root 权限" >&2
    exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
    echo "未找到 systemctl，无法安装 OpenVPN 服务端 systemd 单元" >&2
    exit 1
fi

# 先检查 systemd 的标准单元搜索目录，绝不覆盖发行版包提供的 unit。
for candidate in \
    "/etc/systemd/system/${UNIT_NAME}" \
    "/run/systemd/system/${UNIT_NAME}" \
    "/usr/local/lib/systemd/system/${UNIT_NAME}" \
    "/usr/lib/systemd/system/${UNIT_NAME}" \
    "/lib/systemd/system/${UNIT_NAME}"; do
    if [ -f "${candidate}" ]; then
        _log "已存在 OpenVPN 服务单元，跳过安装: ${candidate}"
        exit 0
    fi
done

if [ ! -x "${OPENVPN_BIN}" ]; then
    echo "OpenVPN 二进制不存在或不可执行: ${OPENVPN_BIN}" >&2
    exit 1
fi

if [ ! -f "${TEMPLATE}" ]; then
    echo "NiceVPN 自带 OpenVPN 服务模板不存在: ${TEMPLATE}" >&2
    exit 1
fi

mkdir -p /etc/systemd/system
tmp_file="$(mktemp "${UNIT_PATH}.XXXXXX")"
trap 'rm -f "${tmp_file}"' EXIT

python3 - "${TEMPLATE}" "${tmp_file}" "${OPENVPN_BIN}" <<'PY'
from pathlib import Path
import sys

template_path, output_path, binary = sys.argv[1:]
text = Path(template_path).read_text(encoding="utf-8")
if "@OPENVPN_BIN@" not in text:
    raise SystemExit("OpenVPN systemd 模板缺少 @OPENVPN_BIN@ 占位符")
Path(output_path).write_text(text.replace("@OPENVPN_BIN@", binary), encoding="utf-8")
PY

chmod 0644 "${tmp_file}"
install -o root -g root -m 0644 "${tmp_file}" "${UNIT_PATH}"

# 保持与源码安装路径一致：允许 OpenVPN 写入 /etc/openvpn 下的运行状态和日志。
INSTALL_SERVER_OVERRIDE=1
export INSTALL_SERVER_OVERRIDE
openvpn_install_server_systemd_override

_log "已使用 NiceVPN 自带模板安装 ${UNIT_PATH}"
_log "ExecStart 指向: ${OPENVPN_BIN}"
