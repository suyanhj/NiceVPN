# -*- coding: utf-8 -*-
"""CIDR 校验工具

子网/重叠等关系用第三方库 :mod:`netaddr` 处理；:mod:`ipaddress` 不再混用，避免双栈语义分裂。
`iptables` 碎片中的 ``-s``/``-d`` 后仍可能为主机名，须与「纯点分但非法」区分处保留极短正则（非地址解析器）。
"""

import re

from netaddr import IPAddress, IPNetwork, IPSet, AddrFormatError


def validate_cidr(s: str) -> bool:
    """校验字符串是否为合法的 **IPv4** CIDR（如 10.8.0.0/16）。

    无 ``/`` 前缀长度的一截地址（如 ``10.8.0.0``）不视为 CIDR，与 :mod:`ipaddress` 行为对齐。
    """
    t = (s or "").strip()
    if not t or "/" not in t:
        return False
    try:
        n = IPNetwork(t, version=4)
    except (AddrFormatError, TypeError, ValueError):
        return False
    return n.version == 4


def is_subnet_of(child: str, parent: str) -> bool:
    """判断 child 是否为 parent 的子网（或完全相同）。

    例：10.8.1.0/24 是 10.8.0.0/16 的子网。
    两个参数都必须是合法的 CIDR，否则返回 False。
    """
    try:
        c = IPNetwork(child, version=4)
        p = IPNetwork(parent, version=4)
    except (AddrFormatError, TypeError, ValueError):
        return False
    return c in p


def subnets_overlap(a: str, b: str) -> bool:
    """判断两个 CIDR 是否存在地址重叠。

    任意一方的地址范围与另一方有交集即视为重叠。
    两个参数都必须是合法的 CIDR，否则返回 False。
    """
    try:
        na, nb = IPNetwork(a, version=4), IPNetwork(b, version=4)
    except (AddrFormatError, TypeError, ValueError):
        return False
    return bool(IPSet([na]) & IPSet([nb]))


def validate_iptables_addr_or_cidr(token: str) -> None:
    """校验适于 ``iptables -s`` / ``-d`` 的单地址或 CIDR 字串。非法时抛出 ``ValueError``。

    使用 :class:`netaddr.IPAddress` / :class:`netaddr.IPNetwork`；支持可选前缀 ``!``（取反）。
    对无法解析为地址但形如点分十进制、且非合法 IP 的串（如 ``0.0.0.0.0``）显式拒绝；含字母的串视为主机名交由对端 ``iptables`` 解析。
    """
    t = (token or "").strip()
    if not t:
        raise ValueError("网络地址字串为空")
    if t.startswith("!"):
        t = t[1:].strip()
    if not t:
        raise ValueError("取反后网络地址字串为空")
    if " " in t or "\n" in t or "\r" in t or "\t" in t:
        raise ValueError("地址中不得含空白")
    if "/" in t:
        if not validate_cidr(t):
            raise ValueError(f"非法 CIDR: {t}")
        return
    try:
        IPAddress(t)
        return
    except (AddrFormatError, ValueError):
        pass
    if re.match(r"^[\d.]+$", t):
        raise ValueError(f"非法 IP: {t}")


def validate_iptables_rest_line_addr_tokens(rest: str) -> None:
    """校验 ``iptables`` 片段中 ``-s`` / ``-d`` 后的地址或 CIDR（与对端链写回前一致）。"""
    t = (rest or "").strip()
    if not t:
        raise ValueError("规则片段为空")
    for m in re.finditer(r"(?:^|\s)-[sd]\s+([^\s]+)", t, re.IGNORECASE):
        tok = m.group(1).strip()
        for p in (x.strip() for x in tok.split(",") if x.strip()):
            validate_iptables_addr_or_cidr(p)
