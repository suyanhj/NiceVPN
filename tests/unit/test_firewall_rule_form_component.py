# -*- coding: utf-8 -*-
"""中心/对端共用防火墙新建规则字段组件测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.ui.components.firewall_rule_form import FirewallRuleCreateFields


def _value(value):
    return SimpleNamespace(value=value)


def _fields(
    *,
    owner_type="group",
    group="g1",
    users=None,
    auto_source=False,
    peer_lan=None,
    source="",
    enabled=True,
    ccd_reader=None,
    parse_user_source=None,
) -> FirewallRuleCreateFields:
    return FirewallRuleCreateFields(
        owner_type=_value(owner_type),
        group=_value(group),
        users=_value(users or []),
        auto_source=_value(auto_source),
        peer_lan=_value(peer_lan),
        source=_value(source),
        user_hint=_value(None),
        action=_value("accept"),
        protocol=_value("tcp"),
        dest_ip=_value("172.16.0.10"),
        dest_port=_value("443"),
        enabled=_value(enabled),
        priority_mode=_value("insert"),
        priority=_value(30),
        description=_value("allow api"),
        group_map={
            "g1": {
                "id": "g1",
                "name": "开发组",
                "subnet": "10.8.0.0/24",
            }
        },
        peer_preset_by_key={
            "peer-lan": "192.168.10.0/24",
        },
        ccd_reader=ccd_reader or (lambda _users: ["10.8.0.20"]),
        parse_user_source=parse_user_source
        or (lambda text: (text, None)),
    )


def test_group_auto_source_uses_group_subnet() -> None:
    fields = _fields(auto_source=True, source="manual-value")

    values = fields.read_values()

    assert values["owner_type"] == "group"
    assert values["owner_id"] == "g1"
    assert values["source_subnet"] == "10.8.0.0/24"
    assert values["source_ips"] is None
    assert values["enabled"] is True
    assert values["priority_mode"] == "insert"
    assert values["priority_value"] == 30


def test_group_peer_lan_preset_wins_over_manual_source() -> None:
    fields = _fields(
        auto_source=False,
        peer_lan="peer-lan",
        source="10.99.0.0/24",
    )

    values = fields.read_values()

    assert values["source_subnet"] == "192.168.10.0/24"
    assert values["source_ips"] is None


def test_user_without_manual_source_uses_ccd_ips() -> None:
    calls: list[list[str]] = []
    fields = _fields(
        owner_type="user",
        group=None,
        users=["alice", "bob"],
        source="",
        ccd_reader=lambda users: (
            calls.append(list(users))
            or ["10.8.0.20", "10.8.0.21"]
        ),
    )

    values = fields.read_values()

    assert values["owner_id"] == "alice"
    assert values["source_subnet"] is None
    assert values["source_ips"] == ["10.8.0.20", "10.8.0.21"]
    assert calls == [["alice", "bob"]]


def test_user_manual_source_uses_shared_parser() -> None:
    calls: list[str] = []
    fields = _fields(
        owner_type="user",
        group=None,
        users=["alice"],
        source="10.8.0.20,10.8.0.21",
        parse_user_source=lambda text: (
            calls.append(text)
            or (None, ["10.8.0.20", "10.8.0.21"])
        ),
    )

    values = fields.read_values()

    assert values["source_subnet"] is None
    assert values["source_ips"] == ["10.8.0.20", "10.8.0.21"]
    assert calls == ["10.8.0.20,10.8.0.21"]


@pytest.mark.parametrize(
    ("owner_type", "group", "users", "message"),
    [
        ("group", None, [], "请选择组"),
        ("user", None, [], "请选择用户"),
    ],
)
def test_missing_owner_is_rejected(
    owner_type: str,
    group,
    users,
    message: str,
) -> None:
    fields = _fields(
        owner_type=owner_type,
        group=group,
        users=users,
    )

    with pytest.raises(ValueError, match=message):
        fields.read_values()


def test_user_without_source_or_ccd_is_rejected() -> None:
    fields = _fields(
        owner_type="user",
        group=None,
        users=["alice"],
        source="",
        ccd_reader=lambda _users: [],
    )

    with pytest.raises(ValueError, match="无 CCD"):
        fields.read_values()


