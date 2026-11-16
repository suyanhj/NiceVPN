# -*- coding: utf-8 -*-
"""FirewallRuleService CRUD、排序与恢复事务行为测试。"""

import json

import pytest

from app.models.firewall import FirewallRule
from app.services.firewall.rule_service import FirewallRuleService
from app.storage.sqlite_store import SqliteStore


def _payload(
    *,
    rule_id: str | None = None,
    owner_id: str = "group-a",
    owner_type: str = "group",
    priority: int = 10,
    description: str = "test rule",
    **overrides,
) -> dict:
    payload = {
        "owner_type": owner_type,
        "owner_id": owner_id,
        "instance": "vpn-main",
        "action": "accept",
        "source_subnet": "10.8.0.0/24",
        "priority": priority,
        "enabled": True,
        "description": description,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }
    if rule_id is not None:
        payload["id"] = rule_id
    payload.update(overrides)
    return payload


def _normalized(payload: dict) -> dict:
    return FirewallRule(**payload).model_dump()


def _seed(store: SqliteStore, owner_id: str, *rules: dict) -> None:
    store.replace_firewall_owner(
        owner_id,
        [_normalized(row) for row in rules],
    )


@pytest.fixture
def firewall(tmp_path, monkeypatch):
    store = SqliteStore(tmp_path / "firewall-service.sqlite")
    service = FirewallRuleService(store)
    audit_events: list[dict] = []
    monkeypatch.setattr(service.audit, "log", lambda **event: audit_events.append(event))
    return store, service, audit_events


def test_create_persists_rule_and_refreshes_only_once(firewall, monkeypatch):
    store, service, audit_events = firewall
    refreshes: list[bool] = []
    monkeypatch.setattr(service, "_refresh_vpn_forward_only", lambda: refreshes.append(True))

    created = service.create(_payload(priority=10))

    assert created["id"]
    assert store.get_firewall_owner("group-a") == [created]
    assert refreshes == [True]
    assert audit_events[-1]["action"] == "create_firewall_rule"
    assert service._prepush_snapshots == {}


def test_create_can_defer_kernel_refresh_for_initialization(firewall, monkeypatch):
    store, service, _ = firewall
    monkeypatch.setattr(
        service,
        "_refresh_vpn_forward_only",
        lambda: pytest.fail("refresh_kernel=False must not touch the kernel"),
    )

    created = service.create(_payload(priority=10), refresh_kernel=False)

    assert store.get_firewall_owner("group-a") == [created]


def test_create_rejects_priority_already_used_by_another_owner(firewall, monkeypatch):
    store, service, _ = firewall
    _seed(store, "group-b", _payload(rule_id="other", owner_id="group-b", priority=10))
    monkeypatch.setattr(
        service,
        "_refresh_vpn_forward_only",
        lambda: pytest.fail("validation must finish before kernel refresh"),
    )

    with pytest.raises(ValueError, match="优先级 10 已被占用"):
        service.create(_payload(priority=10))

    assert store.get_firewall_owner("group-a") == []
    assert [row["id"] for row in store.get_firewall_owner("group-b")] == ["other"]


def test_create_many_persists_same_owner_batch_and_refreshes_once(firewall, monkeypatch):
    store, service, audit_events = firewall
    refreshes: list[bool] = []
    monkeypatch.setattr(service, "_refresh_vpn_forward_only", lambda: refreshes.append(True))

    created = service.create_many(
        [
            _payload(priority=10, description="first"),
            _payload(priority=20, description="second"),
        ]
    )

    assert len(created) == 2
    assert len({row["id"] for row in created}) == 2
    assert [row["priority"] for row in store.get_firewall_owner("group-a")] == [10, 20]
    assert refreshes == [True]
    assert audit_events[-1]["action"] == "create_firewall_rules_batch"


@pytest.mark.parametrize(
    ("payloads", "message"),
    [
        ([], "不能为空"),
        (
            [
                _payload(owner_id="group-a", priority=10),
                _payload(owner_id="group-b", priority=20),
            ],
            "同一归属",
        ),
        (
            [
                _payload(priority=10),
                _payload(priority=10, description="duplicate"),
            ],
            "优先级重复",
        ),
    ],
)
def test_create_many_rejects_invalid_batch_without_partial_write(
    firewall, monkeypatch, payloads, message
):
    store, service, _ = firewall
    _seed(store, "group-a", _payload(rule_id="existing", priority=90))
    monkeypatch.setattr(
        service,
        "_refresh_vpn_forward_only",
        lambda: pytest.fail("invalid batch must not refresh the kernel"),
    )

    with pytest.raises(ValueError, match=message):
        service.create_many(payloads)

    assert [row["id"] for row in store.get_firewall_owner("group-a")] == ["existing"]
    assert store.get_firewall_owner("group-b") == []


