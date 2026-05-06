# -*- coding: utf-8 -*-
"""设备绑定策略：将 SystemConfig.device_bind_mode 同步到 OpenVPN 可读的运行时文件。

client-connect 脚本路径：/etc/openvpn/scripts/device-bind.sh
策略文件路径：见 constants.DEVICE_BIND_MODE_FILE（一行 ASCII：weak_log / weak_fingerprint / strict_hwaddr）。
"""

import logging
import os

from app.core.constants import DEVICE_BIND_MODE_FILE, OPENVPN_MGMT_DIR
from app.utils.posix_data_perms import fix_path_for_openvpn_shared_data

logger = logging.getLogger(__name__)

_ALLOWED = frozenset({"weak_log", "weak_fingerprint", "strict_hwaddr"})


def sync_device_bind_mode_file(mode: str) -> None:
    """把当前策略写入 DEVICE_BIND_MODE_FILE，供 device-bind.sh 每次连接读取。

    非 Linux 或无法写盘时跳过并打日志。

    参数:
        mode: weak_log | weak_fingerprint | strict_hwaddr

    异常:
        OSError: 在应写入 Linux 部署路径但失败时抛出。
    """
    m = (mode or "").strip()
    if m not in _ALLOWED:
        m = "weak_fingerprint"
    if os.name == "nt":
        logger.debug("Windows 环境跳过写入 device_bind_mode 文件")
        return
    OPENVPN_MGMT_DIR.mkdir(parents=True, exist_ok=True)
    DEVICE_BIND_MODE_FILE.write_text(m + "\n", encoding="utf-8")
    fix_path_for_openvpn_shared_data(DEVICE_BIND_MODE_FILE)
    logger.info("已同步设备绑定策略到 %s: %s", DEVICE_BIND_MODE_FILE, m)
