# -*- coding: utf-8 -*-
"""iptables 链备份：链不存在时的行为。"""
from subprocess import CompletedProcess

from app.services.firewall.iptables_mgr import IptablesManager


def test_save_chain_rule_lines_missing_chain_returns_empty(monkeypatch):
    """VPN_FORWARD 尚未创建时，备份应视为空链而非失败。"""

    def fake_run(cmd, **kwargs):
        assert cmd[:3] == ["iptables", "-S", "VPN_FORWARD"]
        return CompletedProcess(
            cmd,
            1,
            stdout="",
            stderr="iptables: No chain/target/match by that name.",
        )

    monkeypatch.setattr("app.services.firewall.iptables_mgr.subprocess.run", fake_run)
    assert IptablesManager._iptables_save_chain_rule_lines("VPN_FORWARD") == []


def test_save_chain_rule_lines_other_error_returns_none(monkeypatch):
    def fake_run(cmd, **kwargs):
        return CompletedProcess(cmd, 1, stdout="", stderr="permission denied")

    monkeypatch.setattr("app.services.firewall.iptables_mgr.subprocess.run", fake_run)
    assert IptablesManager._iptables_save_chain_rule_lines("VPN_FORWARD") is None
