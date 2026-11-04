# -*- coding: utf-8 -*-
"""历史 OVPN 连接地址同步测试。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.user import ovpn_sync


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "app" / "scripts" / "system" / "sync-ovpn-connection.sh"


def _profile(host: str = "1.2.3.4", port: int = 1194, proto: str = "udp") -> str:
    return (
        "client\n"
        f"proto {proto}\n"
        f"remote {host} {port}\n"
        "remote-cert-tls server\n"
        "<cert>\n"
        "CERT-DATA-UNCHANGED\n"
        "</cert>\n"
        "<key>\n"
        "KEY-DATA-UNCHANGED\n"
        "</key>\n"
    )


def test_shell_sync_updates_only_connection_lines(tmp_path: Path) -> None:
    one = tmp_path / "alice.ovpn"
    two = tmp_path / "bob.ovpn"
    one.write_text(_profile(), encoding="utf-8")
    two.write_text(_profile(), encoding="utf-8")

    proc = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            str(tmp_path),
            "1.2.3.4",
            "1194",
            "udp",
            "vpn.example.com",
            "443",
            "tcp",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert "TOTAL=2" in proc.stdout
    assert "CHANGED=2" in proc.stdout
    for path in (one, two):
        text = path.read_text(encoding="utf-8")
        assert "proto tcp\n" in text
        assert "remote vpn.example.com 443\n" in text
        assert "CERT-DATA-UNCHANGED" in text
        assert "KEY-DATA-UNCHANGED" in text
        assert not Path(str(path) + ".nicevpn-connection.bak").exists()


def test_shell_sync_preflights_all_profiles_before_editing(tmp_path: Path) -> None:
    good = tmp_path / "alice.ovpn"
    broken = tmp_path / "broken.ovpn"
    original = _profile()
    good.write_text(original, encoding="utf-8")
    broken.write_text("client\nremote 1.2.3.4 1194\n", encoding="utf-8")

    proc = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            str(tmp_path),
            "1.2.3.4",
            "1194",
            "udp",
            "5.6.7.8",
            "443",
            "tcp",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode != 0
    assert "MISSING_PROTO=" in proc.stderr
    assert good.read_text(encoding="utf-8") == original


def test_shell_sync_missing_profile_dir_is_noop(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    proc = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            str(missing),
            "1.2.3.4",
            "1194",
            "udp",
            "5.6.7.8",
            "443",
            "tcp",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    assert "TOTAL=0" in proc.stdout
    assert "CHANGED=0" in proc.stdout


def test_wrapper_skips_shell_when_endpoint_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def should_not_run(*_args, **_kwargs):
        raise AssertionError("endpoint unchanged must not invoke shell")

    monkeypatch.setattr(ovpn_sync.subprocess, "run", should_not_run)
    cfg = SimpleNamespace(server_ip="1.2.3.4", port=1194, proto="udp")

    assert ovpn_sync.sync_existing_ovpn_connection(cfg, cfg) == {
        "total": 0,
        "changed": 0,
    }
