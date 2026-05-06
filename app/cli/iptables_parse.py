# -*- coding: utf-8 -*-
"""
将单行 iptables 命令解析为 FirewallRule 可用的字段子集。

仅支持 filter 表、常见匹配项；MASQUERADE/SNAT 等 nat 目标跳过。
"""
from __future__ import annotations

import ipaddress
import logging
import re
import shlex
from typing import Any

logger = logging.getLogger(__name__)

_SKIP_JUMPS = frozenset(
    {
        "MASQUERADE",
        "SNAT",
        "DNAT",
        "LOG",
        "MARK",
        "REDIRECT",
    }
)


def _normalize_source_cidr(value: str) -> str:
    """单 IP 补 /32。"""
    s = value.strip()
    if "/" in s:
        return s
    try:
        ipaddress.ip_address(s)
        return f"{s}/32"
    except ValueError:
        return s


def _find_after(parts: list[str], *flags: str) -> str | None:
    """在 parts 中找首个 flag 后的参数值。"""
    for j in range(len(parts) - 1):
        if parts[j] in flags:
            return parts[j + 1]
    return None


def parse_iptables_line(line: str) -> dict[str, Any] | None:
    """
    解析一行完整 iptables 命令（或从 -A 开始的片段）。

    Returns:
        可合并进 FirewallRule 的字段 dict；无法解析时返回 None。
    """
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None

    if not raw.startswith("iptables"):
        if raw.startswith("-A ") or raw.startswith("--append "):
            raw = "iptables " + raw
        else:
            logger.warning("跳过非 iptables 行: %s", raw[:80])
            return None

    try:
        parts = shlex.split(raw)
    except ValueError as e:
        logger.warning("shell 解析失败: %s — %s", raw[:80], e)
        return None

    if not parts or parts[0] != "iptables":
        return None

    table = "filter"
    if "-t" in parts:
        ti = parts.index("-t")
        if ti + 1 < len(parts):
            table = parts[ti + 1]

    chain = _find_after(parts, "-A", "--append")
    jump = _find_after(parts, "-j", "--jump")
    source = _find_after(parts, "-s", "--source")
    dest = _find_after(parts, "-d", "--destination")
    proto = _find_after(parts, "-p", "--protocol")
    dport = _find_after(parts, "--dport", "--destination-port")
    if dport is None:
        dport = _find_after(parts, "--dports")

    if table != "filter":
        logger.info("跳过非 filter 表: %s", raw[:80])
        return None
    if not chain:
        logger.warning("未找到 -A CHAIN: %s", raw[:80])
        return None
    if not jump:
        logger.warning("未找到 -j 目标: %s", raw[:80])
        return None
    if jump in _SKIP_JUMPS:
        logger.info("跳过 nat/诊断类目标 %s: %s", jump, raw[:80])
        return None

    ju = jump.upper()
    if ju == "ACCEPT":
        action = "accept"
    elif ju == "DROP":
        action = "drop"
    elif ju == "REJECT":
        action = "reject"
    else:
        logger.warning("暂不支持的 -j %s: %s", jump, raw[:80])
        return None

    out: dict[str, Any] = {
        "action": action,
        "instance": "server",
        "enabled": True,
        "description": f"cli-iptables {chain} {raw[:200]}",
        "_chain": chain,
        "_raw": raw,
    }

    if source:
        out["source_subnet"] = _normalize_source_cidr(source)
    if dest:
        out["dest_ip"] = dest.strip()
    if dport:
        out["dest_port"] = dport.strip().replace(" ", "")
    if proto:
        p = proto.strip().lower()
        out["protocol"] = "all" if p == "0" else p
    else:
        out["protocol"] = "all"

    return out


def iter_iptables_file(path: str) -> list[dict[str, Any]]:
    """读取文件，逐行解析。"""
    from pathlib import Path

    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")

    results: list[dict[str, Any]] = []
    for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parsed = parse_iptables_line(line)
        if parsed:
            results.append(parsed)
            continue
        if re.match(r"^-A\s+\w+", line):
            parsed = parse_iptables_line("iptables " + line)
            if parsed:
                results.append(parsed)
                continue
        logger.warning("第 %d 行未解析为可导入规则，已跳过: %s", lineno, line[:100])

    return results
