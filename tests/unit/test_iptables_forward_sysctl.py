# -*- coding: utf-8 -*-
"""IPv4 forwarding 使用 sysctl 设置，并由 sysctl.d 模板持久化。"""

from pathlib import Path
from subprocess import CompletedProcess

from app.services.firewall.iptables_mgr import IptablesManager


def test_ensure_ipv4_forward_enabled_uses_sysctl(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return CompletedProcess(command, 0, stdout="net.ipv4.ip_forward = 1\n", stderr="")

    monkeypatch.setattr("app.services.firewall.iptables_mgr.subprocess.run", fake_run)

    IptablesManager._ensure_ipv4_forward_enabled()

    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == ["sysctl", "-w", "net.ipv4.ip_forward=1"]
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["timeout"] == 10
    assert kwargs["check"] is False


def test_ensure_ipv4_forward_enabled_logs_sysctl_failure_without_raising(
    monkeypatch, caplog,
):
    def fake_run(command, **kwargs):
        return CompletedProcess(command, 1, stdout="", stderr="permission denied")

    monkeypatch.setattr("app.services.firewall.iptables_mgr.subprocess.run", fake_run)

    IptablesManager._ensure_ipv4_forward_enabled()

    assert "permission denied" in caplog.text


def test_ensure_ipv4_forward_enabled_handles_missing_sysctl(monkeypatch, caplog):
    def fake_run(*_args, **_kwargs):
        raise FileNotFoundError("sysctl")

    monkeypatch.setattr("app.services.firewall.iptables_mgr.subprocess.run", fake_run)

    IptablesManager._ensure_ipv4_forward_enabled()

    assert "sysctl" in caplog.text


def test_sysctl_dropin_persists_ipv4_forwarding():
    path = Path(__file__).resolve().parents[2] / "deploy" / "sysctl" / "99-openvpn-gateway.conf"
    text = path.read_text(encoding="utf-8")

    assert "net.ipv4.ip_forward = 1" in text
