# -*- coding: utf-8 -*-
"""对端 SSH 私钥：对端优先、否则全局。"""

from app.services.peer_instance.peer_ssh_connect import (
    effective_ssh_private_key_for_peer,
    peer_row_has_usable_ssh_auth,
)


def test_effective_key_peer_takes_precedence() -> None:
    row = {
        "ssh_private_key": "PEER_KEY",
        "ssh_private_key_passphrase": "pp1",
    }
    cfg = {"global_ssh_private_key": "GLOB", "global_ssh_private_key_passphrase": "gp"}
    k, p = effective_ssh_private_key_for_peer(row, cfg)
    assert k == "PEER_KEY"
    assert p == "pp1"


def test_effective_key_global_when_peer_empty() -> None:
    row: dict = {"ssh_private_key": "  "}
    cfg = {"global_ssh_private_key": "G", "global_ssh_private_key_passphrase": "gp"}
    k, p = effective_ssh_private_key_for_peer(row, cfg)
    assert k == "G"
    assert p == "gp"


def test_peer_row_has_auth_password_only() -> None:
    assert peer_row_has_usable_ssh_auth({"ssh_password": "x"}) is True


def test_peer_row_has_auth_global_only(monkeypatch) -> None:
    from app.services.peer_instance import peer_ssh_connect as m

    def fake_load():
        from box import Box

        return Box({"global_ssh_private_key": "K"})

    monkeypatch.setattr(m, "load_config", fake_load)
    assert peer_row_has_usable_ssh_auth({}) is True


def test_peer_row_has_auth_none(monkeypatch) -> None:
    from app.services.peer_instance import peer_ssh_connect as m

    def fake_load():
        from box import Box

        return Box({})

    monkeypatch.setattr(m, "load_config", fake_load)
    assert peer_row_has_usable_ssh_auth({}) is False
