# -*- coding: utf-8 -*-
"""对端标签 ipset 的命名、预建、回收与隔离测试。"""

from app.services.peer_instance.remote_peer_ipset import (
    RemoteIpsetSpec,
    build_remote_tag_ipset_spec,
    gc_remote_peer_tag_ipsets,
    peer_tag_ipset_name,
    peer_tag_ipset_prefix,
    peer_tag_ipset_stage_prefix,
    reconcile_remote_peer_tag_ipsets,
    referenced_peer_tag_ipsets,
)


def test_remote_tag_ipset_name_is_stable_content_addressed_and_peer_scoped():
    entries = ["10.8.0.10", "10.8.0.0/24", "10.8.0.10/32"]
    name = peer_tag_ipset_name("peer-a", "tag-1", entries)
    assert name == peer_tag_ipset_name("peer-a", "tag-1", list(reversed(entries)))
    assert name != peer_tag_ipset_name("peer-b", "tag-1", entries)
    assert name != peer_tag_ipset_name("peer-a", "tag-2", entries)
    assert name != peer_tag_ipset_name("peer-a", "tag-1", ["10.8.1.0/24"])
    assert len(name) <= 31
    assert name.startswith(peer_tag_ipset_prefix("peer-a"))


def test_remote_tag_ipset_spec_normalizes_entries():
    spec = build_remote_tag_ipset_spec(
        "peer-a", "tag-1", ["10.8.0.10", "10.8.0.0/24"],
    )
    assert isinstance(spec, RemoteIpsetSpec)
    assert spec.entries == ("10.8.0.0/24", "10.8.0.10/32")


def test_reconcile_remote_tag_ipsets_stages_swaps_and_cleans_stage():
    state = {"names": set(), "commands": []}

    def fake_exec(_client, command: str, *, timeout: int):
        state["commands"].append(command)
        if "command -v ipset" in command:
            return "", "", 0
        if "ipset list -n" in command:
            return "\n".join(sorted(state["names"])), "", 0
        if "ipset create " in command:
            parts = command.split()
            name = parts[parts.index("create") + 1]
            state["names"].add(name)
            return "", "", 0
        if "ipset add " in command:
            return "", "", 0
        if "ipset swap " in command:
            return "", "", 0
        if "ipset destroy " in command:
            name = command.split()[-1]
            state["names"].discard(name)
            return "", "", 0
        raise AssertionError(command)

    spec = build_remote_tag_ipset_spec("peer-a", "tag-1", ["10.8.0.10"])
    created = reconcile_remote_peer_tag_ipsets(
        object(), "", "peer-a", [spec], fake_exec,
    )

    assert created == {spec.name}
    assert spec.name in state["names"]
    assert not any(name.startswith(peer_tag_ipset_stage_prefix("peer-a")) for name in state["names"])
    assert any("ipset swap" in command for command in state["commands"])


def test_remote_tag_ipset_gc_only_removes_current_peer_orphans_and_stage():
    state = {
        "names": {
            peer_tag_ipset_name("peer-a", "tag-current", ["10.8.0.10"]),
            peer_tag_ipset_name("peer-a", "tag-old", ["10.8.0.11"]),
            peer_tag_ipset_name("peer-b", "tag-other", ["10.8.0.12"]),
            peer_tag_ipset_stage_prefix("peer-a") + "12345678",
        }
    }

    def fake_exec(_client, command: str, *, timeout: int):
        if "command -v ipset" in command:
            return "", "", 0
        if "ipset list -n" in command:
            return "\n".join(sorted(state["names"])), "", 0
        if "ipset destroy " in command:
            state["names"].discard(command.split()[-1])
            return "", "", 0
        raise AssertionError(command)

    keep = peer_tag_ipset_name("peer-a", "tag-current", ["10.8.0.10"])
    removed = gc_remote_peer_tag_ipsets(
        object(), "", "peer-a", [keep], fake_exec,
    )
    assert keep in state["names"]
    assert peer_tag_ipset_name("peer-a", "tag-old", ["10.8.0.11"]) not in state["names"]
    assert peer_tag_ipset_name("peer-b", "tag-other", ["10.8.0.12"]) in state["names"]
    assert len(removed) == 2


def test_raw_remote_rules_keep_referenced_peer_owned_tag_sets():
    set_a = peer_tag_ipset_name("peer-a", "tag-a", ["10.8.0.10"])
    set_b = peer_tag_ipset_name("peer-b", "tag-b", ["10.8.0.11"])
    refs = referenced_peer_tag_ipsets(
        [f"-m set --match-set {set_a} src -j ACCEPT", f"-m set --match-set {set_b} src -j DROP"],
        "peer-a",
    )
    assert refs == {set_a}
