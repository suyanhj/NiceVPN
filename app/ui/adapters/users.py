# -*- coding: utf-8 -*-
"""用户管理页数据适配。

把业务服务返回的用户、分组、CCD、设备绑定与 OpenVPN 在线会话
统一转换为稳定的表格行模型；此模块不依赖 NiceGUI，便于单元测试。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


_MISSING = "—"
_TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%a %b %d %H:%M:%S %Y",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S",
)


def format_bytes(size: object) -> str:
    """将字节数格式化为便于列表阅读的值。"""

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
    """兼容 OpenVPN status 与历史绑定记录中的常见时间格式。"""

    text = " ".join(str(value or "").strip().split())
    if not text:
        return None
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def format_connection_time(value: object) -> str:
    """格式化连接时间；未知格式保留原值，缺失值显示破折号。"""

    text = " ".join(str(value or "").strip().split())
    if not text:
        return _MISSING
    parsed = parse_connection_time(text)
    return parsed.strftime("%Y-%m-%d %H:%M") if parsed else text


def format_connection_duration(value: object, *, now: datetime | None = None) -> str:
    """计算在线连接时长；无法解析或时间在未来时返回破折号。"""

    start = parse_connection_time(value)
    if start is None:
        return _MISSING

    current = now or datetime.now()
    total_seconds = int((current - start).total_seconds())
    if total_seconds < 0:
        return _MISSING

    hours, remainder = divmod(total_seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}天{hours}时{minutes}分"
    return f"{hours}时{minutes}分"


def _short_serial(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return _MISSING
    if len(text) <= 16:
        return text
    return f"{text[:8]}...{text[-8:]}"


def adapt_user_row(
    user: Mapping[str, Any],
    *,
    session: Mapping[str, Any] | None = None,
    binding: Mapping[str, Any] | None = None,
    ccd_virtual_ip: str | None = None,
    group_name: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """把单个用户及辅助数据转换为用户管理页行模型。"""

    binding = binding or {}
    username = str(user.get("username") or "").strip()
    account_status = str(user.get("status") or "active").strip() or "active"
    is_active = account_status == "active"
    is_online = session is not None

    group_id = str(user.get("group_id") or "").strip()
    cert_serial = str(user.get("cert_serial") or "").strip()
    created_at = str(user.get("created_at") or "").strip()
    created_date = created_at[:10] if created_at else _MISSING

    session_virtual_ip = str((session or {}).get("virtual_address") or "").strip()
    virtual_ip = str(ccd_virtual_ip or "").strip() or session_virtual_ip or _MISSING

    bytes_received = (session or {}).get("bytes_received", 0) if is_online else 0
    bytes_sent = (session or {}).get("bytes_sent", 0) if is_online else 0
    connected_since_raw = (session or {}).get("connected_since", "") if is_online else ""
    last_connected_raw = str(binding.get("last_connected_since") or "").strip()

    online_label = "在线" if is_online else "离线"
    account_label = "启用" if is_active else "停用"

    return {
        "id": username,
        "username": username,
        "account_status": account_status,
        "account_status_label": account_label,
        "is_active": is_active,
        "online": is_online,
        "online_status": "online" if is_online else "offline",
        "online_status_label": online_label,
        "status_label": online_label if is_active else f"{online_label} / 已停用",
        "group_id": group_id,
        "group_name": str(group_name or "").strip() or _MISSING,
        "virtual_ip": virtual_ip,
        "real_address": str((session or {}).get("real_address") or "").strip() or _MISSING,
        "bytes_received": bytes_received or 0,
        "bytes_sent": bytes_sent or 0,
        "traffic_received": format_bytes(bytes_received) if is_online else _MISSING,
        "traffic_sent": format_bytes(bytes_sent) if is_online else _MISSING,
        "connected_since_raw": str(connected_since_raw or ""),
        "connected_since": format_connection_time(connected_since_raw) if is_online else _MISSING,
        "connection_duration": (
            format_connection_duration(connected_since_raw, now=now) if is_online else _MISSING
        ),
        "device_label": str(binding.get("device_label") or "").strip() or _MISSING,
        "last_connected_since": (
            format_connection_time(last_connected_raw) if last_connected_raw else _MISSING
        ),
        "cert_serial": cert_serial or _MISSING,
        "cert_serial_short": _short_serial(cert_serial),
        "created_at": created_at,
        "created_date": created_date,
    }


def build_user_rows(
    users: list[Mapping[str, Any]],
    *,
    sessions: Mapping[str, Mapping[str, Any]] | None = None,
    bindings: Mapping[str, Mapping[str, Any]] | None = None,
    ccd_virtual_ips: Mapping[str, str] | None = None,
    groups: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """批量构建用户行模型，并容忍所有辅助数据缺失。"""

    sessions = sessions or {}
    bindings = bindings or {}
    ccd_virtual_ips = ccd_virtual_ips or {}
    groups = groups or {}

    rows: list[dict[str, Any]] = []
    for user in users:
        username = str(user.get("username") or "").strip()
        group_id = str(user.get("group_id") or "").strip()
        rows.append(
            adapt_user_row(
                user,
                session=sessions.get(username),
                binding=bindings.get(username),
                ccd_virtual_ip=ccd_virtual_ips.get(username),
                group_name=groups.get(group_id),
                now=now,
            )
        )
    return rows