def test_create_many_rejects_existing_rule_id_before_replacing_owner(firewall, monkeypatch):
    store, service, _ = firewall
    _seed(store, "group-a", _payload(rule_id="same-id", priority=10))
    monkeypatch.setattr(
        service,
        "_refresh_vpn_forward_only",
        lambda: pytest.fail("duplicate id must be rejected before refresh"),
    )

    with pytest.raises(ValueError, match="已落库的规则 ID"):
        service.create_many([_payload(rule_id="same-id", priority=20)])

    rows = store.get_firewall_owner("group-a")
    assert len(rows) == 1
    assert rows[0]["priority"] == 10


def test_update_preserves_rule_identity_and_allows_its_current_priority(firewall, monkeypatch):
    store, service, audit_events = firewall
    original = _payload(rule_id="rule-1", priority=10, description="before")
    _seed(store, "group-a", original)
    refreshes: list[bool] = []
    monkeypatch.setattr(service, "_refresh_vpn_forward_only", lambda: refreshes.append(True))

    updated = service.update_by_id("rule-1", {"priority": 10, "description": "after"})

    assert updated["id"] == "rule-1"
    assert updated["owner_id"] == "group-a"
    assert updated["description"] == "after"
    assert store.get_firewall_owner("group-a") == [updated]
    assert refreshes == [True]
    assert audit_events[-1]["action"] == "update_firewall_rule"


def test_update_rejects_priority_used_by_another_rule_without_mutation(firewall, monkeypatch):
    store, service, _ = firewall
    _seed(
        store,
        "group-a",
        _payload(rule_id="rule-1", priority=10, description="original"),
        _payload(rule_id="rule-2", priority=20),
    )
    monkeypatch.setattr(
        service,
        "_refresh_vpn_forward_only",
        lambda: pytest.fail("priority conflict must be rejected before refresh"),
    )

    with pytest.raises(ValueError, match="优先级 20 已被占用"):
        service.update_by_id("rule-1", {"priority": 20})

    rows = store.get_firewall_owner("group-a")
    assert [(row["id"], row["priority"], row["description"]) for row in rows] == [
        ("rule-1", 10, "original"),
        ("rule-2", 20, "test rule"),
    ]


def test_update_missing_rule_is_rejected(firewall, monkeypatch):
    _, service, _ = firewall
    monkeypatch.setattr(
        service,
        "_refresh_vpn_forward_only",
        lambda: pytest.fail("missing rule must not refresh the kernel"),
    )

    with pytest.raises(ValueError, match="不存在"):
        service.update_by_id("missing", {"description": "new"})


def test_delete_removes_only_target_rule_and_refreshes_once(firewall, monkeypatch):
    store, service, audit_events = firewall
    _seed(
        store,
        "group-a",
        _payload(rule_id="keep", priority=10),
        _payload(rule_id="remove", priority=20),
    )
    refreshes: list[bool] = []
    monkeypatch.setattr(service, "_refresh_vpn_forward_only", lambda: refreshes.append(True))

    assert service.delete("remove") is True

    assert [row["id"] for row in store.get_firewall_owner("group-a")] == ["keep"]
    assert refreshes == [True]
    assert audit_events[-1]["action"] == "delete_firewall_rule"


def test_delete_rolls_back_sqlite_when_kernel_refresh_fails(firewall, monkeypatch):
    store, service, _ = firewall
    original = _normalized(
        _payload(rule_id="rule-1", priority=10, description="keep on failure")
    )
    _seed(store, "group-a", original)

    def fail_refresh():
        raise RuntimeError("iptables restore failed")

    monkeypatch.setattr(service, "_refresh_vpn_forward_only", fail_refresh)

    with pytest.raises(RuntimeError, match="iptables restore failed"):
        service.delete("rule-1")

    assert store.get_firewall_owner("group-a") == [original]
    assert service._prepush_snapshots == {}


def test_reorder_applies_requested_order_and_reassigns_priorities(firewall, monkeypatch):
    store, service, _ = firewall
    _seed(
        store,
        "group-a",
        _payload(rule_id="rule-1", priority=10),
        _payload(rule_id="rule-2", priority=20),
        _payload(rule_id="rule-3", priority=30),
    )
    refreshes: list[bool] = []
    monkeypatch.setattr(service, "_refresh_vpn_forward_only", lambda: refreshes.append(True))

    assert service.reorder("group-a", ["rule-3", "rule-1", "rule-2"]) is True

    rows = store.get_firewall_owner("group-a")
    assert [row["id"] for row in rows] == ["rule-3", "rule-1", "rule-2"]
    assert [row["priority"] for row in rows] == [10, 20, 30]
    assert refreshes == [True]


