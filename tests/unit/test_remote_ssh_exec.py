# -*- coding: utf-8 -*-
"""Remote OpenVPN, iptables, and client services share the SSH command runner."""

from app.services.peer_instance import remote_openvpn, remote_peer_iptables, remote_peer_ovpn
from app.services.peer_instance.remote_ssh_exec import exec_ssh


class FakeChannel:
    def recv_exit_status(self) -> int:
        return 23


class FakeOutput:
    def __init__(self, body: bytes):
        self.body = body
        self.channel = FakeChannel()

    def read(self) -> bytes:
        return self.body


class FakeClient:
    def __init__(self):
        self.calls = []

    def exec_command(self, command: str, *, timeout: int):
        self.calls.append((command, timeout))
        return None, FakeOutput("正常".encode("utf-8")), FakeOutput(bytes([0xFF]) + b"warn")


def test_common_ssh_executor_returns_decoded_streams_and_exit_status() -> None:
    client = FakeClient()
    stdout, stderr, status = exec_ssh(client, "id -u", timeout=15)

    assert client.calls == [("id -u", 15)]
    assert stdout == "正常"
    assert stderr == chr(0xFFFD) + "warn"
    assert status == 23


def test_all_peer_modules_reuse_common_ssh_executor() -> None:
    assert remote_openvpn._exec_ssh is exec_ssh
    assert remote_peer_iptables._exec_ssh is exec_ssh
    assert remote_peer_ovpn._exec_ssh is exec_ssh
