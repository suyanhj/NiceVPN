# -*- coding: utf-8 -*-
"""日志过期清理：管理端 data/logs 轮转备份、OpenVPN 目录下陈旧 .log、审计按文件名日期删除。

说明：
- app.log / openvpn-install.log 由 TimedRotatingFileHandler 按天轮转，backupCount=LOG_RETENTION_DAYS；
  此处额外按 mtime 清理遗留的 *.log.*，防止历史 Rotating 后缀堆积。
- /etc/openvpn/log 下守护进程与 device-bind 日志：按 mtime 超过保留期则删除（活跃文件 mtime 会更新，一般不会被删）。
- 审计 audit-YYYY-MM-DD.jsonl：按文件名日期早于保留期则删除。
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.constants import (
    AUDIT_DIR,
    LOGS_DIR,
    LOG_RETENTION_DAYS,
    OPENVPN_DAEMON_LOG_DIR,
    OPENVPN_LOG_ROOT,
)

logger = logging.getLogger(__name__)

_AUDIT_NAME_RE = re.compile(r"^audit-(\d{4}-\d{2}-\d{2})\.jsonl$")


def cleanup_expired_logs(retention_days: int | None = None) -> None:
    """
    删除超过保留期的日志文件。

    参数:
        retention_days: 保留天数，默认 LOG_RETENTION_DAYS。

    说明:
        任一删除失败会记录错误并抛出 OSError / RuntimeError（不静默吞掉）。
    """
    if os.name == "nt":
        return

    days = int(LOG_RETENTION_DAYS if retention_days is None else retention_days)
    if days < 1:
        raise ValueError("retention_days 必须 >= 1")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    removed = 0

    def _unlink_if_expired(path: Path) -> None:
        nonlocal removed
        if not path.is_file():
            return
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError as e:
            logger.error("无法 stat 日志文件 %s: %s", path, e)
            raise
        if mtime >= cutoff:
            return
        try:
            path.unlink()
            removed += 1
            logger.info("已删除过期日志: %s (mtime=%s)", path, mtime.date())
        except OSError as e:
            logger.error("删除日志失败 %s: %s", path, e)
            raise

    # data/logs：轮转产生的 app.log.YYYY-MM-DD、openvpn-install.log.* 等
    if LOGS_DIR.is_dir():
        for p in LOGS_DIR.iterdir():
            if not p.is_file():
                continue
            name = p.name
            if name in ("app.log", "openvpn-install.log"):
                continue
            if name.startswith("app.log.") or name.startswith("openvpn-install.log."):
                _unlink_if_expired(p)

    # OpenVPN 守护进程与状态、设备绑定日志
    scan_dirs = tuple(dict.fromkeys((OPENVPN_DAEMON_LOG_DIR, OPENVPN_LOG_ROOT)))
    for scan_dir in scan_dirs:
        if not scan_dir.is_dir():
            continue
        for p in scan_dir.iterdir():
            if not p.is_file():
                continue
            if not (p.suffix == ".log" or p.name.endswith("-status.log")):
                continue
            _unlink_if_expired(p)

    # 审计：按文件名日期
    if AUDIT_DIR.is_dir():
        for p in AUDIT_DIR.iterdir():
            if not p.is_file():
                continue
            m = _AUDIT_NAME_RE.match(p.name)
            if not m:
                continue
            try:
                file_day = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if file_day.date() >= cutoff.date():
                continue
            try:
                p.unlink()
                removed += 1
                logger.info("已删除过期审计日志: %s", p.name)
            except OSError as e:
                logger.error("删除审计日志失败 %s: %s", p, e)
                raise

    if removed:
        logger.info("日志清理完成，共删除 %d 个文件，保留期=%d 天", removed, days)
    else:
        logger.debug("日志清理完成，无过期文件，保留期=%d 天", days)
