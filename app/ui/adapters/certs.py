# -*- coding: utf-8 -*-
"""证书管理页纯展示数据适配。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


STATUS_LABELS = {
    "valid": "有效",
    "expiring": "即将到期",
    "revoked": "已吊销",
    "expired": "已过期",
    "unknown": "未知",
}


def short_serial(value: object) -> str:
    """压缩长序列号用于表格展示，同时不修改原始值。"""

    serial = str(value or "").strip()
    if not serial:
        return "—"
    if len(serial) <= 16:
        return serial
    return f"{serial[:8]}…{serial[-8:]}"


def adapt_cert_row(
    cert: Mapping[str, Any],
    *,
    now: datetime | None = None,
    warn_days: int = 7,
) -> dict[str, Any]:
    """标准化证书状态、到期等级、序列号和操作字段。"""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    common_name = str(cert.get("common_name") or "")
    serial = str(cert.get("serial") or "")
    raw_status = str(cert.get("status") or "unknown")
    status = raw_status if raw_status in {"valid", "revoked", "expired"} else "unknown"
    expires_at = str(cert.get("expires_at") or "")
    expires_display = "—"
    days_left: int | None = None

    if expires_at:
        try:
            expires_dt = datetime.fromisoformat(expires_at)
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            else:
                expires_dt = expires_dt.astimezone(timezone.utc)
            expires_display = expires_dt.strftime("%Y-%m-%d")
            days_left = (expires_dt - current.astimezone(timezone.utc)).days
            if status == "valid" and days_left < 0:
                status = "expired"
            elif status == "valid" and days_left <= warn_days:
                status = "expiring"
        except (TypeError, ValueError):
            expires_display = expires_at

    if status == "valid":
        tone = "success"
    elif status == "expiring":
        tone = "warning"
    elif status in {"expired", "revoked"}:
        tone = "failed"
    else:
        tone = "unknown"

    actionable = status not in {"revoked", "expired"}
    return {
        "id": common_name,
        "common_name": common_name or "—",
        "serial": serial,
        "serial_display": short_serial(serial),
        "status": status,
        "status_label": STATUS_LABELS.get(status, "未知"),
        "status_tone": tone,
        "expires_at": expires_at,
        "expires_display": expires_display,
        "days_left": days_left,
        "revoked_at": str(cert.get("revoked_at") or ""),
        "can_renew": actionable,
        "can_revoke": actionable,
        "actions": "操作",
        "_raw_status": raw_status,
    }
