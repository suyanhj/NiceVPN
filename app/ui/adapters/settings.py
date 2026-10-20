# -*- coding: utf-8 -*-
"""系统设置页的纯数据校验与配置构建。"""

from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from typing import Any, Mapping

from app.models.config import SystemConfig


_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?$")


def _base_dict(config: Mapping[str, Any] | Any) -> dict[str, Any]:
    if hasattr(config, "to_dict"):
        return dict(config.to_dict())
    if hasattr(config, "model_dump"):
        return dict(config.model_dump())
    return dict(config)


def validate_easyrsa_script_path(path: str) -> tuple[bool, str]:
    """校验 EasyRSA 可执行脚本路径。"""

    raw = str(path or "").strip()
    if not raw:
        return False, "请输入 EasyRSA 脚本路径"
    target = Path(raw)
    if not target.exists():
        return False, f"路径不存在: {raw}"
    if not target.is_file():
        return False, f"路径不是文件: {raw}"
    if not os.access(target, os.X_OK):
        return False, f"文件不可执行: {raw}"
    return True, "EasyRSA 路径有效"


def validate_server_host(value: str) -> str:
    """验证 OpenVPN 客户端 remote 使用的 IP/主机名。"""

    host = str(value or "").strip()
    if not host:
        raise ValueError("服务端地址必填")
    if any(ch.isspace() or ord(ch) < 32 for ch in host):
        raise ValueError("服务端地址不能包含空白或控制字符")
    if "://" in host or "/" in host:
        raise ValueError("服务端地址只填写 IP 或域名，不要包含协议或路径")

    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass

    if ":" in host:
        raise ValueError("IPv6 请填写合法地址；域名不能包含端口")
    if not _HOST_RE.fullmatch(host):
        raise ValueError("服务端地址格式无效")
    return host


def build_openvpn_path_config(
    base: Mapping[str, Any] | Any,
    *,
    openvpn_bin: str,
    easyrsa_bin: str,
) -> SystemConfig:
    """生成路径配置；路径存在性由页面验证器负责。"""

    data = _base_dict(base)
    ovpn = str(openvpn_bin or "").strip()
    easy = str(easyrsa_bin or "").strip()
    if not ovpn:
        raise ValueError("OpenVPN 可执行文件路径必填")
    if not easy:
        raise ValueError("EasyRSA 脚本路径必填")
    data["openvpn_bin"] = ovpn
    data["easyrsa_dir"] = easy
    return SystemConfig(**data)


def build_server_connection_config(
    base: Mapping[str, Any] | Any,
    *,
    server_ip: str,
    port: Any,
    proto: str,
    push_lan_routes: str | list[str],
    max_clients: Any,
) -> SystemConfig:
    """验证服务端 remote、push route 与 max-clients。"""

    data = _base_dict(base)
    data["server_ip"] = validate_server_host(server_ip)

    try:
        port_value = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError("端口必须为整数") from exc
    if not 1 <= port_value <= 65535:
        raise ValueError("端口范围必须为 1-65535")
    data["port"] = port_value

    proto_value = str(proto or "").strip().lower()
    if proto_value not in {"udp", "tcp"}:
        raise ValueError("协议仅支持 UDP 或 TCP")
    data["proto"] = proto_value

    data["push_lan_routes"] = push_lan_routes
    try:
        data["max_clients"] = int(max_clients)
    except (TypeError, ValueError) as exc:
        raise ValueError("max-clients 必须为整数") from exc

    return SystemConfig(**data)


def build_device_bind_config(
    base: Mapping[str, Any] | Any,
    *,
    mode: str,
) -> SystemConfig:
    """严格验证设备绑定策略，避免模型兼容逻辑静默回退。"""

    normalized = str(mode or "").strip()
    allowed = {"weak_log", "weak_fingerprint", "strict_hwaddr"}
    if normalized not in allowed:
        raise ValueError("设备绑定策略无效")
    data = _base_dict(base)
    data["device_bind_mode"] = normalized
    return SystemConfig(**data)


def build_global_ssh_config(
    base: Mapping[str, Any] | Any,
    *,
    private_key: str,
    passphrase: str,
) -> SystemConfig:
    """生成全局 SSH 配置；不在异常文本中拼接敏感内容。"""

    data = _base_dict(base)
    data["global_ssh_private_key"] = (
        str(private_key or "").strip() or None
    )
    data["global_ssh_private_key_passphrase"] = (
        str(passphrase or "").strip() or None
    )
    return SystemConfig(**data)
