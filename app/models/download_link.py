# -*- coding: utf-8 -*-
"""一次性下载链接模型"""
from pydantic import BaseModel, Field
from typing import Optional


class DownloadLink(BaseModel):
    """一次性下载链接，用于分发OVPN配置文件"""
    token: str = Field(..., description="下载令牌")
    username: str = Field(..., description="关联的用户名")
    file_path: str = Field(..., description="待下载文件路径")
    # 非空时覆盖下载 Content-Disposition 文件名（如批量 zip：前缀_时间.zip）
    download_filename: Optional[str] = Field(default=None, description="自定义下载文件名")
    expires_at: str = Field(..., description="过期时间(ISO8601)")
    used: bool = Field(default=False, description="是否已使用")
    created_at: str = Field(..., description="创建时间(ISO8601)")
    used_at: Optional[str] = Field(default=None, description="使用时间(ISO8601)")
