# -*- coding: utf-8 -*-
"""用户管理页纯数据适配。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


_TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%a %b %d %H:%M:%S %Y",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S",
)


def format_bytes(size: object) -> str:
    """将字节数格式化为人类可读文本。"""

    try:
        value = float(size or 0)
    except (TypeError, ValueError):
        value = 0.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def parse_connection_time(value: object) -> datetime | None:
    """解析 OpenVPN status 与设备绑定记录中的时间。"""

    normalized = " ".join(str(value or "").strip().split())
    if not normalized:
        return None
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return None


def format_connected_since(value: object) -> str:
    """连接时间统一展示为分钟精度；未知格式原样保留。"""

    normalized = " ".join(str(value or "").strip().split())
    if not normalized:
        return "—"
    parsed = parse_connection_time(normalized)
    return parsed.strftime("%Y-%m-%d %H:%M") if parsed else normalized


def format_connection_duration(value: object, *, now: datetime | None = None) -> str:
    """计算连接持续时长。"""

    start = parse_connection_time(value)
    if start is None:
        return "—"
    delta = (now or datetime.now()) - start
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return "—"
    hours, remainder = divmod(total_seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}天{hours}时{minutes}分"
    return f"{hours}时{minutes}分"


def short_serial(value: object) -> str:
    """长证书序列号压缩展示，同时保留完整值在行模型。"""

    text = str(value or "").strip()
    if not text or text == "—":
        return "—"
    if len(text) <= 16:
        return text
    return f"{text[:8]}...{text[-8:]}"


def adapt_user_grid_row(
    user: Mapping[str, Any],
    *,
    session: Mapping[str, Any] | None,
    binding_aux: Mapping[str, Any] | None,
    ccd_virtual_ip: str,
    group_names: Mapping[str, str],
    live_available: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """把用户、证书、组、绑定、CCD 与会话聚合为稳定 AG Grid 行。"""

    binding_aux = binding_aux or {}
    username = str(user.get("username") or "")
    group_id = str(user.get("group_id") or "")
    status = str(user.get("status") or "active")
    is_online = session is not None if live_available else False
    cert_serial = str(user.get("cert_serial") or "—")
    created_at = str(user.get("created_at") or "")[:10] or "未知"

    if ccd_virtual_ip:
        virtual_ip = ccd_virtual_ip
    elif session:
        virtual_ip = str(session.get("virtual_address") or "").strip() or "—"
    else:
        virtual_ip = "—"

    bytes_received = int((session or {}).get("bytes_received", 0) or 0)
    bytes_sent = int((session or {}).get("bytes_sent", 0) or 0)
    traffic = (
        f"↓{format_bytes(bytes_received)} ↑{format_bytes(bytes_sent)}"
        if session
        else "—"
    )
    connected_raw = str((session or {}).get("connected_since") or "")
    if session:
        connection = (
            f"{format_connected_since(connected_raw)} · "
            f"{format_connection_duration(connected_raw, now=now)}"
        )
    else:
        last_connected = str(binding_aux.get("last_connected_since") or "")
        connection = (
            f"上次 {format_connected_since(last_connected)}"
            if last_connected
            else "—"
        )

    group_name = group_names.get(group_id) or (group_id[:8] if group_id else "—")
    return {
        "id": username,
        "username": username,
        "account_status": "启用" if status == "active" else "停用",
        "session_status": (
            "在线" if is_online else ("离线" if live_available else "未知")
        ),
        "virtual_ip": virtual_ip,
        "group": group_name,
        "group_id": group_id,
        "cert_serial": cert_serial,
        "cert_serial_display": short_serial(cert_serial),
        "traffic": traffic,
        "connection": connection,
        "source": str((session or {}).get("real_address") or "—") if session else "—",
        "device": str(binding_aux.get("device_label") or "").strip() or "—",
        "created_at": created_at,
        "actions": "操作",
        "_status": status,
        "_online": is_online,
        "_live_available": live_available,
    }
