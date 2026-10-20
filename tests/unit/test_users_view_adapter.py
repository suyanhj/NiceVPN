# -*- coding: utf-8 -*-
"""用户管理页纯数据适配测试。"""

from datetime import datetime

from app.ui.adapters.users import (
    adapt_user_row,
    build_user_rows,
    format_bytes,
    format_connection_duration,
)


NOW = datetime(2026, 10, 20, 14, 0, 0)


def test_adapt_online_user_prefers_ccd_virtual_ip_and_formats_session() -> None:
    row = adapt_user_row(
        {
            "username": "alice",
            "status": "active",
            "group_id": "group-a",
            "cert_serial": "1234567890ABCDEF1234567890ABCDEF",
            "created_at": "2026-10-01T08:00:00",
        },
        session={
            "virtual_address": "10.255.0.99",
            "real_address": "203.0.113.8:49200",
            "bytes_received": 1536,
            "bytes_sent": 1048576,
            "connected_since": "2026-10-20 12:30:00",
        },
        binding={"device_label": "Windows / OpenVPN"},
        ccd_virtual_ip="10.255.0.8",
        group_name="研发组",
        now=NOW,
    )

    assert row["id"] == "alice"
    assert row["is_active"] is True
    assert row["online"] is True
    assert row["status_label"] == "在线"
    assert row["virtual_ip"] == "10.255.0.8"
    assert row["traffic_received"] == "1.5 KB"
    assert row["traffic_sent"] == "1.0 MB"
    assert row["connected_since"] == "2026-10-20 12:30"
    assert row["connection_duration"] == "1时30分"
    assert row["group_name"] == "研发组"
    assert row["cert_serial_short"] == "12345678...90ABCDEF"


def test_adapt_offline_disabled_user_handles_missing_optional_fields() -> None:
    row = adapt_user_row(
        {
            "username": "bob",
            "status": "disabled",
            "group_id": "",
        },
        binding={"last_connected_since": "Mon Oct 19 10:20:30 2026"},
        now=NOW,
    )

    assert row["online"] is False
    assert row["is_active"] is False
    assert row["status_label"] == "离线 / 已停用"
    assert row["virtual_ip"] == "—"
    assert row["traffic_received"] == "—"
    assert row["traffic_sent"] == "—"
    assert row["connection_duration"] == "—"
    assert row["device_label"] == "—"
    assert row["cert_serial"] == "—"
    assert row["group_name"] == "—"
    assert row["last_connected_since"] == "Mon Oct 19 10:20:30 2026"


def test_build_user_rows_tolerates_missing_auxiliary_maps() -> None:
    rows = build_user_rows(
        [
            {"username": "alice", "group_id": "g1"},
            {"username": "bob", "group_id": "g2", "status": "disabled"},
        ],
        sessions={"alice": {"virtual_address": "10.0.0.2", "connected_since": "2026-10-20 13:00:00"}},
        groups={"g1": "默认组"},
        now=NOW,
    )

    assert [row["username"] for row in rows] == ["alice", "bob"]
    assert rows[0]["virtual_ip"] == "10.0.0.2"
    assert rows[0]["group_name"] == "默认组"
    assert rows[0]["connection_duration"] == "1时0分"
    assert rows[1]["group_name"] == "—"
    assert rows[1]["status_label"] == "离线 / 已停用"


def test_format_helpers_handle_invalid_values() -> None:
    assert format_bytes(None) == "0.0 B"
    assert format_bytes("bad") == "0.0 B"
    assert format_connection_duration("not-a-time", now=NOW) == "—"
    assert format_connection_duration("2026-10-21 12:00:00", now=NOW) == "—"
