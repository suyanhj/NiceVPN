# -*- coding: utf-8 -*-
"""对端站点实例（组网）模型"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.utils.cidr import validate_cidr


class PeerInstance(BaseModel):
    """对端 VPN 客户端站点：绑定系统用户 + 后方内网 CIDR，供 CCD iroute 与中心 VPN_PEER 使用。"""

    id: str = Field(..., description="对端实例 UUID")
    name: str = Field(..., description="展示名称")
    bound_username: str = Field(..., description="VPN 用户名（CCD 文件名）；同一用户名全局仅能绑定一个对端实例")
    lan_cidrs: list[str] = Field(default_factory=list, description="对端后方内网 CIDR，可多段")
    center_forward_priority: int = Field(
        default=500_000,
        ge=1,
        description="仅用于中心本机 VPN_FORWARD 与 JSON 规则混排时的 priority；对端主机上策略由 SSH 下发，独立于此字段",
    )
    center_forward_enabled: bool = Field(
        default=True,
        description="是否在中心本机合并该对端 LAN 的 VPN_FORWARD 放行；为 False 时本机不插入该源、不删 lan_cidrs 配置",
    )
    center_forward_dest_ip: str = Field(
        default="",
        description="中心侧 VPN_FORWARD 匹配目标地址，逗号分隔，空表示不限；与 JSON 中心规则 dest_ip 语义一致",
    )
    center_forward_dest_port: str = Field(
        default="",
        description="中心侧目标端口，空表示不限；格式同防火墙规则 dest_port",
    )
    center_forward_protocol: str = Field(
        default="all",
        description="中心侧匹配协议：all/tcp/udp/icmp 等，与 JSON 规则 protocol 一致",
    )
    center_forward_rule_description: str = Field(
        default="",
        description="写入本机 iptables -m comment 的说明；空则使用系统默认 tag（ovpn-mgmt-center-peer…）",
    )
    mesh_route_visible_group_ids: list[str] = Field(
        default_factory=list,
        description="mesh push 路由仅对这些组用户下发本对端 LAN；空列表表示对所有活跃用户下发",
    )
    ssh_host: str = Field(default="", description="SSH 管理主机（自动/手动后续使用）")
    ssh_port: int = Field(default=22, ge=1, le=65535, description="SSH 端口")
    ssh_username: str = Field(default="", description="SSH 登录用户名（远端 OpenVPN 客户端所在主机）")
    ssh_auth: Literal["password", "key", "none"] = Field(default="none", description="SSH 认证方式占位")
    ssh_password: str = Field(
        default="",
        description="SSH 密码（落库于对端 JSON，请限制 data 目录权限并做好备份策略）",
    )
    ssh_private_key: str = Field(
        default="",
        description="SSH 私钥 PEM 全文（落库；可来自粘贴或上传文件）",
    )
    ssh_private_key_passphrase: str = Field(
        default="",
        description="私钥加密口令（落库；无私钥加密可留空）",
    )
    masquerade_on_peer: bool = Field(
        default=False,
        description="是否在对端侧通过 SSH 写入 POSTROUTING MASQUERADE",
    )
    auto_install_on_peer: bool = Field(default=False, description="创建后是否自动 SSH 装配对端")
    ssh_openvpn_binary: str = Field(
        default="",
        description="远端 OpenVPN 可执行文件绝对路径（可选）；非空时 SSH 探测优先该路径，再回退标准列表与 command -v",
    )
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(), description="创建时间 ISO8601")
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat(), description="更新时间 ISO8601")

    @field_validator("mesh_route_visible_group_ids")
    @classmethod
    def validate_mesh_route_visible_group_ids(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in v or []:
            s = str(raw).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out

    @field_validator("center_forward_dest_ip", "center_forward_dest_port", "center_forward_rule_description")
    @classmethod
    def _strip_opt_peer_fw(cls, v: str) -> str:
        return str(v or "").strip()

    @field_validator("center_forward_protocol")
    @classmethod
    def _center_proto(cls, v: str) -> str:
        s = str(v or "").strip().lower()
        return s or "all"

    @field_validator("lan_cidrs")
    @classmethod
    def validate_lan_cidrs(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for raw in v or []:
            s = str(raw).strip()
            if not s:
                continue
            if not validate_cidr(s):
                raise ValueError(f"无效的内网 CIDR: {s}")
            out.append(s)
        return out

    @field_validator("ssh_username")
    @classmethod
    def validate_ssh_username(cls, v: str) -> str:
        return str(v or "").strip()

    @field_validator("ssh_openvpn_binary")
    @classmethod
    def validate_ssh_openvpn_binary(cls, v: str) -> str:
        return str(v or "").strip()

    @field_validator("bound_username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        s = str(v or "").strip()
        if not s:
            raise ValueError("bound_username 不能为空")
        return s
