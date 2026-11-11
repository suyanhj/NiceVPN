# -*- coding: utf-8 -*-
"""标签关联、运行时地址集合与防火墙规则防越权回归。"""
import pytest

from app.models.firewall import FirewallRule
from app.services.firewall.ipset_mgr import IpsetManager
from app.services.firewall.iptables_mgr import IptablesManager
from app.services.firewall.rule_service import FirewallRuleService
from app.services.firewall.tag_service import TagService
from app.storage.sqlite_store import SqliteStore


@pytest.fixture
def store(tmp_path):
    db = SqliteStore(tmp_path / "nicevpn.sqlite")
    db.upsert_group({"id": "g1", "name": "销售", "subnet": "10.8.1.0/24", "status": "active"})
    db.upsert_user({"username": "alice", "group_id": "g1", "status": "active"})
    db.upsert_user({"username": "bob", "group_id": "g1", "status": "disabled"})
    return db


def test_schema_v4_tag_members_and_replace_are_idempotent(store):
    assert store.schema_version() == 12
    store.create_tag("tag1", "办公")
    store.set_tag_members("tag1", usernames=["alice", "alice"], group_ids=["g1"])
    assert store.get_tag_members("tag1") == {"usernames": ["alice"], "group_ids": ["g1"]}
    store.set_tag_members("tag1", usernames=[], group_ids=[])
    assert store.get_tag_members("tag1") == {"usernames": [], "group_ids": []}
    assert [tag["name"] for tag in store.list_tags()] == ["办公"]


def test_tag_summary_and_detail_pages_only_return_requested_slice(store):
    store.create_tag("tag1", "办公")
    store.create_tag("tag2", "隔离")
    for i in range(45):
        store.upsert_user({"username": f"person-{i:03}", "group_id": "g1", "status": "active"})
    names = [f"person-{i:03}" for i in range(45)]
    store.set_tag_members("tag1", usernames=names, group_ids=["g1"])
    store.replace_firewall_owner("tag1", [
        FirewallRule(id="tag-rule", owner_type="tag", owner_id="tag1",
                     action="drop", priority=10).model_dump()
    ])

    counts = store.list_tag_counts()
    assert counts["tag1"] == {"users": 45, "groups": 1, "rules": 1}
    assert "tag2" not in counts  # 空标签由页面默认零计数处理

    first, total = store.list_tag_detail_page("tag1", "users", limit=20)
    second, again = store.list_tag_detail_page("tag1", "users", offset=20, limit=20)
    final, _ = store.list_tag_detail_page("tag1", "users", offset=40, limit=20)
    assert total == again == 45
    assert list(map(lambda row: row["username"], first)) == names[:20]
    assert list(map(lambda row: row["username"], second)) == names[20:40]
    assert len(final) == 5
    assert store.list_tag_detail_page("tag1", "users", search="person-044")[1] == 1
    groups, total_groups = store.list_tag_detail_page("tag1", "groups")
    assert total_groups == 1 and groups[0]["id"] == "g1"
    rules, total_rules = store.list_tag_detail_page("tag1", "rules")
    assert total_rules == 1 and rules[0]["id"] == "tag-rule"
    assert store.list_tag_detail_page("tag2", "users") == ([], 0)


def test_member_candidate_pagination_and_search_escape(store):
    store.upsert_user({"username": "u%literal", "group_id": "g1", "status": "active"})
    store.upsert_user({"username": "x_deleted", "group_id": "g1", "status": "deleted"})
    store.upsert_group({"id": "g2", "name": "研发二组", "subnet": "10.8.2.0/24"})
    rows, total = store.list_tag_candidates_page("users", search="%", limit=20)
    assert total == 1 and rows[0]["username"] == "u%literal"
    assert store.list_tag_candidate_ids("users", search="%") == ["u%literal"]
    assert store.list_tag_candidate_ids("users", search="") == ["alice", "bob", "u%literal"]
    rows, total = store.list_tag_candidates_page("users", search="", limit=1)
    assert len(rows) == 1 and total == 3  # alice、bob、u%literal
    groups, total = store.list_tag_candidates_page("groups", search="研发")
    assert total == 1 and groups[0]["id"] == "g2"
    assert store.list_tag_candidate_ids("groups", search="研发") == ["g2"]
    with pytest.raises(ValueError, match="未知"):
        store.list_tag_candidates_page("invalid")
    with pytest.raises(ValueError, match="未知"):
        store.list_tag_candidate_ids("invalid")
    with pytest.raises(ValueError, match="未知"):
        store.list_tag_detail_page("tag1", "invalid")


