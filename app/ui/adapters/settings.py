# -*- coding: utf-8 -*-
"""系统设置页的纯数据校验与配置构建。"""

from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

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


def _normalize_http_url(
    value: str,
    *,
    label: str,
    allow_blank: bool = False,
    allow_query: bool = False,
) -> str | None:
    text = str(value or "").strip()
    if not text:
        if allow_blank:
            return None
        raise ValueError(f"{label}不能为空")
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{label}须为 http/https 完整 URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{label}不能包含 URL 用户名或密码")
    if (parsed.query and not allow_query) or parsed.fragment:
        suffix = "fragment" if allow_query else "query 或 fragment"
        raise ValueError(f"{label}不能包含 {suffix}")
    return text.rstrip("/")


def parse_github_proxy_urls(text: str) -> list[str]:
    """按输入顺序校验并规范化 GitHub 代理前缀。"""

    result: list[str] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(
        str(text or "").splitlines(),
        start=1,
    ):
        value = raw.strip()
        if not value:
            continue
        try:
            normalized = _normalize_http_url(
                value,
                label=f"第 {line_number} 行代理",
            )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        assert normalized is not None
        prefix = normalized + "/"
        if prefix not in seen:
            seen.add(prefix)
            result.append(prefix)
    if not result:
        raise ValueError("至少配置一个 GitHub 下载代理")
    return result


def build_github_candidate_urls(
    source_url: str,
    proxies: list[str],
) -> list[str]:
    """使用当前表单代理列表构造候选，顺序与安装器一致。"""

    url = str(source_url or "").strip()
    if not url:
        raise ValueError("源下载 URL 不能为空")
    normalized = [
        str(prefix).strip().rstrip("/") + "/"
        for prefix in proxies
        if str(prefix).strip()
    ]
    if any(url.startswith(prefix) for prefix in normalized):
        return [url]
    if not any(
        marker in url
        for marker in (
            "github.com",
            "githubusercontent.com",
            "api.github.com",
        )
    ):
        return [url]

    candidates = [prefix + url for prefix in normalized]
    candidates.append(url)
    return list(dict.fromkeys(candidates))


def normalize_download_base_url(value: str) -> str | None:
    """校验固定下载基址；空值表示继续按当前请求自动解析。"""

    text = str(value or "").strip()
    if not text:
        return None
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("下载基础 URL 须为 http/https 完整 URL")
    if parsed.username or parsed.password:
        raise ValueError("下载基础 URL 不能包含 URL 用户名或密码")
    if parsed.query or parsed.fragment:
        raise ValueError("下载基础 URL 不能包含 query 或 fragment")
    return text.rstrip("/")


def build_download_config(
    base: Mapping[str, Any] | Any,
    *,
    download_base_url: str,
) -> SystemConfig:
    data = _base_dict(base)
    data["download_base_url"] = normalize_download_base_url(
        download_base_url
    )
    return SystemConfig(**data)


def parse_rocketchat_channels(value: str | list[str] | None) -> str | list[str] | None:
    """把换行/逗号分隔频道规范化，单个频道保存为字符串。"""

    if value is None:
        return None
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = re.split(r"[\n,]+", str(value))
    channels = [
        str(item).strip()
        for item in raw_items
        if str(item).strip()
    ]
    channels = list(dict.fromkeys(channels))
    if not channels:
        return None
    return channels[0] if len(channels) == 1 else channels


def build_notify_config(
    base: Mapping[str, Any] | Any,
    *,
    enabled: bool,
    provider: str,
    dingtalk_webhook: str,
    dingtalk_secret: str,
    wework_webhook: str,
    rocketchat_webhook: str,
    rocketchat_channel: str | list[str] | None,
    rocketchat_username: str,
    rocketchat_icon_emoji: str,
    rocketchat_title: str,
) -> SystemConfig:
    """构建三通道通知配置，并在启用时验证当前通道必填项。"""

    normalized_provider = str(provider or "none").strip().lower()
    allowed = {"none", "dingtalk", "wework", "rocketchat"}
    if normalized_provider not in allowed:
        raise ValueError("通知通道无效")
    if enabled and normalized_provider == "none":
        raise ValueError("启用通知时必须选择通知通道")

    ding = _normalize_http_url(
        dingtalk_webhook,
        label="钉钉 Webhook",
        allow_blank=True,
        allow_query=True,
    )
    wework = _normalize_http_url(
        wework_webhook,
        label="企业微信 Webhook",
        allow_blank=True,
        allow_query=True,
    )
    rocket = _normalize_http_url(
        rocketchat_webhook,
        label="Rocket.Chat Webhook",
        allow_blank=True,
        allow_query=True,
    )
    channels = parse_rocketchat_channels(rocketchat_channel)

    if enabled and normalized_provider == "dingtalk" and not ding:
        raise ValueError("启用钉钉通知时必须填写 Webhook")
    if enabled and normalized_provider == "wework" and not wework:
        raise ValueError("启用企业微信通知时必须填写 Webhook")
    if enabled and normalized_provider == "rocketchat":
        if not rocket:
            raise ValueError("启用 Rocket.Chat 通知时必须填写 Webhook")
        if not channels:
            raise ValueError("启用 Rocket.Chat 通知时必须填写频道")

    data = _base_dict(base)
    data.update(
        {
            "notify_enabled": bool(enabled),
            "notify_provider": normalized_provider,
            "dingtalk_webhook": ding,
            "dingtalk_secret": (
                str(dingtalk_secret or "").strip() or None
            ),
            "wework_webhook": wework,
            "rocketchat_webhook": rocket,
            "rocketchat_channel": channels,
            "rocketchat_username": (
                str(rocketchat_username or "").strip()
                or "VPN管理"
            ),
            "rocketchat_icon_emoji": (
                str(rocketchat_icon_emoji or "").strip()
                or ":lock:"
            ),
            "rocketchat_title": (
                str(rocketchat_title or "").strip()
                or "OpenVPN"
            ),
        }
    )
    return SystemConfig(**data)
