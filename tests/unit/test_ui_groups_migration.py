# -*- coding: utf-8 -*-
"""用户组业务增量与新版页面结构测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from nicegui.testing import user_simulation

from app.ui.pages.groups import GroupsPage


def _group(group_id: str, name: str, subnet: str, *, status: str = "active", users: int = 0) -> dict:
    return {
        "id": group_id,
        "name": name,
        "subnet": subnet,
        "status": status,
        "user_count": users,
        "firewall_rule_ids": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


def test_group_update_details_can_rename_without_changing_subnet(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """OpenSpec 要求编辑名称或网段，名称修改不应触碰 OpenVPN 配置。"""

    from app.services.group import crud

    from app.storage.sqlite_store import SqliteStore

    store = SqliteStore(tmp_path / "db.sqlite")
    original = _group("root", "默认组", "10.224.0.0/16")
    store.upsert_group(original)

    service = crud.GroupService(store)
    monkeypatch.setattr(service.audit, "log", lambda *_args, **_kwargs: None)

    updated = service.update_details(
        "root",
        name="全局 VPN",
        subnet="10.224.0.0/16",
    )

    assert updated["name"] == "全局 VPN"
    assert updated["subnet"] == "10.224.0.0/16"
    saved = store.get_group("root")
    assert saved is not None
    assert saved["name"] == "全局 VPN"


def test_group_update_details_rejects_duplicate_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """编辑名称时继续保持组名唯一性。"""

    from app.services.group import crud

    from app.storage.sqlite_store import SqliteStore

    store = SqliteStore(tmp_path / "db.sqlite")
    for group in (
        _group("root", "默认组", "10.224.0.0/16"),
        _group("dev", "开发组", "10.224.1.0/24"),
    ):
        store.upsert_group(group)

    service = crud.GroupService(store)
    monkeypatch.setattr(service.audit, "log", lambda *_args, **_kwargs: None)

    with pytest.raises(ValueError, match="组名已存在"):
        service.update_details(
            "dev",
            name="默认组",
            subnet="10.224.1.0/24",
        )


def test_groups_page_uses_shared_header_status_bulk_bar_and_root_protection() -> None:
    """用户组页迁移到统一结构，并明确保护根组。"""

    page_object = GroupsPage()

    class FakeGroupService:
        def list_all(self):
            return [
                _group("root-group-id", "默认组", "10.224.0.0/16", users=2),
                _group(
                    "dev-group-id",
                    "开发组",
                    "10.224.1.0/24",
                    status="disabled",
                ),
            ]

    page_object.group_service = FakeGroupService()

    async def verify_page() -> None:
        async with user_simulation(page_object.render) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("新建用户组")
            await user.should_see("用户组总数")
            await user.should_see("启用分组")
            await user.should_see("停用分组")
            await user.should_see("组内用户")
            await user.should_see("已选择 0 项")
            await user.should_see("默认组")
            await user.should_see("根组")
            await user.should_see("启用")
            await user.should_see("开发组")
            await user.should_see("停用")
            await user.should_see("root-group-id")
            await user.should_see("根组定义全局 VPN 子网，不允许删除")

    asyncio.run(verify_page())

    assert page_object.bulk_bar is not None
    assert page_object.bulk_enable_button._props["disable"] is True
    assert page_object.bulk_disable_button._props["disable"] is True
    assert page_object.bulk_delete_button._props["disable"] is True


def test_group_view_model_covers_root_disabled_and_missing_fields() -> None:
    """分组适配器统一根组、停用和字段缺失语义。"""

    root = GroupsPage._build_group_view_model(
        _group("root", "默认组", "10.224.0.0/16"),
        root_id="root",
    )
    disabled = GroupsPage._build_group_view_model(
        _group("dev", "开发组", "10.224.1.0/24", status="disabled", users=3),
        root_id="root",
    )
    partial = GroupsPage._build_group_view_model(
        {"id": "partial"},
        root_id="root",
    )

    assert root["is_root"] is True
    assert root["status"] == "active"
    assert disabled["is_root"] is False
    assert disabled["status"] == "disabled"
    assert disabled["user_count"] == 3
    assert partial["name"] == "未命名用户组"
    assert partial["subnet"] == "未配置子网"
    assert partial["user_count"] == 0


def test_groups_v2_visual_contract_reuses_shared_overview_grid() -> None:
    """Groups 与 Certs 共用 V2 页面原语，不新增页面专用 Vue 业务层。"""

    root = Path(__file__).resolve().parents[2]
    source = (root / "app" / "ui" / "pages" / "groups.py").read_text(encoding="utf-8")
    shell_css = (
        root / "app" / "ui" / "static" / "css" / "shell.css"
    ).read_text(encoding="utf-8")

    assert "groups-page--v2" in source
    assert "vpn-v2-page-intro" not in source
    assert "vpn-v2-overview-grid" in source
    assert "compact=True" in source
    assert "helper_tooltip=" in source
    assert 'helper="含根组与业务子组"' not in source
    assert "v2_effect=True" not in source
    assert "render_page_toolbar" not in source
    assert "create_action(" in source
    assert "groups-create-action" not in source
    assert "primary_action" not in source
    assert ".vpn-action--create" in shell_css
    assert ".groups-page--v2 .groups-create-action" not in shell_css
    assert ".groups-page--v2 .vpn-bulk-bar" in shell_css
    assert ".groups-page--v2 .mgmt-panel-list" in shell_css
    assert ".groups-page--v2 .groups-record-card" in shell_css
    assert ".groups-page--v2 .groups-record-card--root" in shell_css
    assert ".groups-page--v2 .groups-record-card--disabled" in shell_css
    assert ".groups-page--v2 .groups-root-badge" in shell_css
    assert "background: #0c0c0d !important;" in shell_css
    assert "color: #15bde8 !important;" in shell_css
    assert source.index("self._render_summary_metrics(summary)") < source.index(
        "self.bulk_bar = render_bulk_action_bar"
    )
    assert "self.group_service.list_page(" not in source
    assert "page_method(" in source
    assert "render_server_pager(" in source
    assert ".vpn-v2-overview-grid" in shell_css
    assert ".vpn-metric-card--compact" in shell_css
    assert "fetch(" not in source
