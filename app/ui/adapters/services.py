# -*- coding: utf-8 -*-
"""服务管理页纯展示数据适配。"""

from __future__ import annotations

from typing import Any, Mapping


def format_traffic(value: object) -> str:
    """格式化累计流量。"""

    try:
        size = float(max(int(value or 0), 0))
    except (TypeError, ValueError):
        size = 0.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return "0 B"


def adapt_local_service_view(
    status: Any,
    instance_config: Mapping[str, Any] | None = None,
    *,
    busy: bool = False,
) -> dict[str, Any]:
    """标准化本机 OpenVPN 实例展示和操作可用性。"""

    config = instance_config or {}
    name = str(getattr(status, "name", "") or config.get("name") or "")
    raw_active = getattr(status, "active", None)
    active = raw_active is True

    if raw_active is True:
        state = "running"
        label = "运行中"
        tone = "success"
    elif raw_active is False:
        state = "stopped"
        label = "已停止"
        tone = "disabled"
    else:
        state = "unknown"
        label = "未知"
        tone = "unknown"

    if busy:
        label = "操作中"
        tone = "warning"

    client_count = int(getattr(status, "client_count", 0) or 0)
    received = int(getattr(status, "bytes_received", 0) or 0)
    sent = int(getattr(status, "bytes_sent", 0) or 0)

    return {
        "id": name,
        "name": name or "—",
        "proto": str(config.get("proto") or "udp").upper(),
        "port": int(config.get("port") or 1194),
        "subnet": str(config.get("subnet") or config.get("server") or "子网未配"),
        "state": state,
        "status_label": label,
        "status_tone": tone,
        "active": active,
        "busy": bool(busy),
        "client_count": client_count,
        "bytes_received": received,
        "bytes_sent": sent,
        "traffic_received": format_traffic(received),
        "traffic_sent": format_traffic(sent),
        "can_start": raw_active is False and not busy,
        "can_stop": active and not busy,
        "can_restart": active and not busy,
        "can_view_logs": True,
        "can_edit_config": not busy,
    }


def adapt_peer_service_view(
    peer: Mapping[str, Any],
    status: Mapping[str, Any] | None = None,
    *,
    error: str | None = None,
    busy: bool = False,
) -> dict[str, Any]:
    """标准化对端 openvpn-client systemd 状态与操作可用性。"""

    peer_id = str(peer.get("id") or "").strip()
    name = str(peer.get("name") or peer_id or "未命名对端").strip()
    host = str(peer.get("ssh_host") or "").strip()
    username = str(peer.get("ssh_username") or "").strip()
    bound = str(peer.get("bound_username") or "").strip()
    data = status or {}

    if error:
        state = "failed"
        label = "远程不可达"
        tone = "failed"
        detail = str(error)
        exists = False
        active_state = "unknown"
        enabled_state = "unknown"
    elif not status:
        state = "unknown"
        label = "待查询"
        tone = "unknown"
        detail = ""
        exists = False
        active_state = "unknown"
        enabled_state = "unknown"
    else:
        exists = bool(data.get("exists"))
        active_state = str(data.get("active_state") or data.get("is_active") or "unknown")
        enabled_state = str(data.get("unit_file_state") or data.get("is_enabled") or "unknown")
        if not exists:
            state = "missing"
            label = "服务未安装"
            tone = "warning"
        elif active_state == "active":
            state = "running"
            label = "运行中"
            tone = "success"
        elif active_state in {"inactive", "failed", "deactivating"}:
            state = "stopped" if active_state == "inactive" else "failed"
            label = "已停止" if active_state == "inactive" else "运行失败"
            tone = "disabled" if active_state == "inactive" else "failed"
        else:
            state = "unknown"
            label = "状态未知"
            tone = "unknown"
        config_state = "配置存在" if data.get("config_exists") else "配置缺失"
        detail = f"{active_state} / {enabled_state} / {config_state}"

    if busy:
        label = "操作中"
        tone = "warning"

    is_running = exists and active_state == "active"
    enabled = enabled_state.startswith("enabled")
    action_ready = bool(peer_id and host and username) and not busy and not error

    return {
        "id": peer_id,
        "name": name,
        "host": host or "未配 SSH",
        "ssh_username": username or "—",
        "bound_username": bound or "—",
        "service": str(data.get("service") or "—"),
        "exists": exists,
        "state": state,
        "status_label": label,
        "status_tone": tone,
        "detail": detail,
        "active_state": active_state,
        "enabled_state": enabled_state,
        "config_exists": bool(data.get("config_exists")),
        "busy": bool(busy),
        "error": str(error or ""),
        "can_start": action_ready and exists and not is_running,
        "can_stop": action_ready and is_running,
        "can_restart": action_ready and exists,
        "can_enable": action_ready and exists and not enabled,
        "can_disable": action_ready and exists and enabled,
        "can_view_logs": action_ready,
    }
