# -*- coding: utf-8 -*-
"""对端 iptables 下发规则单测（无真实 SSH）。"""

from unittest.mock import patch

from app.services.peer_instance.remote_peer_iptables import apply_peer_site_iptables_via_ssh


class _FakeClient:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _remote_tag_plan_exec(state, *, fail_restore=False):
    import shlex

    def fake_exec(_client, command: str, *, timeout: int):
        state["commands"].append(command)
        if "iptables -S " in command:
            return "-N VPN_PEER_test\\n", "", 0
        if "command -v ipset" in command:
            return "", "", 0
        if "ipset list -n" in command:
            return "\n".join(sorted(state["sets"])), "", 0
        if "ipset create " in command:
            parts = shlex.split(command)
            state["sets"].add(parts[parts.index("create") + 1])
            return "", "", 0
        if "ipset add " in command or "ipset swap " in command:
            return "", "", 0
        if "ipset destroy " in command:
            parts = shlex.split(command)
            state["sets"].discard(parts[-1])
            return "", "", 0
        if "iptables -m set -h" in command:
            return "", "", 0
        if "iptables-restore" in command:
            return ("", "simulated restore failure", 1) if fail_restore else ("", "", 0)
        raise AssertionError(command)

    return fake_exec


def test_remote_tag_ipset_is_prepared_before_atomic_iptables_restore():
    from app.services.peer_instance.remote_peer_ipset import build_remote_tag_ipset_spec
    from app.services.peer_instance.remote_peer_iptables import replace_peer_filter_chain_plan_via_ssh

    client = _FakeClient()
    state = {"sets": set(), "commands": []}
    spec = build_remote_tag_ipset_spec("peer-1", "tag-ops", ["10.8.0.10", "10.8.0.11"])
    row = {
        "id": "peer-1",
        "ssh_host": "h",
        "ssh_username": "u",
        "ssh_password": "p",
    }
    rest = f"-m set --match-set {spec.name} src -p tcp --dport 443 -j ACCEPT"
    with patch("app.services.peer_instance.remote_peer_iptables.connect_peer_ssh_client_from_row", return_value=client):
        with patch("app.services.peer_instance.remote_peer_iptables._exec_ssh", side_effect=_remote_tag_plan_exec(state)):
            with patch("app.services.peer_instance.remote_peer_iptables._detect_sudo_prefix", return_value=""):
                replace_peer_filter_chain_plan_via_ssh(row, [rest], [spec])

    commands = state["commands"]
    create_index = next(i for i, command in enumerate(commands) if "ipset create " in command and spec.name not in command.split())
    restore_index = next(i for i, command in enumerate(commands) if "iptables-restore" in command)
    assert create_index < restore_index
    assert "--match-set " + spec.name in commands[restore_index]
    assert any("ipset swap " in command for command in commands)
    assert spec.name in state["sets"]
    assert client.closed is True


def test_remote_tag_ipset_new_sets_are_removed_when_atomic_restore_fails():
    import pytest
    from app.services.peer_instance.remote_peer_ipset import build_remote_tag_ipset_spec
    from app.services.peer_instance.remote_peer_iptables import replace_peer_filter_chain_plan_via_ssh

    client = _FakeClient()
    state = {"sets": set(), "commands": []}
    spec = build_remote_tag_ipset_spec("peer-1", "tag-ops", ["10.8.0.10"])
    row = {
        "id": "peer-1",
        "ssh_host": "h",
        "ssh_username": "u",
        "ssh_password": "p",
    }
    rest = f"-m set --match-set {spec.name} src -j ACCEPT"
    with patch("app.services.peer_instance.remote_peer_iptables.connect_peer_ssh_client_from_row", return_value=client):
        with patch("app.services.peer_instance.remote_peer_iptables._exec_ssh", side_effect=_remote_tag_plan_exec(state, fail_restore=True)):
            with patch("app.services.peer_instance.remote_peer_iptables._detect_sudo_prefix", return_value=""):
                with pytest.raises(RuntimeError, match="旧规则保持不变"):
                    replace_peer_filter_chain_plan_via_ssh(row, [rest], [spec])

    assert spec.name not in state["sets"]
    assert any("iptables-restore" in command for command in state["commands"])
    assert client.closed is True


