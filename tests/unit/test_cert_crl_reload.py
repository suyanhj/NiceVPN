# -*- coding: utf-8 -*-
"""CRL 重载只触达 NiceVPN 管理的活动服务端实例。"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from app.services.cert.cert_service import CertService
from app.services.openvpn import instance


def test_reload_instance_crl_targets_only_active_unit_main_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict]] = []
    monkeypatch.setattr(instance, "is_instance_active", lambda _name: True)

    def fake_run(command: list[str], **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(instance.subprocess, "run", fake_run)

    assert instance.reload_instance_crl("server") is True
    assert calls == [
        (
            [
                "systemctl",
                "kill",
                "--signal=HUP",
                "--kill-whom=main",
                "openvpn-server@server",
            ],
            {
                "check": True,
                "capture_output": True,
                "text": True,
                "timeout": 10,
            },
        )
    ]


def test_reload_instance_crl_skips_inactive_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(instance, "is_instance_active", lambda _name: False)
    monkeypatch.setattr(
        instance.subprocess,
        "run",
        lambda command, **_kwargs: calls.append(command),
    )

    assert instance.reload_instance_crl("offline") is False
    assert calls == []


def test_reload_instance_crl_returns_false_when_systemd_kill_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(instance, "is_instance_active", lambda _name: True)

    def fail_run(_command: list[str], **_kwargs):
        raise subprocess.CalledProcessError(
            1,
            ["systemctl", "kill"],
            stderr="Failed to kill unit",
        )

    monkeypatch.setattr(instance.subprocess, "run", fail_run)

    assert instance.reload_instance_crl("server") is False


def test_is_instance_active_uses_systemd_and_handles_missing_systemctl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict]] = []

    def active_run(command: list[str], **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="active\n", stderr="")

    monkeypatch.setattr(instance.subprocess, "run", active_run)
    assert instance.is_instance_active("server") is True
    assert calls[0][0] == ["systemctl", "is-active", "openvpn-server@server"]
    assert calls[0][1]["timeout"] == 10

    monkeypatch.setattr(
        instance.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("systemctl")),
    )
    assert instance.is_instance_active("server") is False


def test_cert_service_reloads_only_discovered_managed_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        instance,
        "iter_all_openvpn_instance_names",
        lambda: ["server", "edge"],
    )
    def fake_reload(name: str) -> bool:
        calls.append(name)
        return name == "server"

    monkeypatch.setattr(instance, "reload_instance_crl", fake_reload)

    service = object.__new__(CertService)
    service._reload_openvpn_instances()

    assert calls == ["server", "edge"]


def test_cert_service_handles_no_managed_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(instance, "iter_all_openvpn_instance_names", lambda: [])
    monkeypatch.setattr(
        instance,
        "reload_instance_crl",
        lambda _name: (_ for _ in ()).throw(AssertionError("no instances expected")),
    )

    service = object.__new__(CertService)
    service._reload_openvpn_instances()
