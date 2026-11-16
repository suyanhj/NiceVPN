#!/usr/bin/env bash
# 安装 ovpn-cli 启动包装器并配置 argcomplete Bash 补全。
# 文件名保留 symlink 旧名称，兼容既有部署命令。

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VPN_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CLI_PY="${VPN_ROOT}/cli.py"
LINK_NAME="${1:-/usr/local/bin/ovpn-cli}"
LINK_DIR="$(dirname "${LINK_NAME}")"
PYTHON_BIN="${PYTHON_BIN:-}"

source "${VPN_ROOT}/deploy/shell/python-runtime.sh"

if [[ ! -f "${CLI_PY}" ]]; then
    echo "错误：找不到 cli.py：${CLI_PY}" >&2
    exit 1
fi

# 与 start.sh 共用解释器识别逻辑；自动跳过低于 Python 3.10 的候选项。
nicevpn_select_python "${VPN_ROOT}" || exit 1
printf '[NiceVPN] 使用 Python %s：%s\n' "$(nicevpn_python_version "${PYTHON_BIN}")" "${PYTHON_BIN}"

"${PYTHON_BIN}" -c 'import argcomplete' 2>/dev/null || {
    echo "错误：当前 Python 未安装 argcomplete。" >&2
    echo "请执行：${PYTHON_BIN} -m pip install argcomplete" >&2
    exit 1
}

REGISTER_BIN="$("${PYTHON_BIN}" -c 'import sysconfig; from pathlib import Path; print(Path(sysconfig.get_path("scripts")) / "register-python-argcomplete")')"
if [[ ! -x "${REGISTER_BIN}" ]]; then
    echo "错误：找不到 register-python-argcomplete：${REGISTER_BIN}" >&2
    exit 1
fi

# 先创建命令目录，再将路径规范化为绝对路径。
if [[ -w "${LINK_DIR}" || "${EUID:-$(id -u)}" -eq 0 ]]; then
    mkdir -p "${LINK_DIR}"
else
    echo "目录 ${LINK_DIR} 无写权限，尝试使用 sudo …" >&2
    sudo mkdir -p "${LINK_DIR}"
fi
LINK_DIR="$(cd "${LINK_DIR}" && pwd)"
LINK_NAME="${LINK_DIR}/$(basename "${LINK_NAME}")"
CLI_NAME="$(basename "${LINK_NAME}")"

# 使用固定解释器生成包装器，避免 /usr/bin/env python3 又命中旧版系统 Python。
WRAPPER_TMP="$(mktemp "${TMPDIR:-/tmp}/nicevpn-cli.XXXXXX")"
trap 'rm -f "${WRAPPER_TMP:-}"' EXIT
{
    printf '#!/usr/bin/env bash\n'
    printf 'exec %q %q "$@"\n' "${PYTHON_BIN}" "${CLI_PY}"
} > "${WRAPPER_TMP}"

if [[ -w "${LINK_DIR}" || "${EUID:-$(id -u)}" -eq 0 ]]; then
    install -m 0755 "${WRAPPER_TMP}" "${LINK_NAME}"
else
    sudo install -m 0755 "${WRAPPER_TMP}" "${LINK_NAME}"
fi

echo "已安装：${LINK_NAME}"
echo "固定 Python：${PYTHON_BIN}"
echo
echo "将以下两行加入 ~/.bashrc，然后执行 source ~/.bashrc："
printf '  export PATH=%q:"$PATH"\n' "${LINK_DIR}"
printf '  eval "$(%q %q)"\n' "${REGISTER_BIN}" "${CLI_NAME}"
echo
echo "使用：${CLI_NAME} <TAB><TAB>"