def test_peer_snat_uses_global_subnet_as_source_without_dest_limit() -> None:
    client = _FakeClient()

    def fake_exec(_client, command: str, *, timeout: int):
        client.commands.append(command)
        return "", "", 0

    row = {
        "id": "peer-1",
        "ssh_host": "h",
        "ssh_username": "u",
        "ssh_password": "p",
        "lan_cidrs": ["192.168.10.0/24"],
        "masquerade_on_peer": True,
    }
    with patch("app.services.peer_instance.remote_peer_iptables.connect_peer_ssh_client_from_row", return_value=client):
        with patch("app.services.peer_instance.remote_peer_iptables._exec_ssh", side_effect=fake_exec):
            with patch("app.services.peer_instance.remote_peer_iptables._detect_sudo_prefix", return_value=""):
                with patch(
                    "app.services.peer_instance.remote_peer_iptables._peer_site_rules_already_current",
                    return_value=False,
                ):
                    ret = apply_peer_site_iptables_via_ssh(row, "10.8.0.0/16", force=True)

    nat_commands = [cmd for cmd in client.commands if "-t nat -I POSTROUTING" in cmd]
    forward_commands = [cmd for cmd in client.commands if "iptables -I FORWARD" in cmd]
    peer_chain_commands = [cmd for cmd in client.commands if "iptables -I VPN_PEER_" in cmd]
    assert ret["masquerade_rules"] == 1
    assert len(forward_commands) == 1
    assert "-i " not in forward_commands[0]
    assert len(peer_chain_commands) == 1
    assert "-i " not in peer_chain_commands[0]
    assert len(nat_commands) == 1
    assert "-s 10.8.0.0/16" in nat_commands[0]
    assert "-d " not in nat_commands[0]
    assert "-o tun0" not in nat_commands[0]
    assert not any("iptables -F VPN_PEER_" in cmd or "iptables -X VPN_PEER_" in cmd for cmd in client.commands)
    assert client.closed is True


def test_peer_site_refresh_preserves_remote_tag_rules_in_existing_chain() -> None:
    from app.services.firewall.iptables_mgr import IptablesManager

    client = _FakeClient()
    chain = IptablesManager.peer_chain_name_for_id("peer-1")
    tag_rule = f"-A {chain} -m set --match-set nvpnpt_1234567890_abcdef123456 src -j ACCEPT"
    old_base = (
        f'-A {chain} -s 10.8.0.0/16 -m comment '
        '--comment "ovpn-mgmt-peer peer=peer-1 role=fwd-global" -j ACCEPT'
    )

    def fake_exec(_client, command: str, *, timeout: int):
        client.commands.append(command)
        if command.endswith(f"iptables -S {chain}"):
            return f"{tag_rule}\n{old_base}\n", "", 0
        return "", "", 0

    row = {
        "id": "peer-1",
        "ssh_host": "h",
        "ssh_username": "u",
        "ssh_password": "p",
        "lan_cidrs": ["192.168.10.0/24"],
        "masquerade_on_peer": False,
    }
    with patch("app.services.peer_instance.remote_peer_iptables.connect_peer_ssh_client_from_row", return_value=client):
        with patch("app.services.peer_instance.remote_peer_iptables._exec_ssh", side_effect=fake_exec):
            with patch("app.services.peer_instance.remote_peer_iptables._detect_sudo_prefix", return_value=""):
                with patch(
                    "app.services.peer_instance.remote_peer_iptables._peer_site_rules_already_current",
                    return_value=False,
                ):
                    apply_peer_site_iptables_via_ssh(row, "10.8.0.0/16", force=True)

    assert any(f"iptables -D {chain} " in cmd and "role=fwd-global" in cmd for cmd in client.commands)
    assert any(f"iptables -I {chain} 1 -s 10.8.0.0/16" in cmd for cmd in client.commands)
    assert not any(f"iptables -D {chain} " in cmd and "nvpnpt_" in cmd for cmd in client.commands)
    assert not any(f"iptables -F {chain}" in cmd or f"iptables -X {chain}" in cmd for cmd in client.commands)
    assert client.closed is True
