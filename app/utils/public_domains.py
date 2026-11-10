# -*- coding: utf-8 -*-
"""Shared externally reachable addresses (HTTP/HTTPS, domain or IP:port).

The caller may select a host that is *on* this allow-list. We never
turn arbitrary Host/X-Forwarded-Host into an OAuth callback URL.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

_HOSTNAME = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9._-]{0,251}[a-zA-Z0-9])?$")


def domain_origin(entry: str) -> str:
    """A bare host defaults to HTTP; explicit http(s) scheme is preserved."""
    value = str(entry or "").strip()
    if not value or any(ch.isspace() or ord(ch) < 32 for ch in value):
        raise ValueError("访问地址不能为空或包含空白、控制字符")
    explicit = "://" in value
    parsed = urlsplit(value if explicit else "http://" + value)
    try:
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("访问地址的 IP、域名或端口格式无效") from exc
    if (parsed.scheme not in {"http", "https"} or not host
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)):
        raise ValueError("访问地址须为域名/IP[:端口] 或完整 http(s):// 地址，不含路径")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not _HOSTNAME.fullmatch(host):
            raise ValueError("访问地址包含无效主机名")
    return f"{parsed.scheme}://{parsed.netloc}"


def parse_domains(value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    """Store human-friendly domain entries, preserving priority/order."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        candidates = re.split(r"[,;\n]+", value)
    elif isinstance(value, (list, tuple)):
        candidates = [
            part for item in value for part in re.split(r"[,;\n]+", str(item))
        ]
    else:
        raise ValueError("访问地址须使用文本或地址列表")
    entries: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        item = str(item).strip()
        if not item:
            continue
        origin = domain_origin(item)
        if origin.lower() in seen:
            continue
        seen.add(origin.lower())
        entries.append(item)
    if len(entries) > 32:
        raise ValueError("全局访问地址最多配置 32 项")
    return entries


def first_domain_origin(domains: list[str] | None) -> str | None:
    for entry in domains or []:
        return domain_origin(entry)
    return None


def match_domain_origin(domains: list[str] | None, host: str | None,
                        scheme: str | None = None) -> str | None:
    """Allow-list match only; prefer request scheme for bare entries.

    The port is part of the host identity, so :8881 will not match :8880.
    """
    if not host or any(ch.isspace() or ord(ch) < 32 for ch in host):
        return None
    try:
        incoming = urlsplit("http://" + host)
        incoming_host, incoming_port = incoming.hostname, incoming.port
    except ValueError:
        return None
    if not incoming_host or incoming.username is not None or incoming.password is not None:
        return None
    for entry in domains or []:
        configured = urlsplit(domain_origin(entry))
        if (configured.hostname or "").casefold() != incoming_host.casefold():
            continue
        if configured.port != incoming_port:
            continue
        if "://" in entry:
            return domain_origin(entry)
        selected_scheme = scheme if scheme in {"http", "https"} else "http"
        return f"{selected_scheme}://{configured.netloc}"
    return None


def resolve_sso_origin(config) -> str:
    """Explicit SSO address > first global address; otherwise empty."""
    override = str(config.get("enterprise_sso_public_base_url") or "").strip()
    if override:
        return override.rstrip("/")
    return first_domain_origin(config.get("domains") or []) or ""
