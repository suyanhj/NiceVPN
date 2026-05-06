# -*- coding: utf-8 -*-
"""一次性下载链接生成与消费服务

生成带有效期的一次性下载令牌，用于安全分发 .ovpn 配置文件。
令牌以 JSON 文件形式持久化到 data/download_links/ 目录。
"""

import secrets
from datetime import datetime, timezone, timedelta

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
    """消费令牌：校验未过期且未使用后，原子置 used=true。

    参数:
        token: 下载令牌

    返回:
        成功时返回 {"username": ..., "file_path": ..., "error": None}
        校验失败返回 {"username": None, "file_path": None, "error": "错误描述"}
        令牌不存在返回 None
    """
    file_path = constants.DOWNLOAD_LINKS_DIR / f"{token}.json"

    if not file_path.exists():
        return None

    data = read_json(file_path)
    if not data:
        return None

    # 校验是否已使用
    if data.get("used", False):
        return {"username": None, "file_path": None, "error": "链接已被使用"}

    # 校验是否过期
    expires_at = datetime.fromisoformat(data["expires_at"])
    now = datetime.now(timezone.utc)

    # 如果 expires_at 没有时区信息，视为 UTC
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if now > expires_at:
        return {"username": None, "file_path": None, "error": "链接已过期"}

    # 原子更新：标记为已使用
    data["used"] = True
    data["used_at"] = now.isoformat()
    write_json_atomic(file_path, data)

    return {
        "username": data["username"],
        "file_path": data["file_path"],
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
