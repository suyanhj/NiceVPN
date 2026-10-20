# -*- coding: utf-8 -*-
"""证书展示适配层测试。"""

from datetime import datetime, timedelta, timezone

import pytest

from app.ui.adapters.certs import adapt_cert_row, short_serial


NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def _cert(*, status="valid", expires_delta_days=30, serial="ABC123", cn="alice"):
    return {
        "common_name": cn,
        "serial": serial,
        "status": status,
        "expires_at": (NOW + timedelta(days=expires_delta_days)).isoformat(),
        "revoked_at": "",
    }


@pytest.mark.parametrize(
    ("source", "expected_status", "label", "tone", "actionable"),
    [
        (_cert(expires_delta_days=30), "valid", "有效", "success", True),
        (_cert(expires_delta_days=3), "expiring", "即将到期", "warning", True),
        (_cert(expires_delta_days=-1), "expired", "已过期", "failed", False),
        (_cert(status="revoked"), "revoked", "已吊销", "failed", False),
    ],
)
def test_cert_adapter_status_and_expiry_levels(
    source: dict,
    expected_status: str,
    label: str,
    tone: str,
    actionable: bool,
) -> None:
    row = adapt_cert_row(source, now=NOW, warn_days=7)

    assert row["status"] == expected_status
    assert row["status_label"] == label
    assert row["status_tone"] == tone
    assert row["can_renew"] is actionable
    assert row["can_revoke"] is actionable


def test_cert_adapter_handles_unknown_and_missing_fields() -> None:
    row = adapt_cert_row(
        {
            "status": "mystery",
            "expires_at": "not-a-date",
        },
        now=NOW,
    )

    assert row["common_name"] == "—"
    assert row["serial_display"] == "—"
    assert row["status"] == "unknown"
    assert row["status_label"] == "未知"
    assert row["status_tone"] == "unknown"
    assert row["expires_display"] == "not-a-date"
    assert row["days_left"] is None


def test_cert_adapter_preserves_revoked_status_even_when_expiry_is_future() -> None:
    row = adapt_cert_row(_cert(status="revoked", expires_delta_days=200), now=NOW)

    assert row["status"] == "revoked"
    assert row["can_renew"] is False
    assert row["can_revoke"] is False


def test_short_serial_uses_stable_head_and_tail() -> None:
    assert short_serial("") == "—"
    assert short_serial("12345678") == "12345678"
    assert short_serial("1234567890ABCDEF1234567890ABCDEF") == "12345678…90ABCDEF"
