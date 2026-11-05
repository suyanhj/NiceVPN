# -*- coding: utf-8 -*-
"""User and certificate AG Grid click-to-copy regression tests."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.ui.pages import users as users_module
from app.ui.pages import certs as certs_module
from app.ui.pages.users import UsersPage
from app.ui.pages.certs import CertsPage


def _event(field, row, *, nested=False):
    if nested:
        return SimpleNamespace(args={"column": {"colId": field}, "data": row})
    return SimpleNamespace(args={"colId": field, "data": row})


def test_user_copyable_grid_columns_and_values():
    columns = {col["field"]: col for col in UsersPage._user_grid_columns()}
    row = {
        "username": "alice",
        "virtual_ip": "10.8.0.5",
        "group": "技术部",
        "group_id": "group-uuid",
        "source": "198.51.100.20",
    }
    for field in ("username", "virtual_ip", "group", "source"):
        assert "vpn-grid-copyable-cell" in columns[field]["cellClass"]
        assert UsersPage._grid_copy_target(field, row) is not None
    assert UsersPage._grid_copy_target("group", row) == ("技术部", "用户组名称已复制")
    assert UsersPage._grid_copy_target("virtual_ip", {"virtual_ip": "—"}) is None
    assert UsersPage._grid_copy_target("actions", row) is None
    assert UsersPage._grid_copy_target("traffic", row) is None


def test_user_grid_click_copies_only_intended_fields():
    page = UsersPage.__new__(UsersPage)
    copied = []
    opened = []
    async def copy(value, message):
        copied.append((value, message))
    page._copy_text = copy
    page._show_user_actions = lambda row: opened.append(row["username"])
    row = {"username": "alice", "virtual_ip": "10.8.0.5"}
    asyncio.run(page._on_grid_cell_clicked(_event("virtual_ip", row)))
    asyncio.run(page._on_grid_cell_clicked(_event("username", row, nested=True)))
    asyncio.run(page._on_grid_cell_clicked(_event("actions", row)))
    asyncio.run(page._on_grid_cell_clicked(_event("session_status", row)))
    assert copied == [
        ("10.8.0.5", "虚拟 IP 已复制"),
        ("alice", "用户名已复制"),
    ]
    assert opened == ["alice"]


def test_cert_copy_uses_full_serial_not_short_display():
    columns = {col["field"]: col for col in CertsPage._grid_columns()}
    row = {
        "common_name": "alice",
        "serial": "0123456789abcdef00112233445566",
        "serial_display": "01234567…33445566",
    }
    assert "vpn-grid-copyable-cell" in columns["common_name"]["cellClass"]
    assert "vpn-grid-copyable-cell" in columns["serial_display"]["cellClass"]
    assert CertsPage._grid_copy_target("serial_display", row) == (
        row["serial"], "完整证书序列号已复制",
    )
    assert CertsPage._grid_copy_target("serial_display", {"serial": ""}) is None
    assert CertsPage._grid_copy_target("status_label", row) is None


def test_cert_grid_click_copies_and_keeps_actions_separate(monkeypatch):
    page = CertsPage.__new__(CertsPage)
    copied = []
    notices = []
    opened = []
    async def fake_copy(value):
        copied.append(value)
        return True
    monkeypatch.setattr(certs_module, "copy_text_to_clipboard", fake_copy)
    monkeypatch.setattr(
        certs_module, "safe_notify",
        lambda message, *, type: notices.append((message, type)),
    )
    page._show_cert_actions = lambda row: opened.append(row["common_name"])
    row = {"common_name": "alice", "serial": "abcdef0123456789"}
    asyncio.run(page._on_grid_cell_clicked(_event("serial_display", row)))
    asyncio.run(page._on_grid_cell_clicked(_event("common_name", row, nested=True)))
    asyncio.run(page._on_grid_cell_clicked(_event("actions", row)))
    asyncio.run(page._on_grid_cell_clicked(_event("expires_display", row)))
    assert copied == ["abcdef0123456789", "alice"]
    assert notices == [
        ("完整证书序列号已复制", "positive"),
        ("证书 CN 已复制", "positive"),
    ]
    assert opened == ["alice"]


def test_cert_grid_copy_failure_has_no_false_success(monkeypatch):
    page = CertsPage.__new__(CertsPage)
    notices = []
    async def copy_denied(_value):
        return False
    monkeypatch.setattr(certs_module, "copy_text_to_clipboard", copy_denied)
    monkeypatch.setattr(certs_module, "safe_notify", lambda *args, **kwargs: notices.append((args, kwargs)))
    asyncio.run(page._on_grid_cell_clicked(_event("serial_display", {"serial": "abc"})))
    assert notices == []
