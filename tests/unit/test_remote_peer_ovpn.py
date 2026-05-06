# -*- coding: utf-8 -*-
"""remote_peer_ovpn 路径与校验单测（无真实 SSH）。"""

from unittest.mock import patch

import pytest

from app.services.peer_instance.remote_peer_ovpn import (
    control_openvpn_client_service_via_ssh,
    deploy_openvpn_client_systemd_via_ssh,
    default_remote_ovpn_path,
    fetch_openvpn_client_service_logs_via_ssh,
    fetch_openvpn_client_service_status_via_ssh,
    _render_remote_peer_client_config,
)


class _FakeChannel:
    def __init__(self, code: int) -> None:
        self._code = code

    def recv_exit_status(self) -> int:
        return self._code


class _FakeStream:
    def __init__(self, text: str, code: int) -> None:
        self._text = text
        self.channel = _FakeChannel(code)

    def read(self) -> bytes:
        return self._text.encode("utf-8")


class _FakeSftp:
    def __init__(self) -> None:
        self.putfo_calls = 0

    def putfo(self, _src, _remote: str) -> None:
        self.putfo_calls += 1

    def close(self) -> None:
        return None


class _FakeClient:
    def __init__(self, responses: list[tuple[str, str, int]]) -> None:
        self.responses = responses
        self.commands: list[str] = []
        self.sftp = _FakeSftp()
        self.closed = False

    def exec_command(self, command: str, timeout: int):
        self.commands.append(command)
        out, err, code = self.responses.pop(0)
        return None, _FakeStream(out, code), _FakeStream(err, code)

    def open_sftp(self) -> _FakeSftp:
        return self.sftp

    def close(self) -> None:
        self.closed = True


def test_default_remote_ovpn_path() -> None:
    assert default_remote_ovpn_path("alice") == "/etc/openvpn/client/client.conf"


def test_default_remote_ovpn_path_strip() -> None:
    assert default_remote_ovpn_path("  bob  ") == "/etc/openvpn/client/client.conf"


def test_default_remote_ovpn_path_empty() -> None:
    with pytest.raises(ValueError):
        default_remote_ovpn_path("")


def test_deploy_openvpn_client_systemd_uses_official_template() -> None:
    client = _FakeClient(
        [
            ("", "", 0),  # sudo -n
            ("", "", 0),  # systemctl cat openvpn-client@.service
            ("", "", 0),  # log drop-in + daemon-reload
            ("", "", 0),  # enable
            ("", "", 0),  # restart
        ]
    )
    with patch(
        "app.services.peer_instance.remote_peer_ovpn.connect_peer_ssh_client_from_row",
        return_value=client,
    ):
        ret = deploy_openvpn_client_systemd_via_ssh({"id": "p1"})

    assert ret["ok"] is True
    assert ret["unit_source"] == "official"
    assert ret["service"] == "openvpn-client@client.service"
    assert ret["instance_config_path"] == "/etc/openvpn/client/client.conf"
    assert client.sftp.putfo_calls == 0
    assert not any("ln -sfn" in c for c in client.commands)
    assert client.closed is True


def test_deploy_openvpn_client_systemd_generates_fallback_without_official_template() -> None:
    client = _FakeClient(
        [
            ("", "", 0),  # sudo -n
            ("", "", 1),  # systemctl cat openvpn-client@.service
            ("/usr/sbin/openvpn\n", "", 0),  # command -v openvpn
            ("", "", 0),  # install unit
            ("", "", 0),  # log drop-in + daemon-reload
            ("", "", 0),  # enable
            ("", "", 0),  # restart
        ]
    )
    with patch(
        "app.services.peer_instance.remote_peer_ovpn.connect_peer_ssh_client_from_row",
        return_value=client,
    ):
        ret = deploy_openvpn_client_systemd_via_ssh({"id": "p1"})

    assert ret["ok"] is True
    assert ret["unit_source"] == "generated"
    assert ret["service"] == "openvpn-client.service"
    assert ret["unit_path"] == "/etc/systemd/system/openvpn-client.service"
    assert client.sftp.putfo_calls == 1
    assert any("systemctl daemon-reload" in c for c in client.commands)
    assert client.closed is True


def test_fetch_openvpn_client_service_status_uses_official_service() -> None:
    client = _FakeClient(
        [
            ("", "", 0),  # systemctl cat openvpn-client@.service
            ("LoadState=loaded\nActiveState=active\nSubState=running\nUnitFileState=enabled\n", "", 0),
            ("active\n", "", 0),
            ("enabled\n", "", 0),
            ("", "", 0),  # config exists
        ]
    )
    with patch(
        "app.services.peer_instance.remote_peer_ovpn.connect_peer_ssh_client_from_row",
        return_value=client,
    ):
        ret = fetch_openvpn_client_service_status_via_ssh({"id": "p1"})

    assert ret["exists"] is True
    assert ret["service"] == "openvpn-client@client.service"
    assert ret["active_state"] == "active"
    assert ret["unit_file_state"] == "enabled"
    assert ret["config_exists"] is True
    assert client.closed is True


def test_control_openvpn_client_service_restart() -> None:
    client = _FakeClient(
        [
            ("", "", 0),  # systemctl cat openvpn-client@.service
            ("", "", 0),  # sudo -n
            ("", "", 0),  # systemctl restart
        ]
    )
    with patch(
        "app.services.peer_instance.remote_peer_ovpn.connect_peer_ssh_client_from_row",
        return_value=client,
    ):
        ret = control_openvpn_client_service_via_ssh({"id": "p1"}, "restart")

    assert ret["ok"] is True
    assert ret["action"] == "restart"
    assert any("systemctl restart openvpn-client@client.service" in c for c in client.commands)
    assert client.closed is True


def test_fetch_openvpn_client_service_logs() -> None:
    client = _FakeClient(
        [
            ("", "", 0),  # systemctl cat openvpn-client@.service
            ("line1\nline2\n", "", 0),  # tail log file
        ]
    )
    with patch(
        "app.services.peer_instance.remote_peer_ovpn.connect_peer_ssh_client_from_row",
        return_value=client,
    ):
        ret = fetch_openvpn_client_service_logs_via_ssh({"id": "p1"}, lines=20)

    assert ret["ok"] is True
    assert ret["service"] == "openvpn-client@client.service"
    assert ret["log"] == "line1\nline2\n"
    assert any("tail -n 20 /etc/openvpn/log/client.log" in c for c in client.commands)
    assert client.closed is True


def test_render_remote_peer_client_config_injects_file_logs() -> None:
    text = _render_remote_peer_client_config("client\nverb 3\n<ca>\nCA\n</ca>\n")

    assert "log-append /etc/openvpn/log/client.log" in text
    assert "status /etc/openvpn/log/client-status.log 30" in text
    assert text.index("log-append /etc/openvpn/log/client.log") < text.index("<ca>")