def test_more_than_sqlite_legacy_variable_limit_associations(store):
    store.create_tag("large", "多用户")
    names = [f"large-{i:04}" for i in range(1200)]
    with store.connection(write=True) as conn:
        conn.executemany(
            "INSERT INTO users(username,group_id,status,payload) VALUES (?,'g1','active',?)",
            [(name, '{"username":"' + name + '","status":"active"}') for name in names],
        )
    store.set_tag_members("large", usernames=names, group_ids=["g1"])
    assert store.list_tag_counts()["large"]["users"] == 1200
    last, total = store.list_tag_detail_page("large", "users", offset=1180, limit=20)
    assert len(last) == 20 and total == 1200
    assert store.get_tag_members("large")["usernames"] == names
    assert store.list_tag_candidate_ids("users", search="large-") == names


def test_unknown_member_does_not_mutate_existing_associations(store):
    store.create_tag("tag1", "办公")
    store.set_tag_members("tag1", usernames=["alice"])
    with pytest.raises(ValueError, match="关联用户不存在"):
        store.set_tag_members("tag1", usernames=["ghost"])
    assert store.get_tag_members("tag1")["usernames"] == ["alice"]


def test_group_and_user_addresses_dynamic_and_disabled_users_excluded(store):
    store.create_tag("tag1", "办公")
    store.set_tag_members("tag1", usernames=["bob"], group_ids=["g1"])
    svc = TagService(store)
    addresses = svc.resolve_addresses(
        "tag1", ccd_ips={"alice": "10.8.1.10", "bob": "10.8.1.11"}
    )
    assert "10.8.1.0/24" in addresses
    assert "10.8.1.10/32" in addresses
    assert "10.8.1.11/32" not in addresses
    store.upsert_group({"id": "g1", "name": "销售", "subnet": "10.9.0.0/24", "status": "active"})
    updated = svc.resolve_addresses("tag1", ccd_ips={"alice": "10.8.1.10"})
    assert "10.9.0.0/24" in updated
    assert "10.8.1.0/24" not in updated


def test_tag_rule_uses_shared_stable_src_ipset():
    mgr = IptablesManager()
    rules = [
        FirewallRule(id=r, owner_type="tag", owner_id="tag1", action="drop",
                     priority=i, source_ips=["10.8.1.10"], dest_ip="1.1.1.1")
        for i, r in enumerate(["r1", "r2"], 1)
    ]
    merged, _ = mgr._build_merged_forward_entries(rules)
    specs = mgr._desired_ipsets_for_merged_forward(merged)
    assert len(specs) == 1
    assert specs[0].name == IpsetManager.stable_name("tag", "tag1", "src")
    assert all(specs[0].name in mgr._expand_rule_lines(rule)[0] for rule in rules)


def test_empty_tag_skips_rule_instead_of_matching_all(store, monkeypatch):
    store.create_tag("empty", "空标签")
    svc = FirewallRuleService(store=store)
    monkeypatch.setattr(
        "app.services.user.crud.UserService.list_ccd_virtual_ipv4_by_username",
        lambda self: {},
    )
    rule = FirewallRule(owner_type="tag", owner_id="empty", action="accept", priority=10)
    assert svc._resolve_tag_rules([rule.model_dump()]) == []


def test_entity_tag_replace_is_atomic_and_rejects_invalid_ids(store):
    store.create_tag("tag1", "财务")
    store.create_tag("tag2", "研发")
    store.replace_entity_tags("user", "alice", ["tag1"])
    assert store.list_entity_tag_ids("user", "alice") == ["tag1"]
    with pytest.raises(ValueError, match="未知标签"):
        store.replace_entity_tags("user", "alice", ["tag2", "unknown"])
    assert store.list_entity_tag_ids("user", "alice") == ["tag1"]
    store.replace_entity_tags("user", "alice", ["tag2"])
    assert store.list_entity_tag_ids("user", "alice") == ["tag2"]


def test_failed_firewall_refresh_restores_tag_associations(store, monkeypatch):
    store.create_tag("tag1", "财务")
    store.create_tag("tag2", "研发")
    store.replace_entity_tags("user", "alice", ["tag1"])
    store.replace_firewall_owner("tag1", [
        FirewallRule(
            id="tag-rule-1", owner_type="tag", owner_id="tag1",
            action="accept", priority=100, enabled=True,
        ).model_dump()
    ])

    def fail_refresh(self):
        raise RuntimeError("iptables-restore failed")

    monkeypatch.setattr(FirewallRuleService, "refresh_vpn_forward_only", fail_refresh)
    with pytest.raises(RuntimeError, match="iptables-restore"):
        TagService(store).set_entity_tags("user", "alice", ["tag2"])
    assert store.list_entity_tag_ids("user", "alice") == ["tag1"]


