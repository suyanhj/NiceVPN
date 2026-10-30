# -*- coding: utf-8 -*-
"""证书续签后的 OVPN 重建闭环测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.cert.cert_service import CertService
from app.services.user.crud import UserService


class _Config(dict):
    @property
    def pki_dir(self) -> str:
        return str(self["pki_dir"])


class _Store:
    def __init__(self, user: dict, group: dict) -> None:
        self.user = dict(user)
        self.group = dict(group)
        self.saved: list[dict] = []

    def get_user(self, username: str):
        return dict(self.user) if username == self.user["username"] else None

    def get_group(self, group_id: str):
        return dict(self.group) if group_id == self.group["id"] else None

    def upsert_user(self, row: dict) -> None:
        self.user = dict(row)
        self.saved.append(dict(row))


def test_regenerate_ovpn_uses_current_cert_and_preserves_encrypted_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pki = tmp_path / "pki"
    (pki / "issued").mkdir(parents=True)
    (pki / "private").mkdir(parents=True)
    (pki / "tc2-clients").mkdir(parents=True)

    (pki / "ca.crt").write_text("CA-CERT", encoding="utf-8")
    (pki / "issued" / "alice.crt").write_text("NEW-CLIENT-CERT", encoding="utf-8")
    (pki / "private" / "alice.key").write_text(
        "-----BEGIN ENCRYPTED PRIVATE KEY-----\nENCRYPTED-KEY\n"
        "-----END ENCRYPTED PRIVATE KEY-----",
        encoding="utf-8",
    )
    (pki / "tc2-clients" / "alice.key").write_text("TC2-CLIENT-KEY", encoding="utf-8")

    store = _Store(
        {
            "username": "alice",
            "group_id": "g1",
            "status": "active",
            "ovpn_file_path": "old.ovpn",
        },
        {"id": "g1", "subnet": "10.8.1.0/24", "status": "active"},
    )
    cfg = _Config(
        pki_dir=str(pki),
        server_ip="vpn.example.com",
        port=1194,
        proto="udp",
    )

    monkeypatch.setattr("app.services.user.crud.load_config", lambda: cfg)
    monkeypatch.setattr(
        "app.services.user.crud.sync_openvpn_runtime_permissions_from_config",
        lambda: None,
    )
    monkeypatch.setattr(
        "app.services.user.ovpn_gen.OVPN_PROFILES_DIR",
        tmp_path / "ovpn",
    )

    path = Path(UserService(store=store).regenerate_ovpn("alice"))
    text = path.read_text(encoding="utf-8")

    assert "remote vpn.example.com 1194" in text
    assert "NEW-CLIENT-CERT" in text
    assert "BEGIN ENCRYPTED PRIVATE KEY" in text
    assert "ENCRYPTED-KEY" in text
    assert "TC2-CLIENT-KEY" in text
    assert store.saved[-1]["ovpn_file_path"] == str(path)


class _Audit:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def log(self, **kwargs) -> None:
        self.entries.append(dict(kwargs))


class _Wrapper:
    def __init__(self) -> None:
        self.renewed: list[str] = []

    def renew(self, username: str) -> bool:
        self.renewed.append(username)
        return True

    def get_cert_info(self, username: str) -> dict:
        return {"common_name": username, "serial": "NEW-SERIAL"}


def test_cert_renew_updates_serial_and_regenerates_user_ovpn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = _Wrapper()
    audit = _Audit()
    calls: list[tuple[str, str]] = []

    class FakeUserService:
        def get(self, username: str):
            return {"username": username, "status": "active"}

        def update_cert_serial(self, username: str, serial: str) -> None:
            calls.append(("serial", f"{username}:{serial}"))

        def regenerate_ovpn(self, username: str) -> str:
            calls.append(("ovpn", username))
            return f"/tmp/{username}.ovpn"

    monkeypatch.setattr(
        "app.services.user.crud.UserService",
        FakeUserService,
    )
    monkeypatch.setattr(
        "app.services.cert.cert_service.sync_openvpn_runtime_permissions_from_config",
        lambda: None,
    )

    svc = object.__new__(CertService)
    svc._audit = audit
    svc._get_wrapper = lambda: wrapper

    assert svc.renew("alice") is True
    assert wrapper.renewed == ["alice"]
    assert ("serial", "alice:NEW-SERIAL") in calls
    assert ("ovpn", "alice") in calls
    assert audit.entries[-1]["result"] == "success"


def test_cert_renew_reports_failure_when_ovpn_regeneration_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = _Wrapper()
    audit = _Audit()

    class FakeUserService:
        def get(self, username: str):
            return {"username": username, "status": "active"}

        def update_cert_serial(self, username: str, serial: str) -> None:
            return None

        def regenerate_ovpn(self, username: str) -> str:
            raise OSError("disk full")

    monkeypatch.setattr(
        "app.services.user.crud.UserService",
        FakeUserService,
    )
    monkeypatch.setattr(
        "app.services.cert.cert_service.sync_openvpn_runtime_permissions_from_config",
        lambda: None,
    )

    svc = object.__new__(CertService)
    svc._audit = audit
    svc._get_wrapper = lambda: wrapper

    assert svc.renew("alice") is False
    assert audit.entries[-1]["result"] == "failure"
    assert "OVPN" in audit.entries[-1]["detail"]
