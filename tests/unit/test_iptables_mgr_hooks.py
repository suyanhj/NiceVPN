# -*- coding: utf-8 -*-
"""iptables 固定钩子、INPUT/NAT 与导入导出行为测试。"""

import json
import subprocess
from pathlib import Path

import pytest

import app.services.firewall.iptables_mgr as iptables_module
from app.services.firewall.iptables_mgr import CHAIN_NAME, IptablesManager


def _patch_tempfile(monkeypatch, tmp_path):
    real_mkstemp = iptables_module.tempfile.mkstemp

    def create_tempfile(*args, **kwargs):
        return real_mkstemp(*args, dir=tmp_path, **kwargs)

    monkeypatch.setattr(iptables_module.tempfile, "mkstemp", create_tempfile)


def _config(monkeypatch, payload):
    monkeypatch.setattr("app.core.config.load_config", lambda: payload)


def test_forward_hooks_order_requires_vpn_jump_before_established_accept(monkeypatch):
    manager = IptablesManager()
    correct = (
        '-A FORWARD -i tun+ -m comment --comment "ovpn-mgmt-jump-vpn-forward inst=vpn-a" -j VPN_FORWARD\n'
        '-A FORWARD -m conntrack --ctstate RELATED,ESTABLISHED -m comment '
        '--comment "ovpn-mgmt-ct-established inst=vpn-a" -j ACCEPT\n'
    )
    monkeypatch.setattr(
        iptables_module.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["iptables", "-S", "FORWARD"], 0, stdout=correct, stderr=""
        ),
    )
    assert manager._forward_hooks_order_ok() is True

    reversed_rules = "\n".join(reversed(correct.splitlines())) + "\n"
    monkeypatch.setattr(
        iptables_module.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["iptables", "-S", "FORWARD"], 0, stdout=reversed_rules, stderr=""
        ),
    )
    assert manager._forward_hooks_order_ok() is False


def test_forward_hooks_already_correct_do_not_rewrite_rules(monkeypatch):
    manager = IptablesManager()
    calls: list[str] = []
    monkeypatch.setattr(manager, "_remove_legacy_shared_vpn_peer_jump_and_chain", lambda: calls.append("legacy"))
    monkeypatch.setattr(manager, "_purge_center_vpn_peer_subchains", lambda: calls.append("peer-chains"))
    monkeypatch.setattr(manager, "_forward_hooks_order_ok", lambda: True)
    monkeypatch.setattr(
        manager,
        "_iptables_delete_rule_repeat",
        lambda _parts: pytest.fail("correct hooks must not be deleted"),
    )
    monkeypatch.setattr(
        iptables_module.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("correct hooks must not be re-added"),
    )

    assert manager._ensure_forward_hooks() is True
    assert calls == ["legacy", "peer-chains"]


def test_forward_hooks_rebuild_in_required_order(monkeypatch):
    manager = IptablesManager()
    calls: list[tuple] = []
    monkeypatch.setattr(manager, "_remove_legacy_shared_vpn_peer_jump_and_chain", lambda: None)
    monkeypatch.setattr(manager, "_purge_center_vpn_peer_subchains", lambda: None)
    monkeypatch.setattr(manager, "_forward_hooks_order_ok", lambda: False)
    monkeypatch.setattr(
        manager,
        "_forward_hook_specs_legacy_no_inst",
        lambda: (["FORWARD", "legacy-jump"], ["FORWARD", "legacy-ct"]),
    )
    monkeypatch.setattr(
        manager,
        "_forward_hook_specs",
        lambda: (["FORWARD", "vpn-jump"], ["FORWARD", "established-ct"]),
    )
    monkeypatch.setattr(
        manager,
        "_iptables_delete_rule_repeat",
        lambda parts: calls.append(("delete", list(parts))),
    )

    def run(command, **_kwargs):
        calls.append(("run", list(command)))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(iptables_module.subprocess, "run", run)

    assert manager._ensure_forward_hooks() is True

    assert [event[1] for event in calls if event[0] == "run"] == [
        ["iptables", "-A", "FORWARD", "vpn-jump"],
        ["iptables", "-A", "FORWARD", "established-ct"],
    ]
    assert [event[1] for event in calls if event[0] == "delete"] == [
        ["FORWARD", "legacy-jump"],
        ["FORWARD", "legacy-ct"],
        ["FORWARD", "vpn-jump"],
        ["FORWARD", "established-ct"],
    ]


