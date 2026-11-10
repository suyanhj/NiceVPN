"""Group and tag SSO opt-in switches are part of normal CRUD, not settings.

The global SSO switch alone must not enable WebAuth on unselected VPN users.
"""
from app.models.group import Group
from app.services.auth.enterprise_sso_policy import SsoPolicyService
from app.services.firewall.tag_service import TagService
from app.storage.sqlite_store import SqliteStore


def test_tag_create_edit_persists_sso_in_existing_form_service(tmp_path):
    db = SqliteStore(tmp_path / "tags.db")
    service = TagService(db)
    new = service.create("集团员工", "办公访问", sso_required=True)
    assert new["sso_required"] is True
    assert db.list_tags()[0]["sso_required"] is True
    updated = service.update(new["id"], name="集团内部", sso_required=False)
    assert updated["sso_required"] is False
    assert db.get_tag(new["id"])["sso_required"] is False


def test_group_edit_preserves_policy_until_explicitly_changed(tmp_path):
    from app.services.group.crud import GroupService
    db = SqliteStore(tmp_path / "groups.db")
    original = Group(
        id="eng", name="研发组", subnet="10.8.0.0/24", status="active",
        sso_required=True,
    ).model_dump()
    db.upsert_group(original)
    group_service = GroupService(db)
    group_service.update_details("eng", name="研发中心", subnet="10.8.0.0/24")
    assert db.get_group("eng")["sso_required"] is True
    group_service.update_details(
        "eng", name="研发中心", subnet="10.8.0.0/24", sso_required=False,
    )
    assert db.get_group("eng")["sso_required"] is False


def test_only_selected_group_or_tag_requires_auth(tmp_path):
    db = SqliteStore(tmp_path / "policy.db")
    db.upsert_group(Group(id="normal", name="普通组", subnet="10.8.0.0/24").model_dump())
    db.upsert_group(Group(id="office", name="办公组", subnet="10.9.0.0/24", sso_required=True).model_dump())
    db.upsert_user({"username": "normal-cn", "group_id": "normal", "status": "active"})
    db.upsert_user({"username": "office-cn", "group_id": "office", "status": "active"})
    policy = SsoPolicyService(db)
    assert policy.resolve("normal-cn", enabled=True).mode == "legacy"
    assert policy.resolve("office-cn", enabled=True).mode == "required"
    assert policy.resolve("office-cn", enabled=False).mode == "legacy"
    assert policy.resolve("unmanaged-cn", enabled=True).mode == "legacy"
    tag = TagService(db).create("强制 SSO", sso_required=True)
    db.replace_entity_tags("user", "normal-cn", [tag["id"]])
    assert policy.resolve("normal-cn", enabled=True).mode == "required"
