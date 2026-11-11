# -*- coding: utf-8 -*-
"""Unified DingTalk allow-list; verified CorpId wins over verified organization name."""
from __future__ import annotations

import re

_CORP_ID = re.compile(r"^[A-Za-z0-9_-]{2,128}$")


def parse_allowed_enterprises(value) -> list[dict[str, str]]:
    """Accept entries with CorpId, organization name, or both.

    Legacy text entries still use 'CorpId | optional name'; name-only text
    entries use '| 企业名称'. Admin UI uses separate input fields.
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
        raise ValueError("企业白名单必须是企业记录列表或每行 CorpId | 企业名称")
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("企业白名单格式无效")
        corp_id = str(item.get("corp_id") or "").strip()
        name = str(item.get("name") or "").strip()
        if corp_id and not _CORP_ID.fullmatch(corp_id):
            raise ValueError("企业 CorpId 格式无效")
        if not corp_id and not name:
            raise ValueError("企业 CorpId 和企业名称至少填写一项")
        if len(name) > 80 or any(ord(c) < 32 for c in name):
            raise ValueError("企业名称无效")
        identifier = ("id", corp_id) if corp_id else ("name", name)
        if identifier in seen:
            raise ValueError(f"企业重复: {corp_id or name}")
        seen.add(identifier)
        out.append({"corp_id": corp_id, "name": name})
    if len(out) > 128:
        raise ValueError("最多允许 128 个企业")
    return out


def is_allowed_enterprise(config, corp_id: str, enterprise_name: str = "") -> bool:
    """Match a server-verified identity; CorpId on an entry overrides its name."""
    if not corp_id:
        return False
    entries = config.get("enterprise_sso_allowed_corps") or []
    if any(item.get("corp_id") and item["corp_id"] == corp_id for item in entries):
        return True
    return bool(enterprise_name) and any(
        not item.get("corp_id") and item.get("name") == enterprise_name
        for item in entries
    )


def needs_enterprise_name_lookup(config) -> bool:
    """Only name-only entries justify an extra DingTalk organization API call."""
    return any(
        not item.get("corp_id") and item.get("name")
        for item in (config.get("enterprise_sso_allowed_corps") or [])
    )


def format_allowed_enterprises(entries) -> str:
    return "\n".join(
        f'{item.get("corp_id") or ""} | {item.get("name") or ""}'.strip().rstrip(" |")
        for item in entries or []
    )
