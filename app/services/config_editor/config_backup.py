"""配置文件在线编辑前自动备份服务

编辑 OpenVPN 配置文件前，先将原文件复制到备份目录，
避免误操作导致配置丢失。备份文件名带时间戳以便溯源。
"""

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.core.constants import BACKUPS_DIR
from app.utils.audit_log import AuditLogger

logger = logging.getLogger(__name__)


def backup_before_edit(conf_path: str) -> str:
    """将原文件复制到 backups/{timestamp}_{filename}，返回备份路径。

    Args:
        conf_path: 待编辑的配置文件路径

    Returns:
        备份文件的绝对路径字符串

    Raises:
        FileNotFoundError: 原配置文件不存在
    """
    src = Path(conf_path)
    if not src.exists():
        raise FileNotFoundError(f"配置文件不存在: {conf_path}")

    # 确保备份目录存在
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

    # 生成带时间戳的备份文件名
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_name = f"{timestamp}_{src.name}"
    backup_path = BACKUPS_DIR / backup_name

    shutil.copy2(src, backup_path)
    logger.info("已备份配置文件 %s -> %s", conf_path, backup_path)

    return str(backup_path)


def save_with_backup(conf_path: str, new_content: str) -> str:
    """先备份再覆写配置文件，写入审计日志，返回备份路径。

    Args:
        conf_path: 待编辑的配置文件路径
        new_content: 新的文件内容

    Returns:
        备份文件的绝对路径字符串
    """
    audit = AuditLogger()

    # 先备份
    backup_path = backup_before_edit(conf_path)

    # 覆写配置文件
    target = Path(conf_path)
    target.write_text(new_content, encoding="utf-8")
    logger.info("已保存配置文件 %s", conf_path)

    # 写入审计日志
    audit.log(
        action="edit_config",
        target_type="config_file",
        target_id=target.name,
        detail=f"在线编辑配置文件 {conf_path}，备份已保存至 {backup_path}",
        result="success",
    )

    return backup_path
