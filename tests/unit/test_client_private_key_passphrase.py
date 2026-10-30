# -*- coding: utf-8 -*-
"""客户端私钥口令语义测试。"""

from __future__ import annotations

import subprocess

import pytest

from app.services.easyrsa.wrapper import EasyRSAWrapper


def test_gen_req_with_passphrase_uses_passout_env_without_cli_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": list(cmd), **kwargs})
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    wrapper = EasyRSAWrapper("/opt/easy-rsa/easyrsa", "/etc/openvpn/pki")
    assert wrapper.gen_req("alice", passphrase="client-secret") is True

    call = calls[-1]
    assert call["cmd"] == ["/opt/easy-rsa/easyrsa", "gen-req", "alice"]
    assert "client-secret" not in " ".join(call["cmd"])
    assert call["env"]["EASYRSA_PASSOUT"] == "env:NICEVPN_EASYRSA_PASSOUT"
    assert call["env"]["NICEVPN_EASYRSA_PASSOUT"] == "client-secret"
    assert call["env"]["EASYRSA_BATCH"] == "1"


def test_gen_req_without_passphrase_keeps_nopass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": list(cmd), **kwargs})
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    wrapper = EasyRSAWrapper("/opt/easy-rsa/easyrsa", "/etc/openvpn/pki")
    assert wrapper.gen_req("bob") is True

    call = calls[-1]
    assert call["cmd"] == [
        "/opt/easy-rsa/easyrsa",
        "gen-req",
        "bob",
        "nopass",
    ]
    assert "NICEVPN_EASYRSA_PASSOUT" not in call["env"]


def test_create_rejects_enabled_private_key_password_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.user.crud import UserService

    class Store:
        def user_exists(self, _username):
            return False

        def get_group(self, _group_id):
            return {
                "id": "g1",
                "name": "default",
                "subnet": "10.8.1.0/24",
                "status": "active",
            }

    svc = UserService(store=Store())
    with pytest.raises(ValueError, match="客户端私钥密码"):
        svc.create(
            username="alice",
            group_id="g1",
            password_enabled=True,
            password="",
        )
