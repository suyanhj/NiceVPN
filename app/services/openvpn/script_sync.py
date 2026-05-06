# -*- coding: utf-8 -*-
"""将仓库内的 OpenVPN 钩子脚本同步到服务器配置目录（与 server.conf 中路径一致）。"""

import logging
from pathlib import Path

from app.core.constants import DEVICE_BIND_LOG_FILE, OPENVPN_ETC_DIR

logger = logging.getLogger(__name__)

_SCRIPT_NAMES = ("device-bind.sh", "device_binding_json.py")


def sync_packaged_openvpn_scripts(openvpn_conf_dir: str | Path) -> None:
    """
    把 app/scripts 下的 client-connect 脚本写入 ``<openvpn_conf_dir>/scripts/``。

    保存系统设置触发 regenerate 时也会调用，避免仓库脚本更新后远端仍是旧版本。
    """
    base = Path(openvpn_conf_dir)
    scripts_dir = base / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    scripts_pkg = Path(__file__).resolve().parent.parent.parent / "scripts"
    for script_name in _SCRIPT_NAMES:
        src = scripts_pkg / script_name
        if not src.is_file():
            logger.warning("打包脚本不存在，跳过同步: %s", src)
            continue
        content = src.read_text(encoding="utf-8").replace("\r\n", "\n")
        content = content.replace("__OPENVPN_ETC_DIR__", str(OPENVPN_ETC_DIR))
        content = content.replace("__DEVICE_BIND_LOG_FILE__", DEVICE_BIND_LOG_FILE.as_posix())
        dst = scripts_dir / script_name
        dst.write_text(content, encoding="utf-8", newline="\n")
        if script_name.endswith(".sh"):
            dst.chmod(0o755)
        else:
            dst.chmod(0o644)
        logger.debug("已同步 OpenVPN 脚本 %s -> %s", script_name, dst)
