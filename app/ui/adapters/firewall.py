# -*- coding: utf-8 -*-
"""防火墙中心策略纯展示数据适配。"""

from __future__ import annotations

from typing import Any, Mapping

from app.services.firewall.rule_service import CE_PEER_RULE_PREFIX


_ACTION_META = {
    "accept": ("ACCEPT", "success"),
    "drop": ("DROP", "failed"),
    "reject": ("REJECT", "warning"),
}


def _join_source(rule: Mapping[str, Any]) -> str:
    subnet = str(rule.get("source_subnet") or "").strip()
    if subnet:
        return subnet
    ips = [
        str(value).strip()
        for value in (rule.get("source_ips") or [])
        if str(value).strip()
    ]
    return ",".join(ips) if ips else "*"


def _normalize_protocol(value: object) -> str:
    text = str(value or "all").strip().lower()
    if text in {"", "any"}:
        text = "all"
    return text.upper()


def adapt_center_rule_row(
    rule: Mapping[str, Any],
    *,
    group_names: Mapping[str, str] | None = None,
    peer_names: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """把普通中心 JSON 规则或中心侧对端虚拟行转换为稳定 Grid 行模型。"""

    groups = group_names or {}
    peers = peer_names or {}

    rule_id = str(rule.get("id") or "").strip()
    row_kind = str(rule.get("_row_kind") or "json").strip() or "json"
    peer_id = str(rule.get("_peer_id") or "").strip()
    if not peer_id and rule_id.startswith(CE_PEER_RULE_PREFIX):
        peer_id = rule_id[len(CE_PEER_RULE_PREFIX):].strip()
        row_kind = "center_peer"

    owner_type = str(rule.get("owner_type") or "").strip() or (
        "peer" if row_kind == "center_peer" else "group"
    )
    owner_id = str(
        rule.get("_owner_id")
        or rule.get("owner_id")
        or peer_id
        or ""
    ).strip()

    if owner_type == "group":
        owner_label = str(groups.get(owner_id) or owner_id or "—")
    elif owner_type == "peer" or row_kind == "center_peer":
        peer_name = str(peers.get(peer_id or owner_id) or "").strip()
        if peer_name and (peer_id or owner_id):
            owner_label = f"{peer_name} ({(peer_id or owner_id)[:8]})"
        else:
            owner_label = peer_name or peer_id or owner_id or "—"
    else:
        owner_label = owner_id or "—"

    action = str(rule.get("action") or "accept").strip().lower()
    action_label, action_tone = _ACTION_META.get(
        action,
        (action.upper() or "UNKNOWN", "unknown"),
    )
    protocol = _normalize_protocol(rule.get("protocol"))
    source = _join_source(rule)
    dest = str(rule.get("dest_ip") or "").strip() or "*"
    port = str(rule.get("dest_port") or "").strip() or "*"
    enabled = bool(rule.get("enabled", True))
    priority = int(rule.get("priority") or 0)
    deployment_target = str(rule.get("deployment_target") or "center").strip() or "center"

    if row_kind == "center_peer":
        kind_label = "对端·中心放行"
    elif deployment_target == "peer":
        kind_label = "对端仅标识"
    else:
        kind_label = "中心节点"

    description = str(rule.get("description") or "").strip()
    center_tag = str(rule.get("_center_tag_line") or "").strip()
    if row_kind == "center_peer" and description and center_tag:
        detail = f"{description} | {center_tag}"
    else:
        detail = description or center_tag

    rule_expression = f"{source} → {dest}:{port}"
    search_text = " ".join(
        [
            rule_id,
            owner_type,
            owner_id,
            owner_label,
            kind_label,
            protocol,
            action_label,
            source,
            dest,
            port,
            detail,
            str(priority),
            "启用" if enabled else "停用",
        ]
    ).casefold()

    return {
        "id": rule_id,
        "row_kind": row_kind,
        "peer_id": peer_id,
        "owner_type": owner_type,
        "owner_id": owner_id,
        "owner_label": owner_label,
        "kind_label": kind_label,
        "deployment_target": deployment_target,
        "enabled": enabled,
        "status_label": "启用" if enabled else "停用",
        "status_tone": "success" if enabled else "disabled",
        "action": action,
        "action_label": action_label,
        "action_tone": action_tone,
        "protocol": protocol,
        "source": source,
        "dest": dest,
        "dest_port": port,
        "rule_expression": rule_expression,
        "priority": priority,
        "description": detail or "—",
        "search_text": search_text,
        "is_center_peer": row_kind == "center_peer",
        "can_edit": True,
        "can_delete": True,
        "can_toggle": True,
        "raw": dict(rule),
    }


def adapt_center_rule_rows(
    rules: list[Mapping[str, Any]],
    *,
    group_names: Mapping[str, str] | None = None,
    peer_names: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """批量适配并保持输入顺序。"""

    return [
        adapt_center_rule_row(
            rule,
            group_names=group_names,
            peer_names=peer_names,
        )
        for rule in rules
    ]
