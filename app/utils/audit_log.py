"""审计日志工具

提供 AuditLogger 单例类，将操作审计记录以 JSONL 格式追加写入日志文件。
- 日志按天分割，文件名格式：audit-YYYY-MM-DD.jsonl
- 每条日志包含前一条的 SHA-256 哈希，形成哈希链以防篡改
- 存储目录由 app.core.constants.AUDIT_DIR 指定
"""

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.constants import AUDIT_DIR


class AuditLogger:
    """审计日志记录器（单例模式）。

    使用哈希链保证日志完整性：每条新日志的 prev_hash 字段
    存储上一条日志的 SHA-256 摘要值。首条日志的 prev_hash 为全零哈希。
    """

    _instance: Optional["AuditLogger"] = None
    _init_lock = threading.Lock()

    # 哈希链初始值（首条日志的 prev_hash）
    _GENESIS_HASH = "0" * 64

    def __new__(cls) -> "AuditLogger":
        """确保全局只创建一个实例。"""
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    cls._instance = instance
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        # 写入锁，保证多线程追加写入的顺序性
        self._write_lock = threading.Lock()
        # 当前日期字符串，用于检测日期切换
        self._current_date: str = ""
        # 上一条日志的哈希值
        self._prev_hash: str = self._GENESIS_HASH
        # 确保审计目录存在
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        # 从已有日志文件恢复哈希链状态
        self._restore_chain()
        self._initialized = True

    # ---- 公开接口 ----

    def log(
        self,
        action: str,
        target_type: str,
        target_id: str,
        detail: str,
        result: str,
        error_message: Optional[str] = None,
    ) -> None:
        """记录一条审计日志。

        参数:
            action:        操作类型，如 "create_user", "revoke_cert"
            target_type:   操作对象类型，如 "user", "group", "cert"
            target_id:     操作对象标识
            detail:        操作详情描述
            result:        操作结果，如 "success", "failure"
            error_message: 失败时的错误信息（可选）
        """
        with self._write_lock:
            now = datetime.now(timezone.utc)
            today = now.strftime("%Y-%m-%d")

            # 日期切换时重新加载当天文件的最后哈希
            if today != self._current_date:
                self._current_date = today
                self._prev_hash = self._load_last_hash(today)

            # 构造日志条目
            entry = {
                "timestamp": now.isoformat(),
                "action": action,
                "target_type": target_type,
                "target_id": target_id,
                "detail": detail,
                "result": result,
                "prev_hash": self._prev_hash,
            }
            if error_message is not None:
                entry["error_message"] = error_message

            # 序列化并计算本条哈希
            line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
            current_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()
            self._prev_hash = current_hash

            # 追加写入当天的 JSONL 文件
            log_file = self._get_log_path(today)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    # ---- 内部方法 ----

    @staticmethod
    def _get_log_path(date_str: str) -> Path:
        """根据日期字符串返回对应的日志文件路径。"""
        return AUDIT_DIR / f"audit-{date_str}.jsonl"

    def _load_last_hash(self, date_str: str) -> str:
        """读取指定日期日志文件的最后一行，计算其 SHA-256 哈希。

        文件不存在或为空时返回创世哈希。
        """
        log_file = self._get_log_path(date_str)
        if not log_file.exists():
            return self._GENESIS_HASH

        last_line = ""
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for raw_line in f:
                    stripped = raw_line.strip()
                    if stripped:
                        last_line = stripped
        except OSError:
            return self._GENESIS_HASH

        if not last_line:
            return self._GENESIS_HASH

        return hashlib.sha256(last_line.encode("utf-8")).hexdigest()

    def _restore_chain(self) -> None:
        """启动时从最新的日志文件恢复哈希链状态。"""
        # 查找审计目录中最新的日志文件
        log_files = sorted(AUDIT_DIR.glob("audit-*.jsonl"))
        if not log_files:
            return

        latest = log_files[-1]
        # 从文件名提取日期（audit-YYYY-MM-DD.jsonl）
        date_str = latest.stem.replace("audit-", "")
        self._current_date = date_str
        self._prev_hash = self._load_last_hash(date_str)
