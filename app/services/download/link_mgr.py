# -*- coding: utf-8 -*-
"""一次性下载链接生成与消费服务

生成带有效期的一次性下载令牌，用于安全分发 .ovpn 配置文件。
令牌以 JSON 文件形式持久化到 data/download_links/ 目录。
"""

import os
import re
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

if os.name == "posix":
    import fcntl

_CONSUME_LOCK = threading.RLock()
_VALID_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")


@contextmanager
def _locked_token_consume():
    """单目录常驻锁文件，避免双请求读到 used=false 后都成功。

    不能使用读写各自加锁的两次操作；也不能释放时删除锁文件。
    """
    constants_dir = constants.DOWNLOAD_LINKS_DIR
    constants_dir.mkdir(parents=True, exist_ok=True)
    with _CONSUME_LOCK:
        if os.name != "posix":
            yield
            return
        fd = os.open(constants_dir / ".consume.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


# 须用模块对象访问，便于单测 monkeypatch ``constants.DOWNLOAD_LINKS_DIR`` 等（勿 ``from … import X`` 绑死快照）
from app.core import constants
from app.models.download_link import DownloadLink
from app.utils.file_lock import write_json_atomic, read_json


def create_link(
    username: str,
    ovpn_path: str,
    base_url: str,
    download_filename: str | None = None,
) -> str:
    """生成一次性下载令牌。

    写入 data/download_links/{token}.json（含 expires_at, used=false）。

    参数:
        username: 关联的用户名
        ovpn_path: 待下载文件路径（.ovpn 或 .zip 等）
        base_url: 服务的基础 URL（如 https://example.com）
        download_filename: 浏览器下载显示名；默认按用户名推断 .ovpn

    返回:
        完整下载 URL: {base_url}/download/{token}
    """
    # 生成安全随机令牌
    token = secrets.token_urlsafe(32)

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=constants.DOWNLOAD_LINK_EXPIRE_SECONDS)

    link = DownloadLink(
        token=token,
        username=username,
        file_path=ovpn_path,
        expires_at=expires_at.isoformat(),
        used=False,
        created_at=now.isoformat(),
        download_filename=download_filename,
    )

    # 确保目录存在并写入令牌文件
    constants.DOWNLOAD_LINKS_DIR.mkdir(parents=True, exist_ok=True)
    file_path = constants.DOWNLOAD_LINKS_DIR / f"{token}.json"
    write_json_atomic(file_path, link.model_dump())

    # 去除 base_url 末尾斜杠，拼接下载路径
    base_url = base_url.rstrip("/")
    return f"{base_url}/download/{token}"


def consume_link(token: str) -> dict | None:
    """一次性原子消费：同一锁内完成查询、过期验证、文件校验和使用状态写入。

    跨线程、跨进程最多只允许一个请求获得可下载的文件路径。
    """
    if not _VALID_TOKEN.fullmatch(str(token)):
        return None

    token_file = constants.DOWNLOAD_LINKS_DIR / f"{token}.json"
    with _locked_token_consume():
        if not token_file.is_file():
            return None

        data = read_json(token_file)
        if not isinstance(data, dict) or data.get("token") != token:
            return None

        if data.get("used", False):
            return {"error": "already_used", "username": data.get("username")}

        try:
            expires_at = datetime.fromisoformat(str(data["expires_at"]))
        except (KeyError, ValueError, TypeError):
            return None
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        if now > expires_at:
            return {"error": "expired", "username": data.get("username")}

        target = Path(str(data.get("file_path") or ""))
        if not target.is_file():
            # 文件缺失不消费一次性链接；管理员恢复文件后仍可重试。
            return {"error": "file_missing", "username": data.get("username")}

        data["used"] = True
        data["used_at"] = now.isoformat()
        write_json_atomic(token_file, data)
        return {
            "username": data.get("username"),
            "file_path": str(target),
            "download_filename": data.get("download_filename"),
            "error": None,
        }


def get_link_info(token: str) -> dict | None:
    """获取链接信息（不消费）。

    参数:
        token: 下载令牌

    返回:
        链接信息字典，令牌不存在返回 None
    """
    file_path = constants.DOWNLOAD_LINKS_DIR / f"{token}.json"

    if not file_path.exists():
        return None

    data = read_json(file_path)
    return data if data else None
