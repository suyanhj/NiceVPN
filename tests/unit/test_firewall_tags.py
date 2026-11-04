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
    assert store.schema_version() == 4
    store.create_tag("tag1", "办公")
    store.set_tag_members("tag1", usernames=["alice", "alice"], group_ids=["g1"])
    assert store.get_tag_members("tag1") == {"usernames": ["alice"], "group_ids": ["g1"]}
    store.set_tag_members("tag1", usernames=[], group_ids=[])
    assert store.get_tag_members("tag1") == {"usernames": [], "group_ids": []}
    assert [tag["name"] for tag in store.list_tags()] == ["办公"]


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
