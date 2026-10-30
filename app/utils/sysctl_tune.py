# -*- coding: utf-8 -*-
"""VPN 网关宿主内核参数：通过 /etc/sysctl.d 持久化，不在应用每次启动时写 /proc。

模板：项目内 deploy/sysctl/99-openvpn-gateway.conf → /etc/sysctl.d/99-openvpn-gateway.conf
加载：sysctl -p <该文件>（仅应用本文件内键值）。
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from app.core.constants import BASE_DIR

logger = logging.getLogger(__name__)

_SYSCTL_DROPIN_NAME = "99-openvpn-gateway.conf"
_SYSCTL_INSTALL_SCRIPT = (
    BASE_DIR / "app" / "scripts" / "system" / "install-vpn-sysctl.sh"
)


def _sysctl_source_path() -> Path:
    return BASE_DIR / "deploy" / "sysctl" / _SYSCTL_DROPIN_NAME


def install_vpn_sysctl_dropin() -> None:
    """调用独立 shell 脚本安装并加载 VPN sysctl drop-in。"""

    if os.name == "nt":
        raise OSError("当前为 Windows，不支持安装 sysctl.d")

    euid = getattr(os, "geteuid", lambda: -1)()
    if euid != 0:
        raise PermissionError("需要 root 权限才能写入 /etc/sysctl.d/")

    src = _sysctl_source_path()
    if not src.is_file():
        raise FileNotFoundError(f"缺少内核调优模板文件: {src}")
    if not _SYSCTL_INSTALL_SCRIPT.is_file():
        raise FileNotFoundError(f"缺少 sysctl 安装脚本: {_SYSCTL_INSTALL_SCRIPT}")

    dst = Path("/etc/sysctl.d") / _SYSCTL_DROPIN_NAME
    env = dict(os.environ)
    env.update(
        {
            "SYSCTL_SOURCE": str(src),
            "SYSCTL_TARGET": str(dst),
        }
    )
    result = subprocess.run(
        ["bash", str(_SYSCTL_INSTALL_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        logger.info("sysctl 安装输出: %s", result.stdout.strip())
    if result.stderr:
        logger.warning("sysctl 安装 stderr: %s", result.stderr.strip())
    if result.returncode != 0:
        raise RuntimeError(
            f"安装 sysctl 配置失败（退出码 {result.returncode}）: "
            f"{(result.stderr or result.stdout).strip()}"
        )