def test_forward_hooks_stop_when_one_rule_cannot_be_added(monkeypatch):
    manager = IptablesManager()
    monkeypatch.setattr(manager, "_remove_legacy_shared_vpn_peer_jump_and_chain", lambda: None)
    monkeypatch.setattr(manager, "_purge_center_vpn_peer_subchains", lambda: None)
    monkeypatch.setattr(manager, "_forward_hooks_order_ok", lambda: False)
    monkeypatch.setattr(
        manager,
        "_forward_hook_specs_legacy_no_inst",
        lambda: (["FORWARD", "old-jump"], ["FORWARD", "old-ct"]),
    )
    monkeypatch.setattr(
        manager,
        "_forward_hook_specs",
        lambda: (["FORWARD", "vpn-jump"], ["FORWARD", "established-ct"]),
    )
    commands: list[list[str]] = []

    def run(command, **_kwargs):
        commands.append(list(command))
        code = 1 if command[-1] == "established-ct" else 0
        return subprocess.CompletedProcess(command, code, stdout="", stderr="permission denied")

    monkeypatch.setattr(iptables_module.subprocess, "run", run)

    assert manager._ensure_forward_hooks() is False
    assert len(commands) == 2


def test_input_accept_policy_removes_legacy_and_current_managed_rules(monkeypatch):
    manager = IptablesManager()
    _config(monkeypatch, {"global_subnet": "10.8.0.0/16"})
    monkeypatch.setattr(manager, "_iptables_inst_comment_value", lambda: "vpn-a")
    monkeypatch.setattr(manager, "_filter_table_chain_policy", lambda _table, _chain: "ACCEPT")
    deleted: list[list[str]] = []
    monkeypatch.setattr(manager, "_iptables_delete_rule_repeat", lambda parts: deleted.append(list(parts)))
    monkeypatch.setattr(
        iptables_module.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("ACCEPT policy must not add INPUT rule"),
    )

    assert manager._ensure_input_from_vpn() is True

    assert deleted == [
        manager._input_tun_rule_parts("10.8.0.0/16", legacy_no_inst=True),
        manager._input_tun_rule_parts(
            "10.8.0.0/16", legacy_no_inst=False, instance_id="vpn-a"
        ),
    ]


def test_input_non_accept_policy_adds_managed_rule_after_check_miss(monkeypatch):
    manager = IptablesManager()
    _config(monkeypatch, {"global_subnet": "10.8.0.0/16"})
    monkeypatch.setattr(manager, "_iptables_inst_comment_value", lambda: "vpn-a")
    monkeypatch.setattr(manager, "_filter_table_chain_policy", lambda _table, _chain: "DROP")
    deleted: list[list[str]] = []
    monkeypatch.setattr(manager, "_iptables_delete_rule_repeat", lambda parts: deleted.append(list(parts)))
    commands: list[list[str]] = []

    def run(command, **_kwargs):
        commands.append(list(command))
        return subprocess.CompletedProcess(command, 1 if command[1] == "-C" else 0, stdout="", stderr="")

    monkeypatch.setattr(iptables_module.subprocess, "run", run)

    assert manager._ensure_input_from_vpn() is True

    expected = manager._input_tun_rule_parts(
        "10.8.0.0/16", legacy_no_inst=False, instance_id="vpn-a"
    )
    assert commands == [["iptables", "-C", *expected], ["iptables", "-A", *expected]]
    assert deleted == [
        manager._input_tun_rule_parts("10.8.0.0/16", legacy_no_inst=True)
    ]


