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
    peer_chain_commands = [cmd for cmd in client.commands if "iptables -A VPN_PEER_" in cmd]
    assert ret["masquerade_rules"] == 1
    assert len(forward_commands) == 1
    assert "-i " not in forward_commands[0]
    assert len(peer_chain_commands) == 1
    assert "-i " not in peer_chain_commands[0]
    assert len(nat_commands) == 1
    assert "-s 10.8.0.0/16" in nat_commands[0]
    assert "-d " not in nat_commands[0]
    assert "-o tun0" not in nat_commands[0]
    assert client.closed is True
