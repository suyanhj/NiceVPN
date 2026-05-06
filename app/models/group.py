"""用户组模型"""
import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class Group(BaseModel):
    """用户组，关联子网和防火墙规则"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="组唯一标识")
    name: str = Field(..., description="组名称")
    subnet: str = Field(..., description="组子网CIDR")
    status: Literal['active', 'disabled'] = Field(default='active', description="组状态")
    user_count: int = Field(default=0, description="组内用户数量")
    firewall_rule_ids: list[str] = Field(default_factory=list, description="关联的防火墙规则ID列表")
    created_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="创建时间(ISO8601)"
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="更新时间(ISO8601)"
    )
