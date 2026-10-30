# -*- coding: utf-8 -*-
"""系统一致性诊断测试。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import app.services.diagnostics.system_diagnostics as diag_mod
from app.services.diagnostics.system_diagnostics import SystemDiagnosticsService


def _service(monkeypatch: pytest.MonkeyPatch, pki: Path, base: Path):
    cfg = {
        "pki_dir": str(pki),
        "easyrsa_dir": str(base / "easyrsa"),
        "openvpn_conf_dir": str(base),
        "instances": {"server": {}},
    }
    monkeypatch.setattr(diag_mod, "load_config", lambda: cfg)
    return SystemDiagnosticsService()


def test_sqlite_quick_check_reports_ok(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "nicevpn.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY)")
        conn.commit()

    pki = tmp_path / "pki"
    service = _service(monkeypatch, pki, tmp_path)
    monkeypatch.setattr(diag_mod, "SQLITE_DB_FILE", db)

    check = service._check_sqlite()
    assert check.status == "ok"
    assert "quick_check=ok" in check.detail


def test_stale_user_ovpn_is_detected_and_safe_repair_regenerates_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "openvpn"
    pki = base / "pki"
    issued = pki / "issued"
    private = pki / "private"
    tc2 = pki / "tc2-clients"
    ccd = base / "ccd"
    ovpn_dir = base / "mgmt" / "ovpn"
    for path in (issued, private, tc2, ccd, ovpn_dir):
        path.mkdir(parents=True, exist_ok=True)

    (issued / "alice.crt").write_text("NEW-CERT", encoding="utf-8")
    (private / "alice.key").write_text("ENCRYPTED-PRIVATE-KEY", encoding="utf-8")
    (tc2 / "alice.key").write_text("TC2-KEY", encoding="utf-8")
    (ccd / "alice").write_text(
        "ifconfig-push 10.8.1.2 255.255.0.0\n",
        encoding="utf-8",
    )
    ovpn = ovpn_dir / "alice.ovpn"
    ovpn.write_text("OLD-CERT\nENCRYPTED-PRIVATE-KEY\nTC2-KEY\n", encoding="utf-8")

    user = {
        "username": "alice",
        "group_id": "g1",
        "status": "active",
        "ovpn_file_path": str(ovpn),
    }
    regenerated: list[str] = []

    class FakeUserService:
        def list_all(self):
            return [dict(user)]

        def regenerate_ovpn(self, username: str):
            regenerated.append(username)
            ovpn.write_text(
                "NEW-CERT\nENCRYPTED-PRIVATE-KEY\nTC2-KEY\n",
                encoding="utf-8",
            )
            return str(ovpn)

    monkeypatch.setattr(diag_mod, "CCD_DIR", ccd)
    monkeypatch.setattr(diag_mod, "OVPN_PROFILES_DIR", ovpn_dir)
    monkeypatch.setattr(diag_mod, "UserService", FakeUserService)
    monkeypatch.setattr(
        diag_mod,
        "sync_packaged_openvpn_scripts",
        lambda _base: None,
    )
    monkeypatch.setattr(
        diag_mod,
        "sync_openvpn_runtime_permissions_from_config",
        lambda: None,
    )

    service = _service(monkeypatch, pki, base)

    before = service._check_user_materials()
    assert before.status == "warning"
    assert "alice" in before.detail

    actions = service.safe_repair()
    assert regenerated == ["alice"]
    assert any("重建 1 个" in action for action in actions)

    after = service._check_user_materials()
    assert after.status == "ok"


def test_missing_underlying_user_material_is_not_auto_repaired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "openvpn"
    pki = base / "pki"
    pki.mkdir(parents=True)
    ccd = base / "ccd"
    ccd.mkdir(parents=True)
    ovpn_dir = base / "mgmt" / "ovpn"
    ovpn_dir.mkdir(parents=True)

    user = {
        "username": "alice",
        "group_id": "g1",
        "status": "active",
        "ovpn_file_path": str(ovpn_dir / "alice.ovpn"),
    }
    regenerated: list[str] = []

    class FakeUserService:
        def list_all(self):
            return [dict(user)]

        def regenerate_ovpn(self, username: str):
            regenerated.append(username)
            raise AssertionError("缺少 PKI 材料时不应尝试重建 OVPN")

    monkeypatch.setattr(diag_mod, "CCD_DIR", ccd)
    monkeypatch.setattr(diag_mod, "OVPN_PROFILES_DIR", ovpn_dir)
    monkeypatch.setattr(diag_mod, "UserService", FakeUserService)
    monkeypatch.setattr(diag_mod, "sync_packaged_openvpn_scripts", lambda _base: None)
    monkeypatch.setattr(
        diag_mod,
        "sync_openvpn_runtime_permissions_from_config",
        lambda: None,
    )

    service = _service(monkeypatch, pki, base)
    actions = service.safe_repair()

    assert regenerated == []
    assert any("底层材料" in action for action in actions)
