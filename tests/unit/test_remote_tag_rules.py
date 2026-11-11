from __future__ import annotations

import pytest

from app.services.firewall.remote_tag_rules import materialize_remote_chain_rows


class FakeTagService:
    def __init__(self, addresses):
        self.addresses = addresses

    def resolve_addresses(self, tag_id):
        if tag_id == "missing":
            raise ValueError("标签不存在")
        return tuple(self.addresses)


def test_materialize_remote_tag_rule_uses_current_member_addresses():
    rows = [
        {
            "rest": "",
            "enabled": True,
            "tag_binding": {
                "tag_id": "tag-ops",
                "action": "accept",
                "protocol": "tcp",
                "dest_ip": "192.168.10.10",
                "dest_port": "443",
            },
        },
        {"rest": "-s 10.9.0.0/24 -j DROP", "enabled": True},
    ]

    rests = materialize_remote_chain_rows(
        rows,
        tag_service=FakeTagService(["10.8.0.10", "10.8.0.11"]),
    )

    assert len(rests) == 3
    assert any("-s 10.8.0.10" in rest and "--dport 443" in rest for rest in rests)
    assert any("-s 10.8.0.11" in rest and "--dport 443" in rest for rest in rests)
    assert rests[-1] == "-s 10.9.0.0/24 -j DROP"


def test_empty_remote_tag_is_fail_closed_and_does_not_create_unrestricted_rule():
    rows = [{
        "rest": "",
        "enabled": True,
        "tag_binding": {
            "tag_id": "tag-empty",
            "action": "accept",
            "protocol": "all",
            "dest_ip": "192.168.10.10",
            "dest_port": "",
        },
    }]

    assert materialize_remote_chain_rows(rows, tag_service=FakeTagService([])) == []


def test_missing_remote_tag_fails_closed():
    rows = [{
        "rest": "",
        "enabled": True,
        "tag_binding": {
            "tag_id": "missing",
            "action": "accept",
            "protocol": "all",
        },
    }]

    with pytest.raises(RuntimeError, match="标签不存在"):
        materialize_remote_chain_rows(rows, tag_service=FakeTagService([]))