def test_reorder_rolls_back_owner_snapshot_when_refresh_fails(firewall, monkeypatch):
    store, service, _ = firewall
    original = [
        _normalized(_payload(rule_id="rule-1", priority=10)),
        _normalized(_payload(rule_id="rule-2", priority=20)),
    ]
    _seed(store, "group-a", *original)
    monkeypatch.setattr(
        service,
        "_refresh_vpn_forward_only",
        lambda: (_ for _ in ()).throw(RuntimeError("rebuild failed")),
    )

    with pytest.raises(RuntimeError, match="rebuild failed"):
        service.reorder("group-a", ["rule-2", "rule-1"])

    assert store.get_firewall_owner("group-a") == original
    assert service._prepush_snapshots == {}


def test_backup_and_restore_round_trip_center_rules(firewall, monkeypatch):
    store, service, audit_events = firewall
    original = [
        _payload(rule_id="rule-1", priority=10, description="allow api"),
        _payload(rule_id="rule-2", priority=20, action="drop", enabled=False),
    ]
    _seed(store, "group-a", *original)

    class FakePeerService:
        def list_all(self):
            return []

    monkeypatch.setattr(
        "app.services.peer_instance.service.PeerService",
        FakePeerService,
    )
    backup = json.loads(service.backup())
    assert backup["version"] == 2
    assert [row["id"] for row in backup["rules_by_owner"]["group-a"]] == [
        "rule-1",
        "rule-2",
    ]
    assert backup["peers_center_forward"] == []

    _seed(store, "group-a", _payload(rule_id="temporary", priority=90))
    refreshes: list[bool] = []
    monkeypatch.setattr(service, "_refresh_vpn_forward_only", lambda: refreshes.append(True))

    assert service.restore(json.dumps(backup, ensure_ascii=False)) is True

    rows = store.get_firewall_owner("group-a")
    assert [row["id"] for row in rows] == ["rule-1", "rule-2"]
    assert rows[0]["description"] == "allow api"
    assert rows[1]["action"] == "drop"
    assert rows[1]["enabled"] is False
    assert refreshes == [True]
    assert audit_events[-1]["action"] == "restore_firewall_rules"


def test_restore_rejects_malformed_and_empty_backup(firewall, monkeypatch):
    _, service, _ = firewall
    monkeypatch.setattr(
        service,
        "_refresh_vpn_forward_only",
        lambda: pytest.fail("invalid backup must not refresh the kernel"),
    )

    assert service.restore("{not-json") is False
    assert service.restore(json.dumps({"version": 2, "rules_by_owner": {}})) is False
    assert service.restore(json.dumps({"version": 2})) is False


def test_restore_skips_invalid_rule_payload_without_losing_valid_rules(firewall, monkeypatch):
    store, service, _ = firewall
    monkeypatch.setattr(service, "_refresh_vpn_forward_only", lambda: None)
    backup = {
        "version": 2,
        "rules_by_owner": {
            "group-a": [
                _payload(rule_id="valid", priority=10),
                _payload(rule_id="invalid", priority=20, dest_port="0"),
                "not-a-rule",
            ]
        },
        "peers_center_forward": [],
    }

    assert service.restore(json.dumps(backup)) is True

    rows = store.get_firewall_owner("group-a")
    assert [row["id"] for row in rows] == ["valid"]


def test_restore_rolls_back_all_existing_owners_when_refresh_fails(firewall, monkeypatch):
    store, service, _ = firewall
    original_a = _normalized(_payload(rule_id="old-a", owner_id="group-a", priority=10))
    original_b = _normalized(_payload(rule_id="old-b", owner_id="group-b", priority=20))
    _seed(store, "group-a", original_a)
    _seed(store, "group-b", original_b)

    backup = {
        "version": 2,
        "rules_by_owner": {
            "group-a": [_payload(rule_id="new-a", owner_id="group-a", priority=30)],
            "group-b": [_payload(rule_id="new-b", owner_id="group-b", priority=40)],
        },
        "peers_center_forward": [],
    }
    monkeypatch.setattr(
        service,
        "_refresh_vpn_forward_only",
        lambda: (_ for _ in ()).throw(RuntimeError("kernel unavailable")),
    )

    assert service.restore(json.dumps(backup)) is False

    assert store.get_firewall_owner("group-a") == [original_a]
    assert store.get_firewall_owner("group-b") == [original_b]
    assert service._prepush_snapshots == {}
