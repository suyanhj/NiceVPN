# -*- coding: utf-8 -*-
"""防火墙中心策略纯展示数据适配。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
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
    # Global search stays focused on concise operational fields. Descriptions and
    # iptables comments can be very long, so they are deliberately not indexed.
    search_text = " ".join(
        [
            rule_id,
            source,
            dest,
            port,
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
        "action_label_tone": action_tone,
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


_TZ_UTC_PLUS_8 = timezone(timedelta(hours=8))


def parse_remote_rule_hints(rest: object) -> dict[str, str]:
    """从对端 iptables rest 片段抽取常用展示字段；保留原始文本作为事实依据。"""

    text = str(rest or "").strip()
    out: dict[str, str] = {}
    patterns = (
        ("proto", r"-p\s+(!?\S+)", re.IGNORECASE),
        ("action", r"-j\s+(\S+)", re.IGNORECASE),
        ("source", r"-s\s+(\S+)", 0),
        ("dest", r"-d\s+(\S+)", 0),
        ("dport", r"--dport(?:s)?\s+(!?\S+)", re.IGNORECASE),
        ("in_if", r"-i\s+(\S+)", 0),
        ("out_if", r"-o\s+(\S+)", 0),
    )
    for key, pattern, flags in patterns:
        match = re.search(pattern, text, flags)
        if match:
            value = match.group(1)
            if key in {"proto", "action"}:
                value = value.upper()
            out[key] = value

    comment = re.search(r'--comment\s+"((?:\\.|[^"\\])*)"', text)
    if not comment:
        comment = re.search(r"--comment\s+(\S+)", text)
    if comment:
        value = comment.group(1).replace("\\'", "'")
        out["comment"] = value if len(value) <= 120 else value[:117] + "…"
    return out


def format_remote_cache_timestamp(raw: object) -> str:
    """将缓存 UTC ISO 时间转换为东八区展示值。"""

    if not raw or not str(raw).strip():
        return "—"
    text = str(raw).strip()
    try:
        iso = text[:-1] + "+00:00" if text.endswith("Z") else text
        value = datetime.fromisoformat(iso)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(_TZ_UTC_PLUS_8).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OSError):
        pass
    normalized = text.replace("T", " ", 1) if "T" in text else text
    if len(normalized) >= 19:
        return normalized[:19]
    return normalized[:32]


def adapt_remote_rule_row(
    row: Mapping[str, Any],
    *,
    index: int,
    peer_id: str,
) -> dict[str, Any]:
    """把对端工作副本一行转换为稳定 Grid 行，并保留原始 rest。"""

    raw_rest = str(row.get("rest") or "").strip()
    hints = parse_remote_rule_hints(raw_rest)
    protocol = str(hints.get("proto") or "ALL").upper()
    if protocol in {"", "ANY", "0"}:
        protocol = "ALL"
    action = str(hints.get("action") or "—").upper()
    action_tone = {
        "ACCEPT": "success",
        "DROP": "failed",
        "REJECT": "warning",
    }.get(action, "unknown")
    source = str(hints.get("source") or "*")
    dest = str(hints.get("dest") or "*")
    dest_port = str(hints.get("dport") or "*")
    enabled = bool(row.get("enabled", True))
    priority = int(row.get("priority", 0) or 0) or ((index + 1) * 10)
    description = (
        str(row.get("description") or "").strip()
        or str(hints.get("comment") or "").strip()
        or "—"
    )
    stable_id = f"{peer_id}:{index}"

    return {
        "id": stable_id,
        "index": index,
        "row_number": index + 1,
        "peer_id": str(peer_id or "").strip(),
        "enabled": enabled,
        "status_label": "启用" if enabled else "停用",
        "status_tone": "success" if enabled else "disabled",
        "priority": priority,
        "protocol": protocol,
        "action_label": action,
        "action_tone": action_tone,
        "action_label_tone": action_tone,
        "source": source,
        "dest": dest,
        "dest_port": dest_port,
        "rule_expression": f"{source} → {dest}:{dest_port}",
        "description": description,
        "raw_rest": raw_rest,
        "parsed_hints": hints,
        "can_toggle": bool(raw_rest),
        "can_edit": bool(raw_rest),
        "can_delete": bool(raw_rest),
        "raw": dict(row),
        "search_text": " ".join(
            [
                str(index + 1),
                protocol,
                action,
                source,
                dest,
                dest_port,
                description,
                raw_rest,
                "启用" if enabled else "停用",
            ]
        ).casefold(),
    }


def adapt_remote_rule_rows(
    rows: list[Mapping[str, Any]],
    *,
    peer_id: str,
) -> list[dict[str, Any]]:
    """批量适配对端工作副本规则，稳定 ID 使用 peer_id + 当前行索引。"""

    return [
        adapt_remote_rule_row(row, index=index, peer_id=peer_id)
        for index, row in enumerate(rows)
    ]


def adapt_remote_cache_status(
    cache: Mapping[str, Any] | None,
    *,
    fetch_running: bool = False,
) -> dict[str, Any]:
    """标准化对端链缓存状态、时间、错误与操作可用性。"""

    data = cache or {}
    exists = bool(cache)
    pending = bool(data.get("pending_sync"))
    error = str(data.get("last_sync_error") or "").strip()
    chain_exists = bool(data.get("chain_exists")) if exists else False

    if fetch_running:
        tone = "warning"
        label = "正在拉取远端真值"
    elif error:
        tone = "failed"
        label = "上次写回失败"
    elif pending:
        tone = "warning"
        label = "本地有待推送变更"
    elif not exists:
        tone = "unknown"
        label = "尚无本地副本"
    elif not chain_exists:
        tone = "warning"
        label = "远端链未创建"
    else:
        tone = "success"
        label = "已与远端同步"

    return {
        "exists": exists,
        "chain": str(data.get("chain") or ""),
        "chain_exists": chain_exists,
        "pending_sync": pending,
        "last_sync_error": error,
        "last_pulled_at": format_remote_cache_timestamp(data.get("last_pulled_at")),
        "last_pushed_at": format_remote_cache_timestamp(data.get("last_pushed_at")),
        "updated_at": format_remote_cache_timestamp(data.get("updated_at")),
        "status_label": label,
        "status_tone": tone,
        "row_count": len(data.get("rows") or []) if exists else 0,
        "can_push": exists and not fetch_running,
        "can_edit": exists and not fetch_running,
    }


def adapt_remote_peer_capability(
    peer: Mapping[str, Any] | None,
    *,
    has_ssh_auth: bool,
    fetch_running: bool = False,
) -> dict[str, Any]:
    """标准化对端 SSH 条件及拉取/推送可用性。"""

    data = peer or {}
    peer_id = str(data.get("id") or "").strip()
    host = str(data.get("ssh_host") or "").strip()
    username = str(data.get("ssh_username") or "").strip()
    identity_ready = bool(host and username)
    remote_ready = bool(peer_id and identity_ready and has_ssh_auth)

    if not peer_id:
        label, tone, reason = "未选择对端", "unknown", "请选择对端站点"
    elif not identity_ready:
        label, tone, reason = "SSH 未配置", "warning", "请先配置 SSH 主机与用户名"
    elif not has_ssh_auth:
        label, tone, reason = (
            "SSH 缺少凭据",
            "warning",
            "请配置密码、对端私钥或系统全局私钥",
        )
    elif fetch_running:
        label, tone, reason = "正在拉取", "warning", "远端链正在拉取"
    else:
        label, tone, reason = "SSH 可用", "success", ""

    return {
        "peer_id": peer_id,
        "name": str(data.get("name") or peer_id or "—"),
        "host": host,
        "ssh_username": username,
        "has_ssh_auth": bool(has_ssh_auth),
        "remote_ready": remote_ready,
        "status_label": label,
        "status_tone": tone,
        "reason": reason,
        "can_fetch": remote_ready and not fetch_running,
        "can_push": remote_ready and not fetch_running,
    }
