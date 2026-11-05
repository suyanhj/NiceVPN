# -*- coding: utf-8 -*-
"""NiceVPN 三类审计日志的独立 logrotate 部署契约。"""
from __future__ import annotations

import grp
import pwd
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CONF = ROOT / "deploy/logrotate/nicevpn-audit.conf.in"
SERVICE = ROOT / "deploy/systemd/nicevpn-audit-logrotate.service.in"
TIMER = ROOT / "deploy/systemd/nicevpn-audit-logrotate.timer"
INSTALL = ROOT / "deploy/shell/install-nicevpn-audit-logrotate.sh"


def test_only_three_nicevpn_logs_are_managed():
    conf = CONF.read_text(encoding="utf-8")
    for filename, size in (
        ("openvpn-device-bind.log", "16M"),
        ("openvpn-session-audit.jsonl", "64M"),
        ("openvpn-connection-audit.jsonl", "128M"),
    ):
        assert f"@OPENVPN_LOG_DIR@/{filename} {{" in conf
        block = conf.split(f"@OPENVPN_LOG_DIR@/{filename} {{", 1)[1].split("}", 1)[0]
        assert f"maxsize {size}" in block
        for directive in (
            "daily", "rotate 14", "maxage 7", "compress", "delaycompress",
            "dateext", "missingok", "notifempty",
            "su @LOG_OWNER@ @LOG_GROUP@",
            "create 0644 @LOG_OWNER@ @LOG_GROUP@",
        ):
            assert directive in block
    assert conf.count("@OPENVPN_LOG_DIR@/") == 3
    assert "/server.log" not in conf
    assert "server-status.log" not in conf
    assert "copytruncate" not in "\n".join(
        l for l in conf.splitlines() if not l.lstrip().startswith("#")
    )


def test_logrotate_timer_has_isolated_config_and_state():
    service = SERVICE.read_text(encoding="utf-8")
    timer = TIMER.read_text(encoding="utf-8")
    installer = INSTALL.read_text(encoding="utf-8")
    start = (ROOT / "start.sh").read_text(encoding="utf-8")
    assert "/etc/nicevpn/logrotate-audit.conf" in service
    assert "/var/lib/logrotate/nicevpn-audit.status" in service
    assert "OnCalendar=*:0/10" in timer
    assert "Persistent=true" in timer
    assert "nicevpn-audit-logrotate.timer" in installer
    assert "command -v logrotate" in installer
    assert "id -gn nobody" in installer
    assert "logrotate-audit.conf" in installer
    assert "install-nicevpn-audit-logrotate.sh" in start


def test_logrotate_installer_bash_syntax():
    result = subprocess.run(
        ["bash", "-n", str(INSTALL)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_logrotate_config_parses_with_real_binary_when_installed(tmp_path):
    binary = shutil.which("logrotate")
    if binary is None:
        pytest.skip("CI 环境未安装 logrotate")
    group = grp.getgrgid(pwd.getpwnam("nobody").pw_gid).gr_name
    logs = tmp_path / "openvpn-logs"
    logs.mkdir()
    conf = (
        CONF.read_text(encoding="utf-8")
        .replace("@OPENVPN_LOG_DIR@", str(logs))
        .replace("@LOG_OWNER@", "nobody")
        .replace("@LOG_GROUP@", group)
    )
    path = tmp_path / "logrotate.conf"
    path.write_text(conf, encoding="utf-8")
    result = subprocess.run(
        [binary, "-d", str(path)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
