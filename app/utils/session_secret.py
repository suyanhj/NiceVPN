# -*- coding: utf-8 -*-
"""管理后台会话签名密钥的持久化工具。"""

from __future__ import annotations

import os
import re
import secrets
import tempfile
from pathlib import Path

from app.core.constants import ADMIN_SESSION_SECRET_FILE
from app.utils.file_lock import file_lock


_SESSION_SECRET_PATTERN = re.compile(r"^[A-Za-z0-9_-]{64,}$")


def _read_session_secret(path: Path) -> str:
    """读取并校验已经持久化的会话密钥。"""

    secret = path.read_text(encoding="utf-8").strip()
    if not _SESSION_SECRET_PATTERN.fullmatch(secret):
        raise RuntimeError(f"会话签名密钥文件无效: {path}")
    return secret


def _write_session_secret_atomic(path: Path, secret: str) -> None:
    """以同目录临时文件和原子替换写入会话密钥。"""

    file_descriptor, temporary_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=".admin_session_secret_",
        suffix=".tmp",
    )
    try:
        os.chmod(temporary_path, 0o600)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as file:
            file.write(secret)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.close(file_descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary_path)
        except OSError:
            pass
        raise


def load_or_create_session_secret(
    path: str | Path = ADMIN_SESSION_SECRET_FILE,
) -> str:
    """读取稳定会话密钥；首次启用认证时安全生成并持久化。"""

    secret_path = Path(path).resolve()
    secret_path.parent.mkdir(parents=True, exist_ok=True)

    with file_lock(secret_path):
        if secret_path.exists():
            secret = _read_session_secret(secret_path)
            os.chmod(secret_path, 0o600)
            return secret

        secret = secrets.token_urlsafe(48)
        _write_session_secret_atomic(secret_path, secret)
        return secret
