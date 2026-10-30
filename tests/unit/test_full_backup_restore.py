# -*- coding: utf-8 -*-
"""NiceVPN 完整备份/恢复测试。"""

from __future__ import annotations

import io
import json
import sqlite3
import tarfile
from pathlib import Path

import pytest

import app.services.backup.full_backup as backup_mod
from app.models.config import SystemConfig
from app.storage.sqlite_store import SqliteStore


def _write_ready_pki(pki: Path) -> None:
    (pki / "private").mkdir(parents=True, exist_ok=True)
    (pki / "issued").mkdir(parents=True, exist_ok=True)
    (pki / "tc2-clients").mkdir(parents=True, exist_ok=True)
    (pki / "ca.crt").write_text("CA-BEFORE", encoding="utf-8")
    (pki / "private" / "ca.key").write_text("CA-PRIVATE", encoding="utf-8")
    (pki / "index.txt").write_text("", encoding="utf-8")
    (pki / "serial").write_text("01\n", encoding="utf-8")


def _prepare_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    data = tmp_path / "data"
    backups = tmp_path / "backups"
    openvpn = tmp_path / "openvpn"
    pki = openvpn / "pki"
    db = data / "nicevpn.db"
    config_file = data / "config.json"
    api_file = data / "api_basic_credentials.json"
    session_file = data / "admin_session_secret"
    remote_chains = data / "remote_peer_chains"

    data.mkdir(parents=True)
    backups.mkdir(parents=True)
    _write_ready_pki(pki)

    for path in (
        openvpn / "server",
        openvpn / "client",
        openvpn / "ccd",
        openvpn / "mgmt" / "device_bindings",
        openvpn / "mgmt" / "ovpn",
    ):
        path.mkdir(parents=True, exist_ok=True)

    (openvpn / "server" / "server.conf").write_text(
        "port 1194\n",
        encoding="utf-8",
    )
    (openvpn / "client" / "client.conf").write_text(
        "client\n",
        encoding="utf-8",
    )
    (openvpn / "ccd" / "alice").write_text(
        "ifconfig-push 10.8.1.2 255.255.0.0\n",
        encoding="utf-8",
    )
    (openvpn / "mgmt" / "device_bindings" / "alice.json").write_text(
        '{"username":"alice"}',
        encoding="utf-8",
    )
    (openvpn / "mgmt" / "ovpn" / "alice.ovpn").write_text(
        "client\n<cert>OLD-CERT</cert>\n",
        encoding="utf-8",
    )
    (openvpn / "mgmt" / "device_bind_mode").write_text(
        "weak_fingerprint\n",
        encoding="utf-8",
    )
    (openvpn / "tc2-server.key").write_text("TC2-SERVER", encoding="utf-8")

    store = SqliteStore(db)
    store.initialize()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO storage_meta(key, value) VALUES('roundtrip', 'before')"
        )
        conn.commit()

    config = SystemConfig(
        initialized=True,
        pki_dir=str(pki),
        easyrsa_dir=str(tmp_path / "easyrsa"),
        server_ip="vpn-before.example.com",
        global_subnet="10.224.0.0/16",
        instances={"server": {"port": 1194, "proto": "udp"}},
    ).model_dump()
    # openvpn_conf_dir 是历史/运行时扩展字段，当前模型允许忽略未知字段。
    config["openvpn_conf_dir"] = str(openvpn)
    config_file.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    api_file.write_text(
        '{"username":"vpn","password":"secret"}',
        encoding="utf-8",
    )
    session_file.write_text("session-secret", encoding="utf-8")

    monkeypatch.setattr(backup_mod, "BACKUPS_DIR", backups)
    monkeypatch.setattr(backup_mod, "SQLITE_DB_FILE", db)
    monkeypatch.setattr(backup_mod, "CONFIG_FILE", config_file)
    monkeypatch.setattr(backup_mod, "API_BASIC_CREDENTIALS_FILE", api_file)
    monkeypatch.setattr(backup_mod, "ADMIN_SESSION_SECRET_FILE", session_file)
    monkeypatch.setattr(backup_mod, "REMOTE_PEER_CHAINS_DIR", remote_chains)
    monkeypatch.setattr(
        backup_mod,
        "load_config",
        lambda: json.loads(config_file.read_text(encoding="utf-8")),
    )
    monkeypatch.setattr(
        backup_mod,
        "sync_packaged_openvpn_scripts",
        lambda _base: None,
    )
    monkeypatch.setattr(
        backup_mod,
        "sync_openvpn_runtime_permissions_from_config",
        lambda: None,
    )

    return {
        "data": data,
        "backups": backups,
        "openvpn": openvpn,
        "pki": pki,
        "db": db,
        "config": config_file,
        "remote_chains": remote_chains,
    }


