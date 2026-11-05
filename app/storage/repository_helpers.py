# -*- coding: utf-8 -*-
"""Shared SQLite value normalization and query helpers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Sequence

from app.utils.cidr import normalize_cidr


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump_payload(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _load_payload(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("SQLite payload 必须为 JSON object")
    return value


def _like_pattern(value: object, *, mode: str = "contains") -> str:
    """生成带 ESCAPE '\\' 的 LIKE 参数。"""

    text = str(value or "")
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    if mode == "equals":
        return escaped
    if mode == "startsWith":
        return f"{escaped}%"
    if mode == "endsWith":
        return f"%{escaped}"
    return f"%{escaped}%"


def _append_multi_text_clause(
    clauses: list[str],
    params: list[Any],
    expression: str,
    values: Sequence[str] | None,
    *,
    match_type: str = "contains",
    operator: str = "AND",
) -> None:
    """把标签式多文本条件安全追加到 SQL WHERE。"""

    normalized = [str(value).strip() for value in (values or []) if str(value).strip()]
    if not normalized:
        return

    mode = str(match_type or "contains")
    joiner = " OR " if str(operator or "AND").upper() == "OR" else " AND "
    fragments: list[str] = []
    for value in normalized:
        if mode in {"equals", "notEqual"}:
            sql_operator = "=" if mode == "equals" else "<>"
            fragments.append(f"{expression} {sql_operator} ? COLLATE NOCASE")
            params.append(value)
            continue
        sql_operator = "NOT LIKE" if mode == "notContains" else "LIKE"
        fragments.append(f"{expression} {sql_operator} ? ESCAPE '\\'")
        params.append(
            _like_pattern(
                value,
                mode="contains" if mode == "notContains" else mode,
            )
        )
    clauses.append("(" + joiner.join(fragments) + ")")


def _normalize_group_payload(data: dict[str, Any]) -> dict[str, Any]:
    """规范组 subnet；合法但非网络地址的历史 CIDR 自动转成网络地址。"""

    payload = dict(data)
    raw_subnet = str(payload.get("subnet") or "").strip()
    if raw_subnet:
        normalized = normalize_cidr(raw_subnet)
        if normalized is not None:
            payload["subnet"] = normalized
    return payload


def _peer_index_values(data: dict[str, Any]) -> tuple[Any, ...]:
    lan_cidrs = [
        str(value).strip()
        for value in (data.get("lan_cidrs") or [])
        if str(value).strip()
    ]
    return (
        str(data.get("name") or ""),
        1 if lan_cidrs else 0,
        ",".join(lan_cidrs),
        str(data.get("center_forward_protocol") or "all").strip().lower() or "all",
        str(data.get("center_forward_dest_ip") or "").strip(),
        str(data.get("center_forward_dest_port") or "").strip(),
    )


def _firewall_index_values(data: dict[str, Any]) -> tuple[Any, ...]:
    source_subnet = str(data.get("source_subnet") or "").strip()
    source_ips = [
        str(value).strip()
        for value in (data.get("source_ips") or [])
        if str(value).strip()
    ]
    source_text = source_subnet or ",".join(source_ips)
    return (
        str(data.get("protocol") or "all").strip().lower() or "all",
        str(data.get("action") or "accept").strip().lower() or "accept",
        str(data.get("deployment_target") or "center").strip().lower() or "center",
        source_text,
        str(data.get("dest_ip") or "").strip(),
        str(data.get("dest_port") or "").strip(),
    )