def test_input_non_accept_policy_returns_false_when_add_fails(monkeypatch):
    manager = IptablesManager()
    _config(monkeypatch, {"global_subnet": "10.8.0.0/16"})
    monkeypatch.setattr(manager, "_iptables_inst_comment_value", lambda: "vpn-a")
    monkeypatch.setattr(manager, "_filter_table_chain_policy", lambda _table, _chain: "DROP")
    monkeypatch.setattr(manager, "_iptables_delete_rule_repeat", lambda _parts: None)
    monkeypatch.setattr(
        iptables_module.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 1, stdout="", stderr="permission denied"
        ),
    )

    assert manager._ensure_input_from_vpn() is False


def test_input_missing_global_subnet_skips_rule_without_failing(monkeypatch):
    manager = IptablesManager()
    _config(monkeypatch, {})
    monkeypatch.setattr(
        iptables_module.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("missing subnet must not invoke iptables"),
    )

    assert manager._ensure_input_from_vpn() is True


def test_nat_masquerade_is_added_for_global_subnet_without_egress_interface(monkeypatch):
    manager = IptablesManager()
    _config(monkeypatch, {"global_subnet": "10.8.0.0/16"})
    monkeypatch.setattr(manager, "_iptables_inst_comment_value", lambda: "vpn-a")
    cleaned: list[str] = []
    monkeypatch.setattr(manager, "_delete_nat_postrouting_for_local_masq", cleaned.append)
    commands: list[list[str]] = []

    def run(command, **_kwargs):
        commands.append(list(command))
        return subprocess.CompletedProcess(
            command, 1 if command[4] == "-C" else 0, stdout="", stderr=""
        )

    monkeypatch.setattr(iptables_module.subprocess, "run", run)

    manager._ensure_vpn_nat_masquerade()

    expected = [
        "POSTROUTING", "-s", "10.8.0.0/16", "-m", "comment",
        "--comment", "ovpn-mgmt-masq inst=vpn-a", "-j", "MASQUERADE",
    ]
    assert cleaned == ["vpn-a"]
    assert commands == [
        ["iptables", "-t", "nat", "-C", *expected],
        ["iptables", "-t", "nat", "-A", *expected],
    ]
    assert "-o" not in commands[1]


def test_nat_masquerade_is_idempotent_when_rule_already_exists(monkeypatch):
    manager = IptablesManager()
    _config(monkeypatch, {"global_subnet": "10.8.0.0/16"})
    monkeypatch.setattr(manager, "_iptables_inst_comment_value", lambda: "vpn-a")
    monkeypatch.setattr(manager, "_delete_nat_postrouting_for_local_masq", lambda _iid: None)
    commands: list[list[str]] = []
    monkeypatch.setattr(
        iptables_module.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(list(command))
        or subprocess.CompletedProcess(command, 0, stdout="", stderr=""),
    )

    manager._ensure_vpn_nat_masquerade()

    assert len(commands) == 1
    assert commands[0][0:4] == ["iptables", "-t", "nat", "-C"]


def test_nat_masquerade_missing_subnet_does_not_run_iptables(monkeypatch):
    manager = IptablesManager()
    _config(monkeypatch, {})
    monkeypatch.setattr(
        iptables_module.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("missing subnet must not invoke iptables"),
    )

    manager._ensure_vpn_nat_masquerade()


def test_nat_masquerade_logs_add_failure_without_raising(monkeypatch, caplog):
    manager = IptablesManager()
    _config(monkeypatch, {"global_subnet": "10.8.0.0/16"})
    monkeypatch.setattr(manager, "_iptables_inst_comment_value", lambda: "vpn-a")
    monkeypatch.setattr(manager, "_delete_nat_postrouting_for_local_masq", lambda _iid: None)
    monkeypatch.setattr(
        iptables_module.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 1, stdout="", stderr="permission denied"
        ),
    )

    manager._ensure_vpn_nat_masquerade()

    assert "permission denied" in caplog.text


