# -*- coding: utf-8 -*-
"""系统配置模型"""
import ipaddress
import re
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.constants import OPENVPN_DEFAULT_MAX_CLIENTS


class SystemConfig(BaseModel):
    """系统全局配置"""

    initialized: bool = Field(default=False, description="系统是否已初始化")
    global_subnet: Optional[str] = Field(default=None, description="全局子网 CIDR")
    openvpn_bin: Optional[str] = Field(default=None, description="OpenVPN 可执行文件路径")
    easyrsa_dir: Optional[str] = Field(default=None, description="EasyRSA 目录路径")
    pki_dir: Optional[str] = Field(default=None, description="PKI 目录路径")
    # OpenVPN 实例名 -> 展示用元数据（端口、协议、子网等）
    instances: dict[str, Any] = Field(default_factory=dict, description="已注册的 OpenVPN 实例")
    vpn_instance_id: Optional[str] = Field(
        default=None,
        description=(
            "本机 VPN 实例唯一标识，写入 iptables 注释 ``inst=…``，便于未来多机组网时区分本机与远端规则；"
            "留空则使用 instances 中实例名（单实例常用）或回退 server"
        ),
    )
    server_ip: Optional[str] = Field(default=None, description="客户端连接的服务端公网 IP 或域名")
    port: int = Field(default=1194, description="默认实例监听端口")
    proto: str = Field(default="udp", description="默认传输协议 udp/tcp")
    max_clients: int = Field(
        default=OPENVPN_DEFAULT_MAX_CLIENTS,
        ge=16,
        le=65534,
        description="OpenVPN 单实例 max-clients（并发隧道上限，须与机器与内核资源匹配）",
    )
    notify_enabled: bool = Field(
        default=False,
        description="是否启用「推送下载链接」到已选通知通道",
    )
    notify_provider: Literal["none", "dingtalk", "wework"] = Field(
        default="none",
        description="通知通道：none 未选；dingtalk 钉钉；wework 企业微信群机器人",
    )
    dingtalk_webhook: Optional[str] = Field(default=None, description="钉钉机器人 Webhook 地址")
    dingtalk_secret: Optional[str] = Field(
        default=None,
        description=(
            "钉钉机器人加签密钥（须与钉钉一致，一般以 SEC 开头）；"
            "dingtalkchatbot 仅对 SEC 前缀密钥做加签，未填则不加签"
        ),
    )
    wework_webhook: Optional[str] = Field(
        default=None,
        description="企业微信群机器人 Webhook（https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…）",
    )
    download_base_url: Optional[str] = Field(
        default=None,
        description=(
            "下载链接基础 URL；留空则按当前浏览器访问地址生成，"
            "localhost 访问时用 Web 启动时解析的本机网卡地址（优先 eth0，与 NiceGUI 欢迎语同源）"
        ),
    )
    github_proxy_urls: list[str] = Field(
        default_factory=lambda: [
            "https://gh-proxy.org/",
            "https://cdn.gh-proxy.org/",
            "https://gh.llkk.cc/",
        ],
        description="GitHub 下载代理前缀列表",
    )
    push_lan_routes: list[str] = Field(
        default_factory=list,
        description=(
            "推送给客户端的 IPv4 内网 CIDR（每行一条），须按实际服务端局域网填写，例如 172.16.22.0/24；"
            "可多行多条。不推 redirect-gateway，仅这些网段走 VPN，其余仍走客户端默认上网路由。"
        ),
    )
    masquerade_out_interfaces: list[str] = Field(
        default_factory=list,
        description="已废弃：中心侧改为按 global_subnet 自动 MASQUERADE，此项不再读取，仅保留兼容旧配置 JSON",
    )
    device_bind_mode: Literal["weak_log", "weak_fingerprint", "strict_hwaddr"] = Field(
        default="weak_fingerprint",
        description=(
            "client-connect 设备策略：weak_log=仅写日志不拒绝；"
            "weak_fingerprint=全体IV_HWADDR；退化：iOS/Mac/Win用UV_UUID，安卓IV_PLAT|IV_PLAT_VER，其它IV_PLAT_VER|IV_GUI_VER；"
            "strict_hwaddr=必须 IV_HWADDR，无则拒绝（OpenVPN 2.x 核心安卓端常不兼容）"
        ),
    )
    global_ssh_private_key: Optional[str] = Field(
        default=None,
        description="对端未单独配置 SSH 私钥时使用的全局 PEM；与对端字段同落 data 配置，须控制文件权限",
    )
    global_ssh_private_key_passphrase: Optional[str] = Field(
        default=None,
        description="全局 SSH 私钥加密口令；无私钥加密可留空",
    )
    created_at: Optional[str] = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="创建时间(ISO8601)",
    )
    updated_at: Optional[str] = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="更新时间(ISO8601)",
    )

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_dingtalk_notify(cls, data: Any) -> Any:
        """旧版仅配置 Webhook、无 notify_* 字段时：视为已启用钉钉通道。"""
        if not isinstance(data, dict):
            return data
        d = dict(data)
        has_new = "notify_enabled" in d or "notify_provider" in d
        webhook = str(d.get("dingtalk_webhook") or "").strip()
        if not has_new and webhook:
            d["notify_enabled"] = True
            d["notify_provider"] = "dingtalk"
        return d

    @field_validator("device_bind_mode", mode="before")
    @classmethod
    def normalize_device_bind_mode(cls, value):
        """兼容旧配置或非法值，回退为 weak_fingerprint。"""
        allowed = frozenset({"weak_log", "weak_fingerprint", "strict_hwaddr"})
        if value is None or value == "":
            return "weak_fingerprint"
        s = str(value).strip()
        return s if s in allowed else "weak_fingerprint"

    @field_validator("global_subnet")
    @classmethod
    def validate_global_subnet(cls, value: Optional[str]) -> Optional[str]:
        """校验全局子网 CIDR 格式与前缀长度（≥/16）"""
        if value is None:
            return value
        try:
            net = ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            raise ValueError(f"无效的 CIDR 格式: {value}") from exc
        if net.prefixlen < 16:
            raise ValueError(
                f"全局子网前缀 /{net.prefixlen} 过大，仅允许 /16～/30"
            )
        return value

    @field_validator("push_lan_routes", mode="before")
    @classmethod
    def parse_push_lan_routes(cls, value):
        """兼容多行字符串或列表。"""
        if value is None:
            return []
        if isinstance(value, str):
            return [line.strip() for line in value.splitlines() if line.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    @field_validator("push_lan_routes", mode="after")
    @classmethod
    def validate_push_lan_routes(cls, value: list[str]) -> list[str]:
        """校验为合法 IPv4 CIDR，并规范化为字符串形式。"""
        out: list[str] = []
        for item in value:
            try:
                net = ipaddress.ip_network(item, strict=False)
            except ValueError as exc:
                raise ValueError(f"无效的 push_lan_routes 项: {item}") from exc
            if net.version != 4:
                raise ValueError(f"push_lan_routes 仅支持 IPv4: {item}")
            out.append(str(net))
        return out

    @field_validator("masquerade_out_interfaces", mode="before")
    @classmethod
    def parse_masquerade_ifaces(cls, value):
        """兼容多行字符串或列表。"""
        if value is None:
            return []
        if isinstance(value, str):
            return [line.strip() for line in value.splitlines() if line.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    @field_validator("masquerade_out_interfaces", mode="after")
    @classmethod
    def validate_masquerade_ifaces(cls, value: list[str]) -> list[str]:
        """网卡名仅允许常见字符，避免注入 shell/iptables 参数。"""
        pat = re.compile(r"^[a-zA-Z0-9._-]+$")
        for name in value:
            if not pat.match(name):
                raise ValueError(f"非法网卡名: {name}")
        return value

    @field_validator("github_proxy_urls", mode="before")
    @classmethod
    def validate_github_proxy_urls(cls, value):
        """兼容字符串或列表形式的 GitHub 代理配置。"""
        default_values = [
            "https://gh-proxy.org/",
            "https://cdn.gh-proxy.org/",
            "https://gh.llkk.cc/",
        ]
        if value is None:
            return default_values
        if isinstance(value, str):
            lines = [line.strip() for line in value.splitlines()]
            return [line for line in lines if line] or default_values
        if isinstance(value, list):
            result: list[str] = []
            for item in value:
                text = str(item).strip()
                if text:
                    result.append(text)
            return result or default_values
        return value
