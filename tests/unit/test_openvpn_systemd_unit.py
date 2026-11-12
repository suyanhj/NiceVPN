# -*- coding: utf-8 -*-
"""OpenVPN server systemd 模板单元的检测与补齐测试。"""

from pathlib import Path

import pytest

from app.services.openvpn import installer as installer_mod


def _make_openvpn_binary(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_existing_systemd_unit_is_reused_without_overwrite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binary = _make_openvpn_binary(tmp_path / "bin" / "openvpn")
    unit = tmp_path / "usr" / "lib" / "systemd" / "system" / "openvpn-server@.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\nExecStart=/usr/sbin/openvpn\n", encoding="utf-8")

    monkeypatch.setattr(
        installer_mod,
        "_OPENVPN_SERVER_UNIT_SEARCH_PATHS",
        (unit,),
    )

    def unexpected_run(*_args, **_kwargs):
        raise AssertionError("已有 unit 时不应执行补装脚本")

    monkeypatch.setattr(installer_mod, "_run_command", unexpected_run)

    result = installer_mod.ensure_openvpn_server_systemd_unit(str(binary))

    assert result == unit
    assert unit.read_text(encoding="utf-8") == "[Service]\nExecStart=/usr/sbin/openvpn\n"


def test_missing_systemd_unit_installs_project_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binary = _make_openvpn_binary(tmp_path / "opt" / "openvpn" / "sbin" / "openvpn")
    scripts = tmp_path / "app" / "scripts" / "install"
    scripts.mkdir(parents=True)
    (scripts / "openvpn-install-systemd-fallback.sh").write_text(
        "#!/usr/bin/env bash\n", encoding="utf-8"
    )
    target = tmp_path / "etc" / "systemd" / "openvpn-server@.service"
    monkeypatch.setattr(installer_mod, "_INSTALL_SCRIPTS_DIR", scripts)
    monkeypatch.setattr(
        installer_mod,
        "_OPENVPN_SERVER_UNIT_SEARCH_PATHS",
        (target,),
    )
    monkeypatch.setattr(installer_mod, "_OPENVPN_SERVER_UNIT_INSTALL_PATH", target)
    monkeypatch.setattr(installer_mod.shutil, "which", lambda command: "/usr/bin/systemctl")

    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(cmd, _output, *, env=None, cwd=None):
        calls.append((cmd, dict(env or {})))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            f"[Service]\nExecStart={env['OPENVPN_BIN']} --config %i.conf\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(installer_mod, "_run_command", fake_run)

    result = installer_mod.ensure_openvpn_server_systemd_unit(str(binary))

    assert result == target
    assert len(calls) == 1
    assert calls[0][0] == ["bash", str(scripts / "openvpn-install-systemd-fallback.sh")]
    assert calls[0][1]["OPENVPN_BIN"] == str(binary.resolve())
    assert str(binary.resolve()) in target.read_text(encoding="utf-8")


def test_missing_systemd_unit_without_systemctl_fails_clearly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binary = _make_openvpn_binary(tmp_path / "openvpn")
    monkeypatch.setattr(installer_mod, "_OPENVPN_SERVER_UNIT_SEARCH_PATHS", ())
    monkeypatch.setattr(installer_mod.shutil, "which", lambda _command: None)

    with pytest.raises(RuntimeError, match="未找到 systemctl"):
        installer_mod.ensure_openvpn_server_systemd_unit(str(binary))


def test_project_systemd_template_matches_nicevpn_server_runtime() -> None:
    template = (
        Path(__file__).resolve().parents[2]
        / "deploy"
        / "systemd"
        / "openvpn-server@.service.in"
    ).read_text(encoding="utf-8")

    assert "Type=notify" in template
    assert "WorkingDirectory=/etc/openvpn/server" in template
    assert "ExecStart=@OPENVPN_BIN@ --suppress-timestamps --config %i.conf" in template
    assert "DeviceAllow=/dev/net/tun rw" in template
    assert "ProtectSystem=true" in template
