# -*- coding: utf-8 -*-
"""声明式 ipset reconcile 单元测试。"""

from __future__ import annotations

import subprocess

import pytest

from app.models.firewall import FirewallRule
from app.services.firewall.ipset_mgr import (
    IPSET_PREFIX,
    IPSET_PREFIX_PEER,
    IPSET_PREFIX_TAG,
    IPSET_STAGE_PREFIX,
    IpsetManager,
    IpsetReconcileTransaction,
    IpsetSpec,
)
from app.services.firewall.iptables_mgr import IptablesManager


class FakeIpsetKernel:
    """最小 ipset 命令模拟器，覆盖 reconcile 所需命令。"""

    def __init__(self) -> None:
        self.sets: dict[str, set[str]] = {}
        self.commands: list[list[str]] = []
        self.fail_add_entry: str | None = None

    def run(self, args: list[str], **_kwargs) -> subprocess.CompletedProcess:
        cmd = list(args)
        self.commands.append(cmd)

        if cmd[:3] == ["ipset", "list", "-n"]:
            stdout = "\n".join(sorted(self.sets))
            if stdout:
                stdout += "\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

        if len(cmd) >= 4 and cmd[:2] == ["ipset", "create"]:
            name = cmd[2]
            if name in self.sets and "-exist" not in cmd:
                return subprocess.CompletedProcess(
                    cmd, 1, stdout="", stderr="Set cannot be created: set with the same name already exists"
                )
            self.sets.setdefault(name, set())
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        if len(cmd) >= 4 and cmd[:2] == ["ipset", "add"]:
            name = cmd[2]
            entry = cmd[3]
            if entry == self.fail_add_entry:
                return subprocess.CompletedProcess(
                    cmd, 1, stdout="", stderr="simulated add failure"
                )
            if name not in self.sets:
                return subprocess.CompletedProcess(
                    cmd, 1, stdout="", stderr="The set with the given name does not exist"
                )
            self.sets[name].add(entry)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        if len(cmd) == 4 and cmd[:2] == ["ipset", "swap"]:
            left, right = cmd[2], cmd[3]
            if left not in self.sets or right not in self.sets:
                return subprocess.CompletedProcess(
                    cmd, 1, stdout="", stderr="The set with the given name does not exist"
                )
            self.sets[left], self.sets[right] = self.sets[right], self.sets[left]
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        if len(cmd) == 3 and cmd[:2] == ["ipset", "destroy"]:
            name = cmd[2]
            if name not in self.sets:
                return subprocess.CompletedProcess(
                    cmd, 1, stdout="", stderr="The set with the given name does not exist"
                )
            del self.sets[name]
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr=f"unsupported fake command: {cmd}"
        )


def test_stable_names_are_compatible_short_and_tag_ready():
    rule_src = IpsetManager.stable_name("rule", "rule-1", "src")
    rule_dst = IpsetManager.stable_name("rule", "rule-1", "dst")
    peer_lan = IpsetManager.stable_name("peer", "peer-1", "lan")
    tag_src = IpsetManager.stable_name("tag", "tag-1", "src")

    assert rule_src == IptablesManager._rule_ipset_name("rule-1", "src")
    assert peer_lan == IptablesManager._peer_lan_ipset_name("peer-1")
    assert rule_src.startswith(f"{IPSET_PREFIX}s")
    assert rule_dst.startswith(f"{IPSET_PREFIX}d")
    assert peer_lan.startswith(f"{IPSET_PREFIX_PEER}l")
    assert tag_src.startswith(f"{IPSET_PREFIX_TAG}s")
    assert rule_src != rule_dst
    assert all(len(name) <= 31 for name in (rule_src, rule_dst, peer_lan, tag_src))


def test_normalize_entries_deduplicates_and_canonicalizes():
    assert IpsetManager.normalize_entries(
        ["10.0.0.2", "10.0.0.2/32", "10.0.0.10/24", "10.0.0.0/24"]
    ) == ("10.0.0.0/24", "10.0.0.2/32")


def test_reconcile_commit_swaps_atomically_then_gc_orphans():
    kernel = FakeIpsetKernel()
    mgr = IpsetManager(runner=kernel.run)

    stable = mgr.stable_name("rule", "r1", "src")
    orphan = mgr.stable_name("peer", "old-peer", "lan")
    kernel.sets[stable] = {"10.0.0.1/32"}
    kernel.sets[orphan] = {"172.16.0.0/16"}
    kernel.sets["manual_keep"] = {"192.0.2.0/24"}

    txn = mgr.reconcile([mgr.spec("rule", "r1", "src", ["10.0.0.2", "10.0.0.3"])])

    assert kernel.sets[stable] == {"10.0.0.2/32", "10.0.0.3/32"}
    stages = [name for name in kernel.sets if name.startswith(IPSET_STAGE_PREFIX)]
    assert len(stages) == 1
    assert kernel.sets[stages[0]] == {"10.0.0.1/32"}
    assert orphan in kernel.sets

    assert mgr.commit(txn)
    assert not any(name.startswith(IPSET_STAGE_PREFIX) for name in kernel.sets)
    assert orphan not in kernel.sets
    assert kernel.sets["manual_keep"] == {"192.0.2.0/24"}


