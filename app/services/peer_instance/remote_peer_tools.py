# -*- coding: utf-8 -*-
"""对端 Linux 防火墙工具依赖预检；包管理器与发行版逻辑留在共享 Shell 函数中。"""

from __future__ import annotations

import logging
import shlex
from pathlib import Path
from typing import Callable

logger = logging.getLogger("peer.remote")
_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
_LINUX_PACKAGE_HELPERS = _SCRIPTS_DIR / "common" / "linux-packages.sh"
_FIREWALL_TOOLS_INSTALL_SCRIPT = _SCRIPTS_DIR / "peer" / "ensure-firewall-tools.sh"


def ensure_remote_firewall_tools(
    client,
    sudo_prefix: str,
    exec_ssh: Callable,
    *,
    include_ipset: bool = False,
    timeout: int = 120,
) -> None:
    """确保对端 iptables 可用；需要标签集合时将 iptables/ipset 合并为一次包安装。

    共享 Shell 函数与入口脚本通过 SSH 内联执行，不需要在对端部署 NiceVPN 文件。
    任一命令缺失时由 nicevpn_ensure_commands 一次安装整组包。
    """
    try:
        common = _LINUX_PACKAGE_HELPERS.read_text(encoding="utf-8")
        script = _FIREWALL_TOOLS_INSTALL_SCRIPT.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"无法读取对端防火墙依赖脚本或共享函数: {exc}") from exc

    include_ipset_env = "1" if include_ipset else "0"
    payload = (
        common
        + "\nexport NICEVPN_INCLUDE_IPSET="
        + include_ipset_env
        + "\n"
        + script
    )
    command = f"{sudo_prefix}bash -lc {shlex.quote(payload)}"
    out, err, code = exec_ssh(client, command, timeout=max(timeout, 300))
    if code != 0:
        raise RuntimeError(
            "对端防火墙依赖检测/安装失败，请检查软件源和 sudo 权限："
            f"{(err or out or '').strip()[-1500:]}"
        )
    logger.info(
        "已确认对端防火墙依赖可用（iptables%s）",
        " + ipset" if include_ipset else "",
    )