def test_full_backup_restore_roundtrip_replaces_stale_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _prepare_env(tmp_path, monkeypatch)

    archive = backup_mod.create_full_backup(label="test")
    assert archive.is_file()
    summary = backup_mod.inspect_full_backup(archive)
    assert summary["format_version"] == 1
    assert summary["file_count"] > 0

    # 模拟备份后的运行状态变化。
    with sqlite3.connect(env["db"]) as conn:
        conn.execute(
            "UPDATE storage_meta SET value='after' WHERE key='roundtrip'"
        )
        conn.commit()

    config = json.loads(env["config"].read_text(encoding="utf-8"))
    config["server_ip"] = "vpn-after.example.com"
    env["config"].write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (env["pki"] / "ca.crt").write_text("CA-AFTER", encoding="utf-8")
    (env["openvpn"] / "ccd" / "stale-user").write_text(
        "ifconfig-push 10.8.1.99 255.255.0.0\n",
        encoding="utf-8",
    )
    env["remote_chains"].mkdir(parents=True, exist_ok=True)
    (env["remote_chains"] / "stale.json").write_text(
        '{"stale":true}',
        encoding="utf-8",
    )

    result = backup_mod.restore_full_backup(archive)
    assert result["restored"] is True
    assert result["requires_restart"] is True
    pre_restore = Path(result["pre_restore_backup"])
    assert pre_restore.is_file()
    assert backup_mod.inspect_full_backup(pre_restore)["file_count"] > 0

    restored_config = json.loads(env["config"].read_text(encoding="utf-8"))
    assert restored_config["server_ip"] == "vpn-before.example.com"
    assert (env["pki"] / "ca.crt").read_text(encoding="utf-8") == "CA-BEFORE"
    assert not (env["openvpn"] / "ccd" / "stale-user").exists()
    assert not (env["remote_chains"] / "stale.json").exists()

    with sqlite3.connect(env["db"]) as conn:
        value = conn.execute(
            "SELECT value FROM storage_meta WHERE key='roundtrip'"
        ).fetchone()[0]
    assert value == "before"


def test_backup_rejects_incomplete_pki(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _prepare_env(tmp_path, monkeypatch)
    (env["pki"] / "serial").unlink()

    with pytest.raises(ValueError, match="PKI 不完整"):
        backup_mod.create_full_backup(label="broken")


def test_backup_rejects_path_traversal_archive(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tar.gz"
    payload = b"owned"
    info = tarfile.TarInfo(name="../outside.txt")
    info.size = len(payload)
    with tarfile.open(archive, "w:gz") as tar:
        tar.addfile(info, io.BytesIO(payload))

    with pytest.raises(ValueError, match="非法路径"):
        backup_mod.inspect_full_backup(archive)
    assert not (tmp_path / "outside.txt").exists()


def test_restore_rejects_dangerously_shallow_target() -> None:
    with pytest.raises(ValueError, match="不安全"):
        backup_mod._validate_restore_target(Path("/etc"), "OpenVPN")
