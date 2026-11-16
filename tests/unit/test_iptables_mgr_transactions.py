# -*- coding: utf-8 -*-
"""IptablesManager 原子刷新与完整重建事务测试。

所有系统命令均在测试内模拟，不会触碰运行测试机器的真实 iptables/ipset。
"""

import subprocess
from pathlib import Path

import pytest

import app.services.firewall.iptables_mgr as iptables_module
from app.services.firewall.iptables_mgr import CHAIN_NAME, IptablesManager


class RecordingIpsetManager:
    def __init__(self, *, fail_reconcile: bool = False, commit_ok: bool = True):
        self.fail_reconcile = fail_reconcile
        self.commit_ok = commit_ok
        self.events: list[str] = []
        self.specs = None
        self.txn = object()

    def reconcile(self, specs):
        self.events.append("reconcile")
        self.specs = list(specs)
        if self.fail_reconcile:
            raise RuntimeError("simulated ipset reconcile failure")
        return self.txn

    def rollback(self, txn):
        assert txn is self.txn
        self.events.append("rollback")
        return True

    def commit(self, txn):
        assert txn is self.txn
        self.events.append("commit")
        return self.commit_ok


def _patch_tempfile(monkeypatch, tmp_path):
    real_mkstemp = iptables_module.tempfile.mkstemp

    def create_tempfile(*args, **kwargs):
        return real_mkstemp(*args, dir=tmp_path, **kwargs)

    monkeypatch.setattr(iptables_module.tempfile, "mkstemp", create_tempfile)


def _prepare_refresh(monkeypatch, tmp_path, ipsets, *, save_result=None):
    events: list[str] = []
    manager = IptablesManager(ipset_mgr=ipsets)
    monkeypatch.setattr(
        manager,
        "_iptables_save_chain_rule_lines",
        lambda chain: events.append(("save", chain)) or (
            ["-A VPN_FORWARD -s 10.8.0.0/24 -j ACCEPT"]
            if save_result is None
            else save_result
        ),
    )
    monkeypatch.setattr(
        manager,
        "_iptables_restore_chain_rule_lines",
        lambda chain, lines: events.append(("restore-chain", chain, list(lines)) or True),
    )
    monkeypatch.setattr(
        manager,
        "_generate_merged_rules_text",
        lambda merged: events.append(("generate", len(merged)))
        or f"*filter\n:{CHAIN_NAME} - [0:0]\nCOMMIT\n",
    )
    monkeypatch.setattr(manager, "_flush_chain", lambda: events.append(("flush",)))
    _patch_tempfile(monkeypatch, tmp_path)
    return manager, events


def _prepare_rebuild(monkeypatch, tmp_path, ipsets, *, forward_ok=True, input_ok=True):
    events: list[tuple] = []
    manager = IptablesManager(ipset_mgr=ipsets)
    monkeypatch.setattr(
        manager,
        "_iptables_save_filter_t_bytes",
        lambda: events.append(("save-filter",)) or b"*filter\nCOMMIT\n",
    )
    monkeypatch.setattr(
        manager,
        "_iptables_restore_filter_t_bytes",
        lambda data: events.append(("restore-filter", data)) or True,
    )
    monkeypatch.setattr(
        manager,
        "_generate_merged_rules_text",
        lambda merged: events.append(("generate", len(merged)))
        or f"*filter\n:{CHAIN_NAME} - [0:0]\nCOMMIT\n",
    )
    monkeypatch.setattr(manager, "_flush_chain", lambda: events.append(("flush",)))
    monkeypatch.setattr(
        manager,
        "_ensure_forward_hooks",
        lambda: events.append(("forward-hooks",)) or forward_ok,
    )
    monkeypatch.setattr(
        manager,
        "_ensure_input_from_vpn",
        lambda: events.append(("input-hook",)) or input_ok,
    )
    monkeypatch.setattr(
        manager,
        "_ensure_ipv4_forward_enabled",
        lambda: events.append(("ip-forward",)),
    )
    monkeypatch.setattr(
        manager,
        "_ensure_vpn_nat_masquerade",
        lambda: events.append(("nat",)),
    )
    _patch_tempfile(monkeypatch, tmp_path)
    return manager, events


def _fake_restore_run(events, *, returncode=0, exception=None):
    def run(args, **kwargs):
        assert args[0] == "iptables-restore"
        assert kwargs["timeout"] == 10
        path = Path(args[-1])
        events.append(("iptables-restore", path.read_text(encoding="utf-8")))
        if exception is not None:
            raise exception
        return subprocess.CompletedProcess(
            args,
            returncode,
            stdout="",
            stderr="simulated restore failure" if returncode else "",
        )

    return run


