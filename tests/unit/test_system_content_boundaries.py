# -*- coding: utf-8 -*-
"""系统部署内容与 OpenVPN 配置模板边界测试。"""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.openvpn import instance as instance_mod
from app.services.user import ovpn_gen
from app.utils import sysctl_tune


def test_openvpn_config_templates_are_external_files() -> None:
    server_template = Path(instance_mod._SERVER_CONF_TEMPLATE)
    client_template = Path(ovpn_gen._CLIENT_OVPN_TEMPLATE)

    assert server_template.is_file()
    assert client_template.is_file()

    server_source = inspect.getsource(instance_mod.generate_server_conf)
    client_source = inspect.getsource(ovpn_gen.generate_ovpn)
    assert "# OpenVPN 服务端配置 — 实例" not in server_source
    assert "# OpenVPN 客户端配置文件" not in client_source


def test_server_config_renders_external_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        instance_mod,
        "_openvpn_supports_tls_crypt_v2_max_age",
        lambda: True,
    )
    text = instance_mod.generate_server_conf(
        "server",
        {
            "openvpn_conf_dir": "/etc/openvpn",
            "pki_dir": "/etc/openvpn/pki",
            "server_network": "10.8.0.0",
            "server_mask": "255.255.255.0",
            "port": 1194,
            "proto": "udp",
            "mgmt_port": 7505,
            "max_clients": 256,
            "push_lan_routes": ["192.168.1.0/24"],
        },
    )

    assert "# OpenVPN 服务端配置 — 实例 server" in text
    assert "port 1194" in text
    assert "management 127.0.0.1 7505" in text
    assert 'push "route 192.168.1.0 255.255.255.0"' in text
    assert "tls-crypt-v2-max-age 3650" in text
    assert "{{" not in text


def test_client_profile_renders_external_template() -> None:
    text = ovpn_gen.generate_ovpn(
        "alice",
        "10.8.1.0/24",
        {
            "server_ip": "vpn.example.com",
            "port": 1194,
            "proto": "udp",
            "ca_cert": "CA",
            "user_cert": "CERT",
            "user_key": "KEY",
            "tc2_client_key": "TC2",
        },
    )

    assert "# 用户: alice" in text
    assert "remote vpn.example.com 1194" in text
    assert "<ca>\nCA\n</ca>" in text
    assert "<tls-crypt-v2>\nTC2\n</tls-crypt-v2>" in text
    assert text.endswith("\n")
    assert "{{" not in text


def test_sysctl_install_delegates_system_changes_to_shell(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "99-openvpn-gateway.conf"
    source.write_text("net.ipv4.ip_forward = 1\n", encoding="utf-8")
    script = tmp_path / "install-vpn-sysctl.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    seen: dict[str, object] = {}
    monkeypatch.setattr(sysctl_tune, "_sysctl_source_path", lambda: source)
    monkeypatch.setattr(sysctl_tune, "_SYSCTL_INSTALL_SCRIPT", script)
    monkeypatch.setattr(sysctl_tune.os, "geteuid", lambda: 0)

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(sysctl_tune.subprocess, "run", fake_run)

    sysctl_tune.install_vpn_sysctl_dropin()

    assert seen["cmd"] == ["bash", str(script)]
    env = seen["env"]
    assert env["SYSCTL_SOURCE"] == str(source)
    assert env["SYSCTL_TARGET"] == "/etc/sysctl.d/99-openvpn-gateway.conf"

    py_source = Path(sysctl_tune.__file__).read_text(encoding="utf-8")
    assert "shutil.copyfile" not in py_source
    assert "os.chmod(" not in py_source
    assert '["sysctl"' not in py_source
