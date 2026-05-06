# -*- coding: utf-8 -*-
"""中心侧 POSTROUTING MASQUERADE 单测。"""
from types import SimpleNamespace


def test_center_nat_uses_source_only_masquerade(monkeypatch) -> None:
    """中心 NAT 只按 VPN 源网段做 MASQUERADE，不限制目标地址。"""
    from app.core import config as core_config
    from app.services.firewall import iptables_mgr as mod
    from app.services.firewall.iptables_mgr import IptablesManager

    monkeypatch.setattr(core_config, "load_config", lambda: {"global_subnet": "10.224.0.0/16"})
    monkeypatch.setattr(mod.os, "name", "posix", raising=False)

    add_cmds: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        if cmd[:5] == ["iptables", "-t", "nat", "-S", "POSTROUTING"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:4] == ["iptables", "-t", "nat", "-C"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        if cmd[:4] == ["iptables", "-t", "nat", "-A"]:
            add_cmds.append(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:4] == ["iptables", "-t", "nat", "-D"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mgr = IptablesManager()
    monkeypatch.setattr(mgr, "_iptables_inst_comment_value", lambda: "server")

    mgr._ensure_vpn_nat_masquerade()

    assert len(add_cmds) == 1
    add = add_cmds[0]
    assert "-s" in add and "10.224.0.0/16" in add
    assert "!" not in add and "-d" not in add
    assert "MASQUERADE" in add
    assert "SNAT" not in add
    assert "--to-source" not in add