def test_refresh_vpn_forward_success_commits_ipset_after_restore(
    monkeypatch, tmp_path
):
    ipsets = RecordingIpsetManager()
    manager, events = _prepare_refresh(monkeypatch, tmp_path, ipsets)
    monkeypatch.setattr(
        iptables_module.subprocess,
        "run",
        _fake_restore_run(events),
    )

    assert manager.refresh_vpn_forward_only([]) is True

    assert ipsets.events == ["reconcile", "commit"]
    names = [event[0] for event in events]
    assert names.index("save") < names.index("generate") < names.index("flush")
    assert names.index("flush") < names.index("iptables-restore")
    assert events[-1][0] == "iptables-restore"
    assert not list(tmp_path.glob("iptables_vpn_forward_*.rules"))


def test_refresh_vpn_forward_aborts_before_ipset_when_snapshot_fails(
    monkeypatch, tmp_path
):
    ipsets = RecordingIpsetManager()
    manager, events = _prepare_refresh(
        monkeypatch, tmp_path, ipsets, save_result=None
    )
    monkeypatch.setattr(
        manager,
        "_iptables_save_chain_rule_lines",
        lambda _chain: None,
    )

    assert manager.refresh_vpn_forward_only([]) is False

    assert ipsets.events == []
    assert not any(event[0] == "restore-chain" for event in events)


def test_refresh_vpn_forward_reconcile_failure_restores_saved_chain(
    monkeypatch, tmp_path
):
    ipsets = RecordingIpsetManager(fail_reconcile=True)
    manager, events = _prepare_refresh(monkeypatch, tmp_path, ipsets)

    assert manager.refresh_vpn_forward_only([]) is False

    assert ipsets.events == ["reconcile"]
    restore = next(event for event in events if event[0] == "restore-chain")
    assert restore[1] == CHAIN_NAME
    assert restore[2] == ["-A VPN_FORWARD -s 10.8.0.0/24 -j ACCEPT"]


def test_refresh_vpn_forward_rule_generation_failure_rolls_back_ipset(
    monkeypatch, tmp_path
):
    ipsets = RecordingIpsetManager()
    manager, events = _prepare_refresh(monkeypatch, tmp_path, ipsets)
    monkeypatch.setattr(
        manager,
        "_generate_merged_rules_text",
        lambda _merged: (_ for _ in ()).throw(ValueError("invalid rule plan")),
    )

    assert manager.refresh_vpn_forward_only([]) is False

    assert ipsets.events == ["reconcile", "rollback"]
    assert any(event[0] == "restore-chain" for event in events)


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.TimeoutExpired(["iptables-restore"], 10),
        OSError("iptables-restore executable missing"),
    ],
    ids=["timeout", "os-error"],
)
def test_refresh_vpn_forward_restore_exception_rolls_back_both_sides(
    monkeypatch, tmp_path, failure
):
    ipsets = RecordingIpsetManager()
    manager, events = _prepare_refresh(monkeypatch, tmp_path, ipsets)
    monkeypatch.setattr(
        iptables_module.subprocess,
        "run",
        _fake_restore_run(events, exception=failure),
    )

    assert manager.refresh_vpn_forward_only([]) is False

    assert ipsets.events == ["reconcile", "rollback"]
    assert any(event[0] == "restore-chain" for event in events)
    assert not list(tmp_path.glob("iptables_vpn_forward_*.rules"))


def test_refresh_vpn_forward_gc_failure_does_not_report_kernel_failure(
    monkeypatch, tmp_path, caplog
):
    ipsets = RecordingIpsetManager(commit_ok=False)
    manager, events = _prepare_refresh(monkeypatch, tmp_path, ipsets)
    monkeypatch.setattr(
        iptables_module.subprocess,
        "run",
        _fake_restore_run(events),
    )

    assert manager.refresh_vpn_forward_only([]) is True

    assert ipsets.events == ["reconcile", "commit"]
    assert "GC 未完全成功" in caplog.text


def test_rebuild_rules_success_commits_only_after_hooks_and_nat(
    monkeypatch, tmp_path
):
    ipsets = RecordingIpsetManager()
    manager, events = _prepare_rebuild(monkeypatch, tmp_path, ipsets)
    monkeypatch.setattr(
        iptables_module.subprocess,
        "run",
        _fake_restore_run(events),
    )

    assert manager.rebuild_rules([]) is True

    names = [event[0] for event in events]
    assert names.index("iptables-restore") < names.index("forward-hooks")
    assert names.index("forward-hooks") < names.index("input-hook")
    assert names.index("input-hook") < names.index("ip-forward")
    assert names.index("ip-forward") < names.index("nat")
    assert ipsets.events == ["reconcile", "commit"]
    assert not list(tmp_path.glob("iptables_*.rules"))


