# -*- coding: utf-8 -*-
"""Firewall 纯视图辅助的独立契约测试。"""

from __future__ import annotations

import json

import pytest

from app.ui.firewall_helpers import (
    center_grid_columns,
    move_order,
    remote_grid_columns,
    rows_from_iptables_paste,
    rows_from_remote_import_payload,
    rows_from_remote_import_text_mixed,
    second_line_comment_text,
    short_peer_id,
)


_CHAIN = "VPN_PEER_abc123456789"


def test_remote_import_helpers_preserve_existing_contract() -> None:
    payload = {
        "peer_id": "peer-a",
        "chain": _CHAIN,
        "chain_exists": False,
        "rows": [
            {
                "rest": "-s 10.0.0.0/8 -j ACCEPT",
                "enabled": False,
                "priority": 35,
                "description": "keep metadata",
            }
        ],
    }

    assert rows_from_remote_import_payload(payload) == [
        {
            "rest": "-s 10.0.0.0/8 -j ACCEPT",
            "enabled": False,
            "priority": 35,
            "description": "keep metadata",
        }
    ]

    rows, chain, exists = rows_from_remote_import_text_mixed(
        json.dumps(payload, ensure_ascii=False),
        "peer-a",
        "ignored",
    )
    assert rows == rows_from_remote_import_payload(payload)
    assert chain == _CHAIN
    assert exists is False

    with pytest.raises(ValueError, match="peer_id"):
        rows_from_remote_import_text_mixed(
            json.dumps({"peer_id": "other", "rows": payload["rows"]}),
            "peer-a",
            _CHAIN,
        )


def test_iptables_paste_and_order_helpers() -> None:
    assert rows_from_iptables_paste(
        f"-A {_CHAIN} -j ACCEPT\n-s 1.1.1.1/32 -j DROP\n",
        _CHAIN,
    ) == [
        {"rest": "-j ACCEPT", "enabled": True},
        {"rest": "-s 1.1.1.1/32 -j DROP", "enabled": True},
    ]

    original = ["a", "b", "c"]
    assert move_order(original, 0, 2) == ["b", "c", "a"]
    assert original == ["a", "b", "c"]
    assert move_order(original, -1, 2) == []


def test_compact_labels_and_comment_fallbacks() -> None:
    assert short_peer_id("1234567890") == "12345678…"
    assert short_peer_id("") == "—"

    assert second_line_comment_text(
        {"description": "说明", "_center_tag_line": "中心标签"},
        title_text="规则",
        is_center_peer=True,
    ) == "说明 | 中心标签"

    assert second_line_comment_text(
        {"description": ""},
        title_text="规则",
        is_remote_row=True,
        iptables_comment="iptables note",
    ) == "iptables note"

    assert second_line_comment_text(
        {"description": "规则", "comment": ""},
        title_text="规则",
    ) == ""


def test_grid_column_helpers_keep_reorder_contract() -> None:
    remote = remote_grid_columns(can_reorder=True)
    assert remote[0]["field"] == "row_number"
    assert remote[0]["rowDrag"] is True
    assert remote[-1]["field"] == "actions"
    assert remote[-1]["pinned"] == "right"

    center = center_grid_columns(can_reorder=True, reorder_mode=True)
    assert center[0]["field"] == "priority"
    assert center[0]["rowDrag"] is True
    assert all(column.get("sortable") is False for column in center)
    assert all(column.get("filter") is False for column in center)
    actions = next(column for column in center if column["field"] == "actions")
    assert actions["hide"] is True
