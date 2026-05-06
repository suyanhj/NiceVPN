"""证书模型"""
from typing import Literal, Optional
from pydantic import BaseModel, Field


class Certificate(BaseModel):
    """EasyRSA管理的证书记录"""
    serial: str = Field(..., description="证书序列号")
    common_name: str = Field(..., description="证书通用名称（通常为用户名）")
    issued_at: str = Field(..., description="签发时间(ISO8601)")
    expires_at: str = Field(..., description="过期时间(ISO8601)")
    status: Literal['valid', 'revoked', 'expired'] = Field(
        default='valid', description="证书状态"
    )
    revoked_at: Optional[str] = Field(default=None, description="吊销时间(ISO8601)")
    crl_version: Optional[int] = Field(default=None, description="CRL版本号")
