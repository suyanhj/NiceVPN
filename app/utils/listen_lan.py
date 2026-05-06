# -*- coding: utf-8 -*-
"""
Web 监听启动时解析本机 IPv4，与 NiceGUI 欢迎语同源（ifaddr），供下载链接在 localhost 访问时回退。

优先顺序：eth0（含 eth0:n）首个非回环 IPv4 → 排除虚拟网卡（docker0/br-/veth 等）及 172.17.0.0/16 后的私网地址。
"""
from __future__ import annotations

import ipaddress
import logging
from typing import Optional

import ifaddr

logger = logging.getLogger(__name__)

# 由 set_listen_http_base 在进程内写入；与监听端口一致
_cached_base: Optional[str] = None


def get_listen_http_base() -> Optional[str]:
    """返回启动时缓存的 http(s)://host[:port]，无则 None。"""
    return _cached_base


def _is_eth0_family(name: str) -> bool:
    return name == "eth0" or name.startswith("eth0:")


def _virtual_iface_skip(name: str) -> bool:
    n = name.lower()
    return (
        n.startswith("docker")
        or n.startswith("br-")
        or n.startswith("veth")
        or n.startswith("virbr")
    )


def pick_preferred_listen_ipv4() -> Optional[str]:
    """
    选取用于展示下载链接的本机 IPv4（非 VPN 公网配置）。

    Returns:
        点分 IPv4 字符串；无法解析时 None
    """
    adapters = list(ifaddr.get_adapters())
    for adapter in adapters:
        if not _is_eth0_family(adapter.name):
            continue
        for ip in adapter.ips:
            if not ip.is_IPv4:
                continue
            s = str(ip.ip)
            if s == "127.0.0.1":
                continue
            try:
                addr = ipaddress.ip_address(s)
            except ValueError:
                continue
            if addr.is_loopback:
                continue
            logger.info("下载链接优先使用 eth0 地址: %s", s)
            return s

    candidates: list[tuple[str, str]] = []
    for adapter in adapters:
        if _virtual_iface_skip(adapter.name):
            continue
        for ip in adapter.ips:
            if not ip.is_IPv4:
                continue
            s = str(ip.ip)
            if s == "127.0.0.1":
                continue
            try:
                addr = ipaddress.ip_address(s)
            except ValueError:
                continue
            if addr.is_loopback:
                continue
            if addr in ipaddress.ip_network("172.17.0.0/16"):
                continue
            candidates.append((adapter.name, s))

    def priv_key(item: tuple[str, str]) -> tuple[int, str]:
        a = ipaddress.ip_address(item[1])
        return (0 if a.is_private else 1, item[1])

    candidates.sort(key=priv_key)
    if candidates:
        name, s = candidates[0]
        logger.info("下载链接使用网卡 %s 地址: %s", name, s)
        return s
    return None


def _format_listen_netloc(host: str, port: int, scheme: str) -> str:
    default_port = 443 if scheme == "https" else 80
    is_v6 = ":" in host and "." not in host
    h = f"[{host}]" if is_v6 else host
    if port and port != default_port:
        return f"{h}:{port}"
    return h


def set_listen_http_base(scheme: str, bind_host: str, port: int) -> None:
    """
    在 ui.run 前调用：根据绑定地址与本机网卡生成下载链接用的基础 URL（无 path）。

    - 绑定 0.0.0.0 或 :: 时，用 pick_preferred_listen_ipv4 + port。
    - 绑定具体 IP 时，直接使用该 IP（管理员明确监听面）。
    """
    global _cached_base
    _cached_base = None
    host = (bind_host or "").strip()
    scheme = (scheme or "http").lower().split(":")[0] or "http"

    if host in ("0.0.0.0", "::", ""):
        ip_txt = pick_preferred_listen_ipv4()
        if not ip_txt:
            logger.warning(
                "未解析到本机可用 IPv4，通过 localhost 打开管理页时需在设置中填写下载基础 URL"
            )
            return
        netloc = _format_listen_netloc(ip_txt, port, scheme)
        _cached_base = f"{scheme}://{netloc}".rstrip("/")
        logger.info("下载链接监听快照: %s", _cached_base)
        return

    if host in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "Web 仅监听回环地址，无法自动生成局域网下载链接，请在设置中填写下载基础 URL"
        )
        return

    netloc = _format_listen_netloc(host.strip("[]"), port, scheme)
    _cached_base = f"{scheme}://{netloc}".rstrip("/")
    logger.info("下载链接使用绑定地址: %s", _cached_base)
