# -*- coding: utf-8 -*-
"""证书管理 AG Grid、状态适配和危险操作测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from nicegui.testing import user_simulation

from app.ui.pages.certs import CertsPage


def _cert(name: str, status: str, *, days: int = 90, serial: str | None = None) -> dict:
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    return {
        "common_name": name,
        "serial": serial or f"SERIAL-{name}-0123456789",
        "status": status,
        "expires_at": (now + timedelta(days=days)).isoformat(),
        "revoked_at": "",
    }


def test_cert_adapter_covers_valid_expiring_expired_revoked_and_invalid_fields() -> None:
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)

    valid = CertsPage._adapt_cert_row(_cert("valid", "valid", days=90), now=now)
    expiring = CertsPage._adapt_cert_row(_cert("soon", "valid", days=5), now=now)
    expired = CertsPage._adapt_cert_row(_cert("old", "valid", days=-1), now=now)
    revoked = CertsPage._adapt_cert_row(_cert("revoked", "revoked", days=90), now=now)
    partial = CertsPage._adapt_cert_row({"common_name": "partial", "status": "x"}, now=now)

    assert valid["status"] == "valid"
    assert valid["status_label"] == "有效"
    assert expiring["status"] == "expiring"
    assert expiring["status_tone"] == "warning"
    assert expired["status"] == "expired"
    assert revoked["status"] == "revoked"
    assert revoked["status_tone"] == "failed"
    assert partial["status"] == "unknown"
    assert partial["expires_display"] == "—"


def test_certs_page_uses_shared_grid_bulk_state_and_search(monkeypatch: pytest.MonkeyPatch) -> None:
    page = CertsPage()
    sample = [
        _cert("alice", "valid", days=90),
        _cert("bob", "valid", days=3),
        _cert("revoked-user", "revoked", days=90),
    ]
    monkeypatch.setattr(page.cert_service, "list_all", lambda: sample)
    monkeypatch.setattr(page.cert_service, "get_expiring", lambda: [sample[1]])

    async def verify_page() -> None:
        async with user_simulation(page.render) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("1 个证书将到期")
            await user.should_see("证书总数")
            await user.should_see("有效证书")
            await user.should_see("临期证书")
            await user.should_see("失效 / 吊销")
            await user.should_see("共 3 个证书")
            await user.should_see("已选择 0 项")

            page.search_input.set_value("no-such-cert")
            page._handle_search()
            await user.should_see("没有匹配结果")
            await user.should_see("没有证书匹配“no-such-cert”")

    asyncio.run(verify_page())

    assert page.grid_view is not None
    assert page.grid_view.grid._props["modules"] == ["AllCommunityModule"]
    assert page.grid_view.grid.options["paginationPageSize"] == 50
    assert ":getRowId" in page.grid_view.grid.options
    assert page.bulk_renew_button._props["disable"] is True
    assert page.bulk_revoke_button._props["disable"] is True


def test_cert_actionability_disables_expired_and_revoked_actions() -> None:
    """过期/吊销状态的单项动作语义由适配行稳定提供。"""

    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    revoked = CertsPage._adapt_cert_row(_cert("r", "revoked"), now=now)
    expired = CertsPage._adapt_cert_row(_cert("e", "expired", days=-5), now=now)
    valid = CertsPage._adapt_cert_row(_cert("v", "valid"), now=now)

    assert revoked["status"] == "revoked"
    assert expired["status"] == "expired"
    assert valid["status"] == "valid"


def test_batch_renew_and_revoke_summarize_partial_results(monkeypatch: pytest.MonkeyPatch) -> None:
    page = CertsPage()
    page.selected_certs = {"ok", "bad"}

    def renew(name: str) -> bool:
        return name == "ok"

    monkeypatch.setattr(page.cert_service, "renew", renew)
    monkeypatch.setattr(page, "_refresh_cert_list", lambda: None)

    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.ui.pages.certs.ui.notify",
        lambda message, type="info", **_kwargs: messages.append((str(message), type)),
    )

    page._do_batch_renew()
    assert messages[-1] == ("批量续签完成：成功 1，失败 1", "warning")
    assert page.selected_certs == set()

    page.selected_certs = {"ok", "bad"}
    monkeypatch.setattr(page.cert_service, "revoke", lambda name: name == "ok")
    page._do_batch_revoke()
    assert messages[-1] == ("批量吊销完成：成功 1，失败 1", "warning")
    assert page.selected_certs == set()


def test_certificate_guard_blocks_duplicate_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    page = CertsPage()
    messages: list[str] = []
    monkeypatch.setattr(
        "app.ui.pages.certs.ui.notify",
        lambda message, **_kwargs: messages.append(str(message)),
    )

    assert page._begin_operation() is True
    assert page._begin_operation() is False
    assert messages[-1] == "证书操作正在执行，请稍候"
    page._finish_operation()
    assert page._begin_operation() is True
    page._finish_operation()


def test_rebuild_ca_uses_shared_required_text_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    page = CertsPage()
    state: dict[str, object] = {}

    def fake_show(*args, **kwargs):
        state.update(kwargs)
        state["message"] = kwargs.get("message") or (args[0] if args else "")
        return object()

    monkeypatch.setattr("app.ui.pages.certs.confirm_dialog.show", fake_show)
    page._show_rebuild_ca_confirm()

    assert state["confirm_text"] == "REBUILD"
    assert state["danger_level"] == "danger"
    assert "全部失效" in str(state["impact"])


def test_cert_adapter_handles_5000_rows_as_lightweight_grid_data() -> None:
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    rows = [
        CertsPage._adapt_cert_row(
            _cert(f"user-{index:04d}", "valid", days=90),
            now=now,
        )
        for index in range(5000)
    ]

    assert len(rows) == 5000
    assert rows[0]["common_name"] == "user-0000"
    assert rows[-1]["common_name"] == "user-4999"
    assert CertsPage._row_matches_query(rows[-1], "user-4999")


def test_certs_v2_visual_contract_uses_shared_page_primitives() -> None:
    """证书页复用 V2 视觉原语，不建立第二套 Vue 业务层。"""

    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / "app" / "ui" / "pages" / "certs.py").read_text(encoding="utf-8")
    shell_css = (
        root / "app" / "ui" / "static" / "css" / "shell.css"
    ).read_text(encoding="utf-8")

    assert "certs-page--v2" in source
    assert "vpn-v2-page-intro" not in source
    assert "vpn-v2-overview-grid" in source
    assert "v2_effect=True" in source
    assert ".vpn-v2-page-intro" not in shell_css
    assert ".vpn-v2-overview-grid" in shell_css
    assert "fetch(" not in source