def test_rollback_restores_old_contents_and_removes_new_stable():
    kernel = FakeIpsetKernel()
    mgr = IpsetManager(runner=kernel.run)

    old_name = mgr.stable_name("rule", "existing", "src")
    new_name = mgr.stable_name("rule", "new", "src")
    kernel.sets[old_name] = {"10.0.0.1/32"}

    txn = mgr.reconcile(
        [
            mgr.spec("rule", "existing", "src", ["10.0.0.8"]),
            mgr.spec("rule", "new", "src", ["10.0.0.9"]),
        ]
    )
    assert kernel.sets[old_name] == {"10.0.0.8/32"}
    assert kernel.sets[new_name] == {"10.0.0.9/32"}

    assert mgr.rollback(txn)
    assert kernel.sets[old_name] == {"10.0.0.1/32"}
    assert new_name not in kernel.sets
    assert not any(name.startswith(IPSET_STAGE_PREFIX) for name in kernel.sets)


def test_reconcile_failure_rolls_back_prior_swaps_without_leaks():
    kernel = FakeIpsetKernel()
    mgr = IpsetManager(runner=kernel.run)

    first = mgr.stable_name("rule", "r1", "src")
    second = mgr.stable_name("rule", "r2", "src")
    kernel.sets[first] = {"10.0.0.1/32"}
    kernel.fail_add_entry = "10.0.0.99/32"

    with pytest.raises(RuntimeError, match="simulated add failure"):
        mgr.reconcile(
            [
                mgr.spec("rule", "r1", "src", ["10.0.0.2"]),
                mgr.spec("rule", "r2", "src", ["10.0.0.99"]),
            ]
        )

    assert kernel.sets[first] == {"10.0.0.1/32"}
    assert second not in kernel.sets
    assert not any(name.startswith(IPSET_STAGE_PREFIX) for name in kernel.sets)


def test_gc_only_touches_nicevpn_namespaces():
    kernel = FakeIpsetKernel()
    mgr = IpsetManager(runner=kernel.run)

    desired = mgr.stable_name("rule", "keep", "src")
    orphan = mgr.stable_name("tag", "orphan", "src")
    stale_stage = f"{IPSET_STAGE_PREFIX}stale"
    kernel.sets[desired] = {"10.0.0.1/32"}
    kernel.sets[orphan] = {"10.0.0.2/32"}
    kernel.sets[stale_stage] = {"10.0.0.3/32"}
    kernel.sets["third_party_set"] = {"203.0.113.0/24"}

    assert mgr.gc({desired})
    assert desired in kernel.sets
    assert orphan not in kernel.sets
    assert stale_stage not in kernel.sets
    assert "third_party_set" in kernel.sets


def test_iptables_desired_plan_uses_shared_rule_and_peer_lifecycle():
    kernel = FakeIpsetKernel()
    ipset_mgr = IpsetManager(runner=kernel.run)
    mgr = IptablesManager(ipset_mgr=ipset_mgr)

    rule = FirewallRule(
        id="r-shared",
        owner_type="group",
        owner_id="g1",
        instance="vpn-main",
        action="accept",
        priority=100,
        source_ips=["10.0.0.1", "10.0.0.2"],
        dest_ip="172.16.0.10,172.16.0.20",
    )
    merged, _ = mgr._build_merged_forward_entries(
        [rule],
        [("peer-a", ["192.168.1.1", "192.168.1.2"], 200, {})],
    )
    specs = mgr._desired_ipsets_for_merged_forward(merged)
    names = {spec.name for spec in specs}

    assert IpsetManager.stable_name("rule", "r-shared", "src") in names
    assert IpsetManager.stable_name("rule", "r-shared", "dst") in names
    assert IpsetManager.stable_name("peer", "peer-a", "lan") in names


class RecordingIpsetManager:
    """只验证 IptablesManager 的事务调用顺序。"""

    def __init__(self) -> None:
        self.reconciled = False
        self.rolled_back = False
        self.committed = False

    @staticmethod
    def spec(owner_type: str, owner_id: str, purpose: str, entries) -> IpsetSpec:
        return IpsetManager.spec(owner_type, owner_id, purpose, entries)

    def reconcile(self, _specs) -> IpsetReconcileTransaction:
        self.reconciled = True
        return IpsetReconcileTransaction(desired_names=set())

    def rollback(self, _txn) -> bool:
        self.rolled_back = True
        return True

    def commit(self, _txn) -> bool:
        self.committed = True
        return True


def test_refresh_rolls_back_ipset_when_iptables_restore_fails(monkeypatch):
    import app.services.firewall.iptables_mgr as mod

    ipsets = RecordingIpsetManager()
    mgr = IptablesManager(ipset_mgr=ipsets)
    rule = FirewallRule(
        id="r1",
        owner_type="group",
        owner_id="g1",
        instance="vpn-main",
        action="accept",
        priority=100,
        source_ips=["10.0.0.1", "10.0.0.2"],
    )

    monkeypatch.setattr(mgr, "_iptables_save_chain_rule_lines", lambda _name: [])
    monkeypatch.setattr(mgr, "_iptables_restore_chain_rule_lines", lambda _name, _lines: True)
    monkeypatch.setattr(mgr, "_flush_chain", lambda: None)

    real_run = mod.subprocess.run

    def fake_run(args, *a, **kw):
        if args and args[0] == "iptables-restore":
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="restore failed")
        return real_run(args, *a, **kw)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    assert mgr.refresh_vpn_forward_only([rule]) is False
    assert ipsets.reconciled is True
    assert ipsets.rolled_back is True
    assert ipsets.committed is False
