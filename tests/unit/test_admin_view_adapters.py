# -*- coding: utf-8 -*-
"""用户与用户组展示数据适配测试。"""

from datetime import datetime

from app.ui.adapters.groups import adapt_group_view
from app.ui.adapters.users import adapt_user_grid_row


NOW = datetime(2026, 10, 20, 14, 0, 0)


def test_user_adapter_covers_online_ccd_certificate_group_and_binding() -> None:
    row = adapt_user_grid_row(
        {
            "username": "alice",
            "status": "active",
            "group_id": "group-1",
            "cert_serial": "1234567890ABCDEF1234567890ABCDEF",
            "created_at": "2026-10-01T08:00:00+00:00",
        },
        session={
            "virtual_address": "10.8.0.99",
            "real_address": "203.0.113.8:50000",
            "bytes_received": 1536,
            "bytes_sent": 1048576,
            "connected_since": "2026-10-20 12:30:00",
        },
        binding_aux={"device_label": "Windows / OpenVPN"},
        ccd_virtual_ip="10.8.0.8",
        group_names={"group-1": "研发组"},
        live_available=True,
        now=NOW,
    )

    assert row["session_status"] == "在线"
    assert row["account_status"] == "启用"
    assert row["virtual_ip"] == "10.8.0.8"
    assert row["group"] == "研发组"
    assert row["traffic"] == "↓1.5 KB ↑1.0 MB"
    assert row["connection"] == "2026-10-20 12:30 · 1时30分"
    assert row["device"] == "Windows / OpenVPN"
    assert row["cert_serial_display"] == "12345678...90ABCDEF"
    assert row["_online"] is True


def test_user_adapter_covers_offline_disabled_missing_and_unavailable_live_data() -> None:
    row = adapt_user_grid_row(
        {
            "username": "bob",
            "status": "disabled",
            "group_id": "",
        },
        session=None,
        binding_aux={},
        ccd_virtual_ip="",
        group_names={},
        live_available=False,
        now=NOW,
    )

    assert row["account_status"] == "停用"
    assert row["session_status"] == "未知"
    assert row["virtual_ip"] == "—"
    assert row["group"] == "—"
    assert row["cert_serial"] == "—"
    assert row["traffic"] == "—"
    assert row["connection"] == "—"
    assert row["device"] == "—"


def test_group_adapter_covers_root_active_disabled_and_user_counts() -> None:
    root = adapt_group_view(
        {"id": "root", "name": "默认组", "subnet": "10.8.0.0/16", "status": "active", "user_count": 3},
        is_root=True,
    )
    child = adapt_group_view(
        {"id": "dev", "name": "开发组", "subnet": "10.8.1.0/24", "status": "disabled", "user_count": 0},
    )

    assert root["status_label"] == "启用"
    assert root["status_tone"] == "running"
    assert root["is_root"] is True
    assert root["user_count"] == 3
    assert root["can_delete"] is False
    assert "根组" in root["delete_reason"]

    assert child["status_label"] == "停用"
    assert child["status_tone"] == "disabled"
    assert child["subnet"] == "10.8.1.0/24"
    assert child["can_delete"] is True
