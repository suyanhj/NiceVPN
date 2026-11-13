# -*- coding: utf-8 -*-
"""远端防火墙依赖预检：验证共享脚本内联与一次性包安装计划。"""

import pytest

from app.services.peer_instance.remote_peer_tools import ensure_remote_firewall_tools


def test_remote_firewall_preflight_batches_iptables_and_ipset():
    calls = []

    def fake_exec(client, command: str, *, timeout: int):
        calls.append((client, command, timeout))
        return "ready", "", 0

    client = object()
    ensure_remote_firewall_tools(
        client, "sudo -n ", fake_exec, include_ipset=True, timeout=60,
    )

    assert len(calls) == 1
    actual_client, command, timeout = calls[0]
    assert actual_client is client
    assert command.startswith("sudo -n bash -lc ")
    assert "nicevpn_ensure_commands iptables iptables ipset ipset" in command
    assert "apt-get install -y" in command
    assert "dnf|yum" in command
    assert timeout == 300


def test_remote_firewall_preflight_installs_only_iptables_without_tag_sets():
    calls = []

    def fake_exec(_client, command: str, *, timeout: int):
        calls.append(command)
        return "", "", 0

    ensure_remote_firewall_tools(
        object(), "", fake_exec, include_ipset=False, timeout=30,
    )

    assert len(calls) == 1
    assert "export NICEVPN_INCLUDE_IPSET=0" in calls[0]
    assert "nicevpn_ensure_commands iptables iptables" in calls[0]
    assert "nicevpn_ensure_commands iptables iptables ipset ipset" in calls[0]


def test_remote_firewall_preflight_reports_install_failure():
    def fake_exec(_client, _command: str, *, timeout: int):
        assert timeout == 300
        return "", "apt repository unavailable", 1

    with pytest.raises(RuntimeError, match="apt repository unavailable"):
        ensure_remote_firewall_tools(
            object(), "sudo -n ", fake_exec, include_ipset=True, timeout=30,
        )
