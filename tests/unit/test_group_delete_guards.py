"""用户组删除必须保护所有仍在使用中的关联。"""

import pytest

from app.services.group.crud import GroupService
from app.storage.sqlite_store import SqliteStore


def _seed_groups(store: SqliteStore) -> None:
    store.upsert_group({
        "id": "root",
        "name": "根组",
        "subnet": "10.8.0.0/16",
        "status": "active",
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    store.upsert_group({
        "id": "g1",
        "name": "业务组",
        "subnet": "10.8.1.0/24",
        "status": "active",
        "created_at": "2026-01-02T00:00:00+00:00",
    })


def test_group_delete_is_blocked_by_tag_membership(tmp_path):
    store = SqliteStore(tmp_path / "group-tag.db")
    _seed_groups(store)
    store.create_tag("tag-office", "办公标签")
    store.set_tag_members("tag-office", group_ids=["g1"])

    service = GroupService(store)
    with pytest.raises(ValueError, match="仍被 1 个标签关联"):
        service.delete("g1")

    assert store.get_group("g1") is not None
    assert store.get_tag_members("tag-office")["group_ids"] == ["g1"]


def test_group_delete_is_blocked_by_peer_mesh_group_scope(tmp_path):
    store = SqliteStore(tmp_path / "group-mesh.db")
    _seed_groups(store)
    store.upsert_peer({
        "id": "peer-1",
        "name": "分支对端",
        "bound_username": "",
        "mesh_route_visible_group_ids": ["g1"],
    })

    service = GroupService(store)
    with pytest.raises(ValueError, match="仍被 1 个对端 Mesh 路由范围引用"):
        service.delete("g1")

    assert store.get_group("g1") is not None
    assert store.get_peer("peer-1")["mesh_route_visible_group_ids"] == ["g1"]


def test_group_delete_block_reasons_report_all_reference_types(tmp_path):
    store = SqliteStore(tmp_path / "group-blockers.db")
    _seed_groups(store)
    store.upsert_user({
        "username": "alice",
        "group_id": "g1",
        "status": "active",
    })
    store.create_tag("tag-office", "办公标签")
    store.set_tag_members("tag-office", group_ids=["g1"])
    store.replace_firewall_owner("g1", [{
        "id": "group-rule",
        "owner_id": "g1",
        "owner_type": "group",
        "priority": 10,
        "enabled": False,
        "action": "drop",
        "protocol": "all",
    }])
    store.upsert_peer({
        "id": "peer-1",
        "name": "分支对端",
        "bound_username": "",
        "mesh_route_visible_group_ids": ["g1"],
    })

    reasons = GroupService(store).delete_block_reasons(["g1", "missing"])
    reason = reasons["g1"]
    assert "组内有 1 个用户" in reason
    assert "仍被 1 个标签关联" in reason
    assert "仍被 1 条中心防火墙规则引用" in reason
    assert "仍被 1 个对端 Mesh 路由范围引用" in reason
    assert reasons["missing"] == ""


def test_repository_rechecks_group_references_inside_delete_transaction(tmp_path):
    store = SqliteStore(tmp_path / "group-atomic.db")
    _seed_groups(store)
    store.create_tag("tag-office", "办公标签")
    store.set_tag_members("tag-office", group_ids=["g1"])

    # 即使绕过 GroupService 直接调用仓储层，也不能级联删除 group_tags。
    with pytest.raises(ValueError, match="仍被 1 个标签关联"):
        store.delete_group("g1")

    assert store.get_group("g1") is not None
    assert store.get_tag_members("tag-office")["group_ids"] == ["g1"]


def test_group_without_any_reference_can_still_be_deleted(tmp_path, monkeypatch):
    store = SqliteStore(tmp_path / "group-free.db")
    _seed_groups(store)
    service = GroupService(store)
    monkeypatch.setattr(service.audit, "log", lambda *_args, **_kwargs: None)

    assert service.delete("g1") is True
    assert store.get_group("g1") is None
    assert store.get_group("root") is not None


def test_groups_page_loads_delete_block_reasons_in_one_batch():
    from app.ui.pages.groups import GroupsPage

    class FakeGroupService:
        def list_page(self, **_kwargs):
            return ([{
                "id": "g1",
                "name": "业务组",
                "subnet": "10.8.1.0/24",
                "status": "active",
                "user_count": 0,
            }], 1)

        def first_group_id(self):
            return "root"

        def delete_block_reasons(self, group_ids):
            assert group_ids == ["g1"]
            return {"g1": "用户组无法删除：仍被 1 个标签关联。请先解除关联后重试"}

    page = GroupsPage()
    page.group_service = FakeGroupService()
    groups, total, root_id = page._load_group_page()

    assert total == 1
    assert root_id == "root"
    assert groups[0]["delete_block_reason"] == (
        "用户组无法删除：仍被 1 个标签关联。请先解除关联后重试"
    )
