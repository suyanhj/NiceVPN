"""用户模型"""
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field


class User(BaseModel):
    """VPN用户，关联组、设备绑定和证书"""
    username: str = Field(..., description="用户名")
    group_id: str = Field(..., description="所属组ID")
    password_enabled: bool = Field(default=False, description="是否启用密码认证")
    password_hash: Optional[str] = Field(default=None, description="密码哈希值")
    status: Literal['active', 'disabled', 'deleted'] = Field(
        default='active', description="用户状态"
    )
    ovpn_file_path: Optional[str] = Field(default=None, description="OVPN配置文件路径")
    device_binding_id: Optional[str] = Field(default=None, description="绑定的设备ID")
    cert_serial: Optional[str] = Field(default=None, description="证书序列号")
    firewall_rule_ids: list[str] = Field(default_factory=list, description="关联的防火墙规则ID列表")
    created_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="创建时间(ISO8601)"
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="更新时间(ISO8601)"
    )