def test_tag_delete_rejected_while_rule_references_tag(store):
    service = TagService(store)
    store.create_tag("tag1", "核心")
    store.replace_firewall_owner("tag1", [
        FirewallRule(id="protected-rule", owner_type="tag", owner_id="tag1",
                     action="drop", priority=100, enabled=False).model_dump()
    ])
    with pytest.raises(ValueError, match="仍被"):
        service.delete("tag1")
    assert store.get_tag("tag1") is not None
    store.replace_firewall_owner("tag1", [])
    service.delete("tag1")
    assert store.get_tag("tag1") is None


def test_tags_page_has_registered_route_and_navigation():
    from app.ui.components.app_shell import NAVIGATION_ITEMS, PAGE_HELP_BY_PATH
    import main
    from app.ui.pages.tags import TagsPage

    assert TagsPage is not None
    assert any(item.path == "/tags" for item in NAVIGATION_ITEMS)
    assert "/tags" in PAGE_HELP_BY_PATH
    assert callable(main._register_pages)


def test_tags_page_list_filters_and_independent_expansion_state():
    from app.ui.pages.tags import TagsPage

    tag = {"name": "运维终端", "description": "生产跳板机"}
    match = TagsPage._matches_filter
    assert match(tag, "跳板", "all", users=0, groups=0, rules=0)
    assert match(tag, "", "empty", users=0, groups=0, rules=0)
    assert not match(tag, "", "assigned", users=0, groups=0, rules=0)
    assert match(tag, "", "assigned", users=2, groups=0, rules=0)
    assert match(tag, "", "referenced", users=0, groups=0, rules=1)
    assert not match(tag, "无关", "all", users=1, groups=1, rules=1)

    page = TagsPage()
    page.expanded_tags = set()
    page._refresh = lambda: None
    page._toggle_tag("first")
    page._toggle_tag("second")
    assert page.expanded_tags == {"first", "second"}
    page._toggle_tag("first")
    assert page.expanded_tags == {"second"}


def test_batch_add_remove_keeps_unrelated_tags_and_is_atomic(store):
    store.create_tag("a", "A")
    store.create_tag("b", "B")
    store.create_tag("c", "C")
    store.replace_entity_tags("user", "alice", ["a"])
    before = store.batch_change_entity_tags(
        "user", ["alice", "bob"], ["b"], operation="add"
    )
    assert before == {"alice": ["a"], "bob": []}
    assert store.list_entity_tag_ids("user", "alice") == ["a", "b"]
    assert store.list_entity_tag_ids("user", "bob") == ["b"]
    store.batch_change_entity_tags("user", ["alice", "bob"], ["b"], operation="remove")
    assert store.list_entity_tag_ids("user", "alice") == ["a"]
    assert store.list_entity_tag_ids("user", "bob") == []
    with pytest.raises(ValueError, match="不存在"):
        store.batch_change_entity_tags(
            "user", ["alice", "nonexistent"], ["c"], operation="add"
        )
    assert store.list_entity_tag_ids("user", "alice") == ["a"]


def test_batch_tag_service_refreshes_once_and_rolls_back(store, monkeypatch):
    store.create_tag("a", "A")
    store.create_tag("b", "B")
    store.replace_entity_tags("user", "alice", ["a"])
    store.replace_firewall_owner("a", [
        FirewallRule(id="tag-batch-rule", owner_type="tag", owner_id="a",
                     action="drop", priority=20).model_dump()
    ])
    calls = []

    def refresh(self):
        calls.append(1)
        raise RuntimeError("iptables failure")

    monkeypatch.setattr(FirewallRuleService, "refresh_vpn_forward_only", refresh)
    with pytest.raises(RuntimeError, match="iptables failure"):
        TagService(store).batch_change_entities(
            "user", ["alice", "bob"], ["b"], operation="add"
        )
    assert len(calls) == 1
    assert store.list_entity_tag_ids("user", "alice") == ["a"]
    assert store.list_entity_tag_ids("user", "bob") == []


def test_batch_group_tag_association(store):
    store.create_tag("g-tag", "业务组")
    TagService(store).batch_change_entities(
        "group", ["g1"], ["g-tag"], operation="add"
    )
    assert store.list_entity_tag_ids("group", "g1") == ["g-tag"]
