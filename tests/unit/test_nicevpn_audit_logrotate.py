# -*- coding: utf-8 -*-
"""NiceVPN 三类审计日志的独立 logrotate 部署契约。"""
from __future__ import annotations

import grp
import os
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
    active_entries = [
        line for line in conf.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not any("/server.log" in line for line in active_entries)
    assert not any("server-status.log" in line for line in active_entries)
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
    if os.geteuid() != 0:
        pytest.skip("logrotate su nobody 预检需要 root 权限")
    # 只做 -d 语法检验，/var/log 可由 nobody 穿越；pytest 临时目录可能是 0700。
    logs = Path("/var/log")
    conf = (
        CONF.read_text(encoding="utf-8")
        .replace("@OPENVPN_LOG_DIR@", str(logs))
        .replace("@LOG_OWNER@", "nobody")
        .replace("@LOG_GROUP@", group)
    )
    path = tmp_path / "logrotate.conf"
    path.write_text(conf, encoding="utf-8")
    result = subprocess.run(
        [binary, "-d", "-s", str(tmp_path / "state"), str(path)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_expiry_cleanup_keeps_active_nicevpn_logs(monkeypatch, tmp_path):
    """老时间戳的 NiceVPN 活动审计日志由 logrotate 管理，不能直接 unlink。"""
    import time
    from app.utils import log_cleanup

    logs = tmp_path / "openvpn" / "log"
    logs.mkdir(parents=True)
    data_logs = tmp_path / "data" / "logs"
    audit = tmp_path / "data" / "audit"
    data_logs.mkdir(parents=True)
    audit.mkdir(parents=True)

    monkeypatch.setattr(log_cleanup, "OPENVPN_DAEMON_LOG_DIR", logs)
    monkeypatch.setattr(log_cleanup, "OPENVPN_LOG_ROOT", logs)
    monkeypatch.setattr(log_cleanup, "LOGS_DIR", data_logs)
    monkeypatch.setattr(log_cleanup, "AUDIT_DIR", audit)
    for name, attribute in (
        ("openvpn-device-bind.log", "DEVICE_BIND_LOG_FILE"),
        ("openvpn-session-audit.jsonl", "VPN_SESSION_AUDIT_LOG_FILE"),
        ("openvpn-connection-audit.jsonl", "VPN_CONNECTION_AUDIT_LOG_FILE"),
    ):
        file = logs / name
        file.write_text("old log", encoding="utf-8")
        monkeypatch.setattr(log_cleanup, attribute, file)

    old = time.time() - 15 * 86400
    for file in logs.iterdir():
        os.utime(file, (old, old))

    # 既有 OpenVPN 日志继续走按时间清理逻辑；NiceVPN 三个文件必须保留。
    old_daemon = logs / "server.log"
    old_daemon.write_text("older", encoding="utf-8")
    os.utime(old_daemon, (old, old))

    log_cleanup.cleanup_expired_logs(retention_days=7)
    assert not old_daemon.exists()
    for name in (
        "openvpn-device-bind.log",
        "openvpn-session-audit.jsonl",
        "openvpn-connection-audit.jsonl",
    ):
        assert (logs / name).exists()
