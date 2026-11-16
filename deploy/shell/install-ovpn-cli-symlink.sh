#!/usr/bin/env bash
# 安装 ovpn-cli 命令并配置 argcomplete 的 Bash 补全提示。

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VPN_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CLI_PY="${VPN_ROOT}/cli.py"
LINK_NAME="${1:-/usr/local/bin/ovpn-cli}"
LINK_DIR="$(dirname "${LINK_NAME}")"
PYTHON_BIN="${PYTHON_BIN:-}"

if [[ ! -f "${CLI_PY}" ]]; then
    echo "错误：找不到 cli.py：${CLI_PY}" >&2
    exit 1
fi

# 与 start.sh 一致：优先项目虚拟环境，再使用当前环境或系统 Python。
if [[ -z "${PYTHON_BIN}" ]]; then
    if [[ -x "${VPN_ROOT}/.venv/bin/python" ]]; then
        PYTHON_BIN="${VPN_ROOT}/.venv/bin/python"
    elif [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
        PYTHON_BIN="${VIRTUAL_ENV}/bin/python"
    elif [[ -n "${CONDA_PREFIX:-}" && -x "${CONDA_PREFIX}/bin/python" ]]; then
        PYTHON_BIN="${CONDA_PREFIX}/bin/python"
    else
        PYTHON_BIN="$(command -v python3 || true)"
    fi
fi

if [[ -z "${PYTHON_BIN}" || ! -x "${PYTHON_BIN}" ]]; then
    echo "错误：找不到可用 Python；可通过 PYTHON_BIN=/path/to/python 指定。" >&2
    exit 1
fi

"${PYTHON_BIN}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || {
    echo "错误：NiceVPN CLI 要求 Python >= 3.10。" >&2
    exit 1
}

"${PYTHON_BIN}" -c 'import argcomplete' 2>/dev/null || {
    echo "错误：当前 Python 未安装 argcomplete，请执行：${PYTHON_BIN} -m pip install -r ${VPN_ROOT}/requirements.txt" >&2
    exit 1
}

REGISTER_BIN="$("${PYTHON_BIN}" -c 'import sysconfig; from pathlib import Path; print(Path(sysconfig.get_path("scripts")) / "register-python-argcomplete")')"
if [[ ! -x "${REGISTER_BIN}" ]]; then
    echo "错误：找不到 register-python-argcomplete：${REGISTER_BIN}" >&2
    exit 1
fi

chmod +x "${CLI_PY}" 2>/dev/null || true

if [[ -w "${LINK_DIR}" || "${EUID:-$(id -u)}" -eq 0 ]]; then
    mkdir -p "${LINK_DIR}"
    ln -sf "${CLI_PY}" "${LINK_NAME}"
else
    echo "目录 ${LINK_DIR} 无写权限，尝试使用 sudo …" >&2
    sudo mkdir -p "${LINK_DIR}"
    sudo ln -sf "${CLI_PY}" "${LINK_NAME}"
fi

# 转成绝对路径，确保 .bashrc 在任意工作目录都能注册补全。
LINK_NAME="$(cd "${LINK_DIR}" && pwd)/$(basename "${LINK_NAME}")"
PYTHON_BIN_DIR="$("${PYTHON_BIN}" -c 'import os, sys; print(os.path.dirname(sys.executable))')"

echo "已创建：${LINK_NAME} -> ${CLI_PY}"
echo
echo "将以下两行加入 ~/.bashrc，然后执行 source ~/.bashrc："
printf '  export PATH=%q:"$PATH"\n' "${PYTHON_BIN_DIR}"
printf '  eval "$(%q %q)"\n' "${REGISTER_BIN}" "${LINK_NAME}"
echo
echo "使用：ovpn-cli <TAB><TAB>"
