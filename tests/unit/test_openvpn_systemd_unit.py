# -*- coding: utf-8 -*-
"""OpenVPN server systemd unit 检测与既有安装逻辑复用测试。"""

from pathlib import Path
import subprocess

import pytest

from app.services.openvpn import installer as installer_mod


def _make_openvpn_binary(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _systemctl_result(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["systemctl", "list-unit-files"],
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


def test_existing_server_unit_is_detected_and_left_untouched(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binary = _make_openvpn_binary(tmp_path / "sbin" / "openvpn")
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return _systemctl_result(
            "openvpn-server@.service disabled enabled\n"
            "openvpn-client@.service disabled enabled\n"
        )

    monkeypatch.setattr(installer_mod.subprocess, "run", fake_run)

    def unexpected_install(**_kwargs):
        raise AssertionError("已有 server unit 时不应重新生成或覆盖")

    monkeypatch.setattr(
        installer_mod,
        "install_openvpn_server_systemd_from_staged_source",
        unexpected_install,
    )

    result = installer_mod.ensure_openvpn_server_systemd_unit(
        binary,
        openvpn_version="2.7.8",
    )

    assert result == "openvpn-server@.service"
    assert calls == [[
        "systemctl",
        "list-unit-files",
        "--type=service",
        "--no-legend",
        "--no-pager",
    ]]


def test_missing_server_unit_reuses_existing_systemd_generator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binary = _make_openvpn_binary(tmp_path / "opt" / "openvpn" / "sbin" / "openvpn")
    source_dir = tmp_path / "openvpn-2.7.8"
    template = source_dir / "distro" / "systemd" / "openvpn-server@.service.in"
    template.parent.mkdir(parents=True)
    template.write_text("[Unit]\n", encoding="utf-8")
    monkeypatch.setattr(installer_mod, "_OPENVPN_SOURCE_ROOT", tmp_path)

    monkeypatch.setattr(
        installer_mod.subprocess,
        "run",
        lambda *_args, **_kwargs: _systemctl_result("openvpn-client@.service disabled enabled\n"),
    )

    calls: list[dict] = []

    def fake_install(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        installer_mod,
        "install_openvpn_server_systemd_from_staged_source",
        fake_install,
    )

    result = installer_mod.ensure_openvpn_server_systemd_unit(
        binary,
        openvpn_version="2.7.8",
    )

    assert result == "openvpn-server@.service"
    assert calls == [{
        "source_dir": source_dir,
        "tag": "v2.7.8",
        "openvpn_bin": binary.resolve(),
        "on_output": None,
    }]


def test_missing_server_unit_without_source_template_fails_clearly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binary = _make_openvpn_binary(tmp_path / "openvpn")
    monkeypatch.setattr(installer_mod, "_OPENVPN_SOURCE_ROOT", tmp_path)
    monkeypatch.setenv("OPENVPN_SRC_ROOT", str(tmp_path))
    monkeypatch.setattr(
        installer_mod.subprocess,
        "run",
        lambda *_args, **_kwargs: _systemctl_result(""),
    )

    with pytest.raises(FileNotFoundError, match="未找到 OpenVPN 源码中的 systemd 模板"):
        installer_mod.ensure_openvpn_server_systemd_unit(
            binary,
            openvpn_version="2.7.8",
        )


def test_systemctl_failure_is_reported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binary = _make_openvpn_binary(tmp_path / "openvpn")
    monkeypatch.setattr(
        installer_mod.subprocess,
        "run",
        lambda *_args, **_kwargs: _systemctl_result("", returncode=1),
    )

    with pytest.raises(RuntimeError, match="systemctl list-unit-files 执行失败"):
        installer_mod.ensure_openvpn_server_systemd_unit(binary, openvpn_version="2.7.8")


def test_staged_systemd_installer_passes_explicit_binary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binary = _make_openvpn_binary(tmp_path / "custom" / "bin" / "openvpn")
    script = tmp_path / "openvpn-install-systemd.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    monkeypatch.setattr(installer_mod, "_INSTALL_SCRIPTS_DIR", tmp_path)

    calls: list[dict] = []

    def fake_run(command, _output, *, env=None, cwd=None):
        calls.append({"command": command, "env": dict(env or {})})

    monkeypatch.setattr(installer_mod, "_run_command", fake_run)

    installer_mod.install_openvpn_server_systemd_from_staged_source(
        source_dir=tmp_path / "source",
        tag="v2.7.8",
        openvpn_bin=binary,
    )

    assert calls[0]["env"]["OPENVPN_SRC_DIR"] == str(tmp_path / "source")
    assert calls[0]["env"]["OPENVPN_BIN"] == str(binary.resolve())
    assert calls[0]["env"]["OPENVPN_SBIN_DIR"] == str(binary.resolve().parent)