def test_rebuild_rules_aborts_before_ipset_when_filter_snapshot_fails(
    monkeypatch, tmp_path
):
    ipsets = RecordingIpsetManager()
    manager, events = _prepare_rebuild(monkeypatch, tmp_path, ipsets)
    monkeypatch.setattr(manager, "_iptables_save_filter_t_bytes", lambda: None)

    assert manager.rebuild_rules([]) is False

    assert ipsets.events == []
    assert not any(event[0] == "iptables-restore" for event in events)


def test_rebuild_rules_reconcile_failure_restores_filter_snapshot(
    monkeypatch, tmp_path
):
    ipsets = RecordingIpsetManager(fail_reconcile=True)
    manager, events = _prepare_rebuild(monkeypatch, tmp_path, ipsets)

    assert manager.rebuild_rules([]) is False

    assert ipsets.events == ["reconcile"]
    assert ("restore-filter", b"*filter\nCOMMIT\n") in events


@pytest.mark.parametrize(
    ("forward_ok", "input_ok", "expected_hooks"),
    [
        (False, True, ["forward-hooks"]),
        (True, False, ["forward-hooks", "input-hook"]),
    ],
    ids=["forward-hook-failure", "input-hook-failure"],
)
def test_rebuild_rules_hook_failure_rolls_back_ipset_and_filter(
    monkeypatch, tmp_path, forward_ok, input_ok, expected_hooks
):
    ipsets = RecordingIpsetManager()
    manager, events = _prepare_rebuild(
        monkeypatch,
        tmp_path,
        ipsets,
        forward_ok=forward_ok,
        input_ok=input_ok,
    )
    monkeypatch.setattr(
        iptables_module.subprocess,
        "run",
        _fake_restore_run(events),
    )

    assert manager.rebuild_rules([]) is False

    assert ipsets.events == ["reconcile", "rollback"]
    assert ("restore-filter", b"*filter\nCOMMIT\n") in events
    actual_hooks = [event[0] for event in events if event[0] in {"forward-hooks", "input-hook"}]
    assert actual_hooks == expected_hooks
    assert not any(event[0] in {"ip-forward", "nat"} for event in events)


def test_rebuild_rules_restore_failure_rolls_back_ipset_and_filter(
    monkeypatch, tmp_path
):
    ipsets = RecordingIpsetManager()
    manager, events = _prepare_rebuild(monkeypatch, tmp_path, ipsets)
    monkeypatch.setattr(
        iptables_module.subprocess,
        "run",
        _fake_restore_run(events, returncode=1),
    )

    assert manager.rebuild_rules([]) is False

    assert ipsets.events == ["reconcile", "rollback"]
    assert ("restore-filter", b"*filter\nCOMMIT\n") in events
    assert not any(event[0] == "forward-hooks" for event in events)


def test_rebuild_rules_generation_failure_rolls_back_before_restore(
    monkeypatch, tmp_path
):
    ipsets = RecordingIpsetManager()
    manager, events = _prepare_rebuild(monkeypatch, tmp_path, ipsets)
    monkeypatch.setattr(
        manager,
        "_generate_merged_rules_text",
        lambda _merged: (_ for _ in ()).throw(ValueError("bad desired state")),
    )

    assert manager.rebuild_rules([]) is False

    assert ipsets.events == ["reconcile", "rollback"]
    assert ("restore-filter", b"*filter\nCOMMIT\n") in events


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.TimeoutExpired(["iptables-restore"], 10),
        OSError("iptables-restore executable missing"),
    ],
    ids=["timeout", "os-error"],
)
def test_rebuild_rules_restore_exception_rolls_back_ipset_and_filter(
    monkeypatch, tmp_path, failure
):
    ipsets = RecordingIpsetManager()
    manager, events = _prepare_rebuild(monkeypatch, tmp_path, ipsets)
    monkeypatch.setattr(
        iptables_module.subprocess,
        "run",
        _fake_restore_run(events, exception=failure),
    )

    assert manager.rebuild_rules([]) is False

    assert ipsets.events == ["reconcile", "rollback"]
    assert ("restore-filter", b"*filter\nCOMMIT\n") in events
    assert not list(tmp_path.glob("iptables_*.rules"))


def test_rebuild_rules_gc_failure_keeps_successful_kernel_plan(
    monkeypatch, tmp_path, caplog
):
    ipsets = RecordingIpsetManager(commit_ok=False)
    manager, events = _prepare_rebuild(monkeypatch, tmp_path, ipsets)
    monkeypatch.setattr(
        iptables_module.subprocess,
        "run",
        _fake_restore_run(events),
    )

    assert manager.rebuild_rules([]) is True

    assert ipsets.events == ["reconcile", "commit"]
    assert "GC 未完全成功" in caplog.text
