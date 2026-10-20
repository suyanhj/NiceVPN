# -*- coding: utf-8 -*-
"""用户组业务增量与新版页面结构测试。"""

from __future__ import annotations

import asyncio
import json
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

    groups_dir = tmp_path / "groups"
    groups_dir.mkdir()
    original = _group("root", "默认组", "10.224.0.0/16")
    (groups_dir / "root.json").write_text(json.dumps(original), encoding="utf-8")

    monkeypatch.setattr(crud, "GROUPS_DIR", groups_dir)
    service = crud.GroupService()
    monkeypatch.setattr(service.audit, "log", lambda *_args, **_kwargs: None)

    updated = service.update_details(
        "root",
        name="全局 VPN",
        subnet="10.224.0.0/16",
    )

    assert updated["name"] == "全局 VPN"
    assert updated["subnet"] == "10.224.0.0/16"
    saved = json.loads((groups_dir / "root.json").read_text(encoding="utf-8"))
    assert saved["name"] == "全局 VPN"


def test_group_update_details_rejects_duplicate_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """编辑名称时继续保持组名唯一性。"""

    from app.services.group import crud

    groups_dir = tmp_path / "groups"
    groups_dir.mkdir()
    for group in (
        _group("root", "默认组", "10.224.0.0/16"),
        _group("dev", "开发组", "10.224.1.0/24"),
    ):
        (groups_dir / f"{group['id']}.json").write_text(json.dumps(group), encoding="utf-8")

    monkeypatch.setattr(crud, "GROUPS_DIR", groups_dir)
    service = crud.GroupService()
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
            await user.should_see("用户组管理")
            await user.should_see("新建用户组")
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
