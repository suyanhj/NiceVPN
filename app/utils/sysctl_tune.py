# -*- coding: utf-8 -*-
"""VPN 网关宿主内核参数：通过 /etc/sysctl.d 持久化，不在应用每次启动时写 /proc。

模板：项目内 deploy/sysctl/99-openvpn-gateway.conf → /etc/sysctl.d/99-openvpn-gateway.conf
加载：sysctl -p <该文件>（仅应用本文件内键值）。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from app.core.constants import BASE_DIR

logger = logging.getLogger(__name__)

_SYSCTL_DROPIN_NAME = "99-openvpn-gateway.conf"


def _sysctl_source_path() -> Path:
    return BASE_DIR / "deploy" / "sysctl" / _SYSCTL_DROPIN_NAME


def install_vpn_sysctl_dropin() -> None:
    """
    将仓库内 deploy/sysctl/99-openvpn-gateway.conf 复制到 /etc/sysctl.d/ 并执行 sysctl -p 加载。

    Raises:
        OSError: Windows 环境。
        PermissionError: 非 root。
        FileNotFoundError: 仓库内模板文件不存在。
        RuntimeError: sysctl 命令不可用或执行失败。
    """
    if os.name == "nt":
        raise OSError("当前为 Windows，不支持安装 sysctl.d")

    euid = getattr(os, "geteuid", lambda: -1)()
    if euid != 0:
        raise PermissionError("需要 root 权限才能写入 /etc/sysctl.d/")

    src = _sysctl_source_path()
    if not src.is_file():
        raise FileNotFoundError(f"缺少内核调优模板文件: {src}")

    dst = Path("/etc/sysctl.d") / _SYSCTL_DROPIN_NAME
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    os.chmod(dst, 0o644)
    logger.info("已安装 sysctl 配置: %s", dst)

    sysctl_bin = shutil.which("sysctl") or "/sbin/sysctl"
    if not Path(sysctl_bin).exists() and sysctl_bin == "/sbin/sysctl":
        sysctl_bin = "/usr/sbin/sysctl"
    if not Path(sysctl_bin).exists():
        raise RuntimeError("未找到 sysctl 可执行文件")

    r = subprocess.run(
        [sysctl_bin, "-p", str(dst)],
        capture_output=True,
        text=True,
    )
    if r.stdout:
        logger.debug("sysctl -p stdout: %s", r.stdout.strip())
    if r.stderr:
        logger.warning("sysctl -p stderr: %s", r.stderr.strip())
    if r.returncode != 0:
        # 部分键（如 nf_conntrack）在未加载模块时会导致整文件 -p 报错；文件已在 sysctl.d，重启后仍会加载其余项
        logger.warning(
            "sysctl -p 退出码 %s（若仅 conntrack 相关可忽略；配置已写入 %s）",
            r.returncode,
            dst,
        )
    else:
        logger.info("已执行 sysctl -p %s", dst)
