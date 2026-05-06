"""设备绑定模型"""
import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class DeviceBinding(BaseModel):
    """设备绑定记录，用于限制用户只能在指定设备连接"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="绑定记录唯一标识")
    username: str = Field(..., description="关联的用户名")
    fingerprint: str = Field(..., description="设备指纹")
    # 与 device-bind.sh 写入值一致（IV_HWADDR、weak_peer_info、machine-id 等）
    fingerprint_source: str = Field(..., description="指纹来源类型")
    # client-connect 环境变量 IV_PLAT（如 android/ios），由 shell 写入绑定 JSON
    iv_plat: Optional[str] = Field(default=None, description="客户端平台上报原始值")
    # client-connect 环境变量 time_ascii，与 status 中「连接开始」一致；断线后保留便于用户页展示上次会话起点
    last_connected_since: Optional[str] = Field(default=None, description="最近一次会话开始时间（OpenVPN time_ascii）")
    openvpn_unique_id: Optional[str] = Field(default=None, description="OpenVPN分配的唯一ID")
    bound_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="绑定时间(ISO8601)"
    )
    last_seen_at: Optional[str] = Field(default=None, description="最后在线时间(ISO8601)")
