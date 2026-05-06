#!/usr/bin/env bash
# OpenVPN 管理端 CLI — bash Tab 补全（argcomplete）
#
# 【本脚本】在系统路径创建指向本项目 cli.py 的固定命令（默认 /usr/local/bin/ovpn-cli），
# 并提示将 register-python-argcomplete 写入 ~/.bashrc。
#
# 用法（Linux）：
#   cd script/py/vpn/deploy/shell && bash install-ovpn-cli-symlink.sh
#   bash /path/to/install-ovpn-cli-symlink.sh ["$HOME/.local/bin/ovpn-cli"]   # 可选自定义链接路径
#
# 依赖：pip install argcomplete（requirements.txt 已列出）
#
# 手工临时启用（当前 shell、不建软链时）：
#   eval "$(register-python-argcomplete /你的项目根/cli.py)"
#
# 补全行为示例（固定命令或上述 eval 指向的脚本一致时）：
#   ovpn-cli <TAB><TAB>              → add-group add-user add-firewall、--dry-run
#   ovpn-cli add-group --<TAB>       → --dry-run、--name、--subnet 等
#   ovpn-cli add-firewall --owner-type <TAB>
#   ovpn-cli add-firewall --iptables-file <TAB>   → 路径补全（FilesCompleter）
#
# 将 eval 行写入 ~/.bashrc 后 source ~/.bashrc 可长期生效。
# zsh 需 bashcompinit 或改用 bash 子 shell，以 argcomplete 文档为准。
#
# 说明：「python main.py cli …」不宜直接注册 argcomplete（前缀词过多），请用本脚本或
# 手工 ln -sf 项目根/cli.py 到 PATH 内固定名后再 register-python-argcomplete。
#
# 运行 ovpn-cli 时工作目录须在项目根，或已 export PYTHONPATH=项目根（与 register 使用同一 Python 环境）。

set -euo pipefail

# 本脚本所在目录 → 项目根（script/py/vpn）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VPN_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CLI_PY="${VPN_ROOT}/cli.py"
LINK_NAME="${1:-/usr/local/bin/ovpn-cli}"
LINK_DIR="$(dirname "${LINK_NAME}")"

if [[ ! -f "${CLI_PY}" ]]; then
  echo "错误：找不到 cli.py：${CLI_PY}" >&2
  exit 1
fi

if [[ ! -x "$(command -v python3 2>/dev/null)" ]]; then
  echo "错误：需要 PATH 中的 python3 以执行 ${LINK_NAME}" >&2
  exit 1
fi

chmod +x "${CLI_PY}" 2>/dev/null || true

_mklink() {
  ln -sf "${CLI_PY}" "${LINK_NAME}"
}

if [[ -w "${LINK_DIR}" ]] || [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  mkdir -p "${LINK_DIR}"
  _mklink
else
  echo "目录 ${LINK_DIR} 无写权限，尝试使用 sudo …" >&2
  sudo mkdir -p "${LINK_DIR}"
  sudo ln -sf "${CLI_PY}" "${LINK_NAME}"
fi

echo "已创建：${LINK_NAME} -> ${CLI_PY}"
echo ""
echo "请将以下行写入 ~/.bashrc（或按需粘贴到当前 shell），然后 source ~/.bashrc："
echo "  eval \"\$(register-python-argcomplete '${LINK_NAME}')\""
echo ""
echo "说明：须已 pip install argcomplete，且 register-python-argcomplete 与运行 ovpn-cli 时使用同一 Python 环境。"
echo "执行 CLI 时请在项目根目录 cd ${VPN_ROOT}，或设置 export PYTHONPATH=${VPN_ROOT}"
