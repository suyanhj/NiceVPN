# -*- coding: utf-8 -*-
"""对端站点页面纯展示数据适配。"""

from __future__ import annotations

from typing import Any, Mapping


def adapt_peer_view(
    row: Mapping[str, Any],
    *,
    group_names: Mapping[str, str] | None = None,
    tag_names: Mapping[str, str] | None = None,
    has_global_ssh_key: bool = False,
    busy: bool = False,
) -> dict[str, Any]:
    """标准化对端展示、SSH 能力和操作可用性。"""

    groups = group_names or {}
    tags = tag_names or {}
    peer_id = str(row.get("id") or "").strip()
    name = str(row.get("name") or peer_id or "未命名对端").strip()
    bound_username = str(row.get("bound_username") or "").strip()
    lan_cidrs = [
        str(value).strip()
        for value in (row.get("lan_cidrs") or [])
        if str(value).strip()
    ]
    visible_group_ids = [
        str(value).strip()
        for value in (row.get("mesh_route_visible_group_ids") or [])
        if str(value).strip()
    ]

    visible_tag_ids = [
        str(value).strip()
        for value in (row.get("mesh_route_visible_tag_ids") or [])
        if str(value).strip()
    ]
    visible_group_names = [
        str(groups.get(group_id) or f"{group_id[:8]}…")
        for group_id in visible_group_ids
    ]
    visible_tag_names = [
        str(tags.get(tag_id) or f"{tag_id[:8]}…")
        for tag_id in visible_tag_ids
    ]
    if not visible_group_ids and not visible_tag_ids:
        mesh_label = "全部用户"
    else:
        parts = []
        if visible_group_names:
            group_label = "、".join(visible_group_names[:3])
            if len(visible_group_names) > 3:
                group_label += f" 等{len(visible_group_names)}组"
            parts.append(f"组：{group_label}")
        if visible_tag_names:
            tag_label = "、".join(visible_tag_names[:3])
            if len(visible_tag_names) > 3:
                tag_label += f" 等{len(visible_tag_names)}标签"
            parts.append(f"标签：{tag_label}")
        mesh_label = "；".join(parts)

    ssh_host = str(row.get("ssh_host") or "").strip()
    ssh_username = str(row.get("ssh_username") or "").strip()
    has_peer_password = bool(str(row.get("ssh_password") or "").strip())
    has_peer_key = bool(str(row.get("ssh_private_key") or "").strip())
    has_auth = has_peer_password or has_peer_key or bool(has_global_ssh_key)
    ssh_identity_ready = bool(ssh_host and ssh_username)
    remote_capable = ssh_identity_ready and has_auth

    if not ssh_identity_ready:
        connection_label = "未配置 SSH"
        connection_tone = "unknown"
        connection_reason = "请先填写 SSH 主机与用户名"
    elif not has_auth:
        connection_label = "缺少凭据"
        connection_tone = "warning"
        connection_reason = "请配置 SSH 密码、对端私钥或系统全局私钥"
    else:
        connection_label = "可远程运维"
        connection_tone = "success"
        connection_reason = ""

    if busy:
        connection_label = "远程操作中"
        connection_tone = "warning"

    return {
        "id": peer_id,
        "name": name,
        "bound_username": bound_username or "—",
        "lan_cidrs": lan_cidrs,
        "lan_label": "、".join(lan_cidrs) if lan_cidrs else "无内网",
        "visible_group_ids": visible_group_ids,
        "visible_group_names": visible_group_names,
        "visible_tag_ids": visible_tag_ids,
        "visible_tag_names": visible_tag_names,
        "mesh_label": mesh_label,
        "ssh_host": ssh_host,
        "ssh_username": ssh_username,
        "ssh_port": int(row.get("ssh_port") or 22),
        "remote_capable": remote_capable,
        "connection_label": connection_label,
        "connection_tone": connection_tone,
        "connection_reason": connection_reason,
        "busy": bool(busy),
        "can_install": remote_capable and not busy,
        "can_push": remote_capable and not busy,
        "can_remote_service": remote_capable,
        "can_remote_log": remote_capable,
        "can_edit": not busy,
        "can_delete": not busy,
    }
