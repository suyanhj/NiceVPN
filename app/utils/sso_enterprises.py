# -*- coding: utf-8 -*-
"""Unified DingTalk corporate allow-list. Names are for display; CorpId is authoritative."""
from __future__ import annotations

import re

_CORP_ID = re.compile(r"^[A-Za-z0-9_-]{2,128}$")


def parse_allowed_enterprises(value) -> list[dict[str, str]]:
    """Accept config list or admin multiline 'CorpId | optional name'.

    Never grant access based on display names or browser-provided corp IDs.
    """
    if value is None or value == "":
        return []
    if isinstance(value, str):
        items = []
        for line in value.splitlines():
            line = line.strip()
            if not line:
                continue
            corp_id, separator, name = line.partition("|")
            items.append({"corp_id": corp_id.strip(), "name": name.strip() if separator else ""})
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ValueError("企业白名单必须是列表或每行 CorpId | 企业名称")
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("企业白名单格式必须为 CorpId | 企业名称")
        corp_id = str(item.get("corp_id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not _CORP_ID.fullmatch(corp_id):
            raise ValueError("企业 CorpId 格式无效")
        if len(name) > 80 or any(ord(c) < 32 for c in name):
            raise ValueError("企业名称无效")
        if corp_id in seen:
            raise ValueError(f"企业 CorpId 重复: {corp_id}")
        seen.add(corp_id)
        out.append({"corp_id": corp_id, "name": name})
    if len(out) > 128:
        raise ValueError("最多允许 128 个企业")
    return out


def is_allowed_enterprise(config, corp_id: str) -> bool:
    """Exact comparison only; never allow blank or wildcard values."""
    if not corp_id:
        return False
    entries = config.get("enterprise_sso_allowed_corps") or []
    return any(item.get("corp_id") == corp_id for item in entries)


def format_allowed_enterprises(entries) -> str:
    return "\n".join(
        f'{item["corp_id"]} | {item.get("name") or ""}'.rstrip(" |")
        for item in entries or []
    )
