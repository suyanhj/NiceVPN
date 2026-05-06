# -*- coding: utf-8 -*-
"""防火墙规则模型"""
import re
import uuid
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator

# 预置协议选项（下拉可选） + 支持手动输入任意协议
PRESET_PROTOCOLS = ["tcp", "udp", "icmp", "all"]


class FirewallRule(BaseModel):
    """防火墙规则，关联到组或用户"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="规则唯一标识")
    owner_type: Literal['group', 'user'] = Field(..., description="规则归属类型")
    owner_id: str = Field(..., description="归属对象ID（组ID或用户名）")
    deployment_target: Literal["center", "peer"] = Field(
        default="center",
        description="部署目标：center=中心 VPN 主机 iptables；peer=对端站点（仅规划/标识，中心不生成 iptables）",
    )
    instance: str = Field(default="server", description="OpenVPN实例名称（单机默认 server）")
    action: Literal['accept', 'drop', 'reject'] = Field(..., description="规则动作")
    source_subnet: Optional[str] = Field(default=None, description="源子网CIDR")
    source_ips: Optional[list[str]] = Field(default=None, description="源IP列表（用户类型时使用）")
    dest_ip: Optional[str] = Field(default=None, description="目标IP，多个用逗号分隔，置空表示允许所有")
    dest_port: Optional[str] = Field(
        default=None,
        description="目标端口，置空表示允许所有。单端口；范围用冒号或连字符（80:443、80-443）；多端口用英文逗号",
    )
    protocol: str = Field(default='all', description="协议类型: all(不限) / tcp / udp / icmp 等，支持自定义")
    priority: int = Field(..., description="规则优先级，数值越小优先级越高")
    enabled: bool = Field(default=True, description="规则是否启用")
    description: Optional[str] = Field(default=None, description="规则描述")
    created_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="创建时间(ISO8601)"
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="更新时间(ISO8601)"
    )

    @field_validator('dest_port')
    @classmethod
    def validate_dest_port(cls, v: Optional[str]) -> Optional[str]:
        """校验端口：单端口；范围用 ``:`` 或 ``-``；多段用英文逗号。"""
        if v is None:
            return v
        text = v.strip()
        if not text:
            return None
        pattern = r"^(\d+)(?::(\d+))?$"
        if "," in text:
            parts: list[str] = []
            for part in text.split(","):
                p = part.strip()
                if not p:
                    continue
                norm = cls._validate_one_port_token(p, pattern)
                parts.append(norm)
            return ",".join(parts)
        return cls._validate_one_port_token(text, pattern)

    _PORT_ERR_HINT = (
        "支持：单端口如 80；"
        "连续端口范围用冒号或连字符（80:443 与 80-443 等价，勿写 80,443 作范围）；"
        "多个端口/范围用英文逗号分隔，如 80,443,8000-8080"
    )

    @classmethod
    def _normalize_port_range_token(cls, token: str) -> str:
        """将误用的 ``起-止`` 转为与 iptables 一致的 ``起:止``，仅当整体匹配两段数字时。"""
        m = re.match(r"^(\d+)-(\d+)$", token)
        if m:
            return f"{m.group(1)}:{m.group(2)}"
        return token

    @classmethod
    def _validate_one_port_token(cls, token: str, pattern: str) -> str:
        token = cls._normalize_port_range_token(token)
        match = re.match(pattern, token)
        if not match:
            raise ValueError(
                f"目标端口「{token}」无效。{cls._PORT_ERR_HINT}"
            )
        port_start = int(match.group(1))
        port_end = int(match.group(2)) if match.group(2) else port_start
        if not (1 <= port_start <= 65535):
            raise ValueError(f"端口号超出有效范围(1-65535): {port_start}")
        if not (1 <= port_end <= 65535):
            raise ValueError(f"端口号超出有效范围(1-65535): {port_end}")
        if port_start > port_end:
            raise ValueError(f"端口范围起始值不能大于结束值: {port_start}:{port_end}")
        return token
