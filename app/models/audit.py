"""审计日志模型"""
import uuid
from typing import Literal, Optional
from pydantic import BaseModel, Field


class AuditEntry(BaseModel):
    """审计日志条目，使用哈希链保证不可篡改"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="日志条目唯一标识")
    timestamp: str = Field(..., description="事件时间(ISO8601)")
    operator: str = Field(default='admin', description="操作人")
    action: str = Field(..., description="操作类型（如create_user、revoke_cert）")
    target_type: str = Field(..., description="操作目标类型（如user、group、cert）")
    target_id: Optional[str] = Field(default=None, description="操作目标ID")
    detail: dict = Field(default_factory=dict, description="操作详情")
    result: Literal['success', 'failure'] = Field(..., description="操作结果")
    error_message: Optional[str] = Field(default=None, description="失败时的错误信息")
    prev_hash: str = Field(..., description="前一条日志的哈希值，形成哈希链")
    entry_hash: str = Field(..., description="当前条目的哈希值")
