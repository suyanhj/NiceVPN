# -*- coding: utf-8 -*-
"""UI callbacks must not reuse dead NiceGUI slots or deleted async containers."""
from __future__ import annotations

import asyncio
import ast
from pathlib import Path
from types import SimpleNamespace

from app.ui.components import feedback
from app.ui.pages import certs as certs_module
from app.ui.pages.users import UsersPage
from app.ui.pages.certs import CertsPage


def test_safe_notify_ignores_deleted_slot_context(monkeypatch):
    def deleted_slot(*_args, **_kwargs):
        raise RuntimeError("The parent element this slot belongs to has been deleted.")
    monkeypatch.setattr(feedback.ui, "notify", deleted_slot)
    assert feedback.safe_notify("删除成功", type="positive") is False


def test_async_user_refresh_does_not_touch_deleted_container():
    page = UsersPage.__new__(UsersPage)
    page.data_container = SimpleNamespace(is_deleted=True, clear=lambda: (_ for _ in ()).throw(AssertionError("deleted")))
    page._refresh_running = True

    async def load():
        return [], [], 0

    page._load_user_grid_rows_async = load
    asyncio.run(page._refresh_user_list_async())
    assert page._refresh_running is False


def test_async_cert_refresh_does_not_touch_deleted_container(monkeypatch):
    page = CertsPage.__new__(CertsPage)
    page.data_container = SimpleNamespace(is_deleted=True, clear=lambda: (_ for _ in ()).throw(AssertionError("deleted")))
    page.summary_container = SimpleNamespace(is_deleted=True, clear=lambda: (_ for _ in ()).throw(AssertionError("deleted")))
    page.alert_container = SimpleNamespace(is_deleted=True, clear=lambda: (_ for _ in ()).throw(AssertionError("deleted")))
    page._refresh_running = True

    async def io_bound(_callback):
        return []

    monkeypatch.setattr(certs_module.run, "io_bound", io_bound)
    page._load_rows = lambda: []
    asyncio.run(page._refresh_cert_list_async())
    assert page._refresh_running is False


def test_management_pages_do_not_use_slot_bound_ui_notify():
    """Management callbacks should use safe_notify or their captured page client."""
    root = Path(__file__).resolve().parents[2] / "app" / "ui" / "pages"
    for file in root.glob("*.py"):
        tree = ast.parse(file.read_text(encoding="utf-8"))
        direct_notifications = [
            node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "notify"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "ui"
        ]
        assert not direct_notifications, f"{file.name}: direct ui.notify at {direct_notifications}"