def test_export_rules_keeps_only_filter_header_vpn_chain_and_commit(monkeypatch):
    manager = IptablesManager()
    output = (
        "*filter\n"
        ":INPUT ACCEPT [0:0]\n"
        ":VPN_FORWARD - [0:0]\n"
        "-A FORWARD -j VPN_FORWARD\n"
        "-A VPN_FORWARD -s 10.8.0.0/24 -j ACCEPT\n"
        "COMMIT\n"
        "*nat\n"
        "COMMIT\n"
    )
    monkeypatch.setattr(
        iptables_module.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["iptables-save"], 0, stdout=output, stderr=""
        ),
    )

    exported = json.loads(manager.export_rules())

    assert exported["chain"] == CHAIN_NAME
    assert exported["rules_text"].splitlines() == [
        "*filter",
        ":VPN_FORWARD - [0:0]",
        "-A FORWARD -j VPN_FORWARD",
        "-A VPN_FORWARD -s 10.8.0.0/24 -j ACCEPT",
        "COMMIT",
    ]


def test_export_rules_reports_iptables_save_failure(monkeypatch):
    manager = IptablesManager()

    def fail(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, ["iptables-save"], stderr="permission denied")

    monkeypatch.setattr(iptables_module.subprocess, "run", fail)

    exported = json.loads(manager.export_rules())

    assert "error" in exported
    assert "iptables-save" in exported["error"]


def test_import_rules_restores_text_then_repairs_hooks_and_nat(monkeypatch, tmp_path):
    manager = IptablesManager()
    _patch_tempfile(monkeypatch, tmp_path)
    calls: list[str] = []

    def run(command, **_kwargs):
        assert command[0] == "iptables-restore"
        assert Path(command[-1]).read_text(encoding="utf-8") == "*filter\nCOMMIT\n\n"
        calls.append("restore")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(iptables_module.subprocess, "run", run)
    monkeypatch.setattr(manager, "_ensure_forward_hooks", lambda: calls.append("forward") or True)
    monkeypatch.setattr(manager, "_ensure_input_from_vpn", lambda: calls.append("input") or True)
    monkeypatch.setattr(manager, "_ensure_ipv4_forward_enabled", lambda: calls.append("sysctl"))
    monkeypatch.setattr(manager, "_ensure_vpn_nat_masquerade", lambda: calls.append("nat"))

    assert manager.import_rules(json.dumps({"rules_text": "*filter\nCOMMIT\n"})) is True

    assert calls == ["restore", "forward", "input", "sysctl", "nat"]
    assert not list(tmp_path.glob("iptables_import_*.rules"))


@pytest.mark.parametrize(
    "payload",
    [
        "{not-json",
        json.dumps({}),
        json.dumps({"rules_text": ""}),
    ],
)
def test_import_rules_rejects_invalid_or_empty_payload(payload):
    assert IptablesManager().import_rules(payload) is False


def test_import_rules_stops_when_restore_fails(monkeypatch, tmp_path):
    manager = IptablesManager()
    _patch_tempfile(monkeypatch, tmp_path)
    monkeypatch.setattr(
        iptables_module.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 1, stdout="", stderr="restore failed"
        ),
    )
    monkeypatch.setattr(
        manager,
        "_ensure_forward_hooks",
        lambda: pytest.fail("failed import must not mutate hooks"),
    )

    assert manager.import_rules(json.dumps({"rules_text": "*filter\nCOMMIT\n"})) is False
    assert not list(tmp_path.glob("iptables_import_*.rules"))


def test_import_rules_stops_when_forward_hook_cannot_be_repaired(monkeypatch, tmp_path):
    manager = IptablesManager()
    _patch_tempfile(monkeypatch, tmp_path)
    monkeypatch.setattr(
        iptables_module.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, stdout="", stderr=""
        ),
    )
    monkeypatch.setattr(manager, "_ensure_forward_hooks", lambda: False)
    monkeypatch.setattr(
        manager,
        "_ensure_input_from_vpn",
        lambda: pytest.fail("input hook must not run after forward hook failure"),
    )

    assert manager.import_rules(json.dumps({"rules_text": "*filter\nCOMMIT\n"})) is False
