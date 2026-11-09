# -*- coding: utf-8 -*-
"""NiceVPN + OpenVPN 日志使用发行版标准 logrotate 策略的回归验证。"""
from __future__ import annotations

import grp
import os
import pwd
import shutil
import subprocess
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CONF = ROOT / "deploy/logrotate/nicevpn-openvpn.conf.in"
INSTALL = ROOT / "deploy/shell/install-nicevpn-logrotate.sh"


def _policy(conf: str, name: str) -> str:
    return conf.split(f"@OPENVPN_LOG_DIR@/{name} {{", 1)[1].split("}", 1)[0]


def test_only_four_append_logs_are_managed():
    conf = CONF.read_text(encoding="utf-8")
    for filename, size, copytruncate in (
        ("openvpn-device-bind.log", "16M", False),
        ("openvpn-session-audit.jsonl", "64M", False),
        ("openvpn-connection-audit.jsonl", "128M", False),
        ("server.log", "128M", True),
    ):
        block = _policy(conf, filename)
        assert f"maxsize {size}" in block
        for directive in ("daily", "rotate 14", "maxage 7", "compress",
                          "dateext", "missingok", "notifempty",
                          "su @LOG_OWNER@ @LOG_GROUP@"):
            assert directive in block
        assert "delaycompress" not in block
        assert ("copytruncate" in block) == copytruncate
        if not copytruncate:
            assert "create 0644 @LOG_OWNER@ @LOG_GROUP@" in block
    assert conf.count("@OPENVPN_LOG_DIR@/") == 4
    noncomments = [
        line for line in conf.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not any("server-status.log" in line for line in noncomments)


def test_native_logrotate_installer_and_openvpn_install_flow():
    installer = INSTALL.read_text(encoding="utf-8")
    start = (ROOT / "start.sh").read_text(encoding="utf-8")
    python_installer = (
        ROOT / "app/services/openvpn/installer.py"
    ).read_text(encoding="utf-8")
    debian = (
        ROOT / "app/scripts/install/openvpn-install-debian.sh"
    ).read_text(encoding="utf-8")
    rhel = (
        ROOT / "app/scripts/install/openvpn-install-rhel.sh"
    ).read_text(encoding="utf-8")
    assert "/etc/logrotate.d/nicevpn-openvpn" in installer
    assert "install-nicevpn-logrotate.sh" in start
    assert "install-nicevpn-logrotate.sh" in python_installer
    assert "logrotate" in debian
    assert "logrotate" in rhel
    assert "INSTALL_SERVER_SYSTEMD" in debian
    assert "INSTALL_SERVER_SYSTEMD" in rhel
    assert "id -gn nobody" in installer
    assert "copytruncate" not in installer
    assert "systemctl enable --now nicevpn-audit-logrotate.timer" not in installer
    assert 'SYSTEMD_DROPIN_TARGET="$SYSTEMD_DROPIN_DIR/nicevpn-openvpn.conf"' in installer
    assert "systemctl cat logrotate.service" in installer
    assert "ReadWritePaths=-$LOG_DIR" in installer
    assert "systemctl daemon-reload" in installer


def test_logrotate_installer_detects_duplicate_system_policy_and_rolls_back():
    installer = INSTALL.read_text(encoding="utf-8")
    # 独立策略校验之外还要检查系统入口，发现重名条目后恢复旧配置。
    assert 'grep -Eq' in installer
    assert 'include /etc/logrotate.d' in installer
    assert '"$LOGROTATE_BIN" -d "$tmp_conf" > "$tmp_check" 2>&1' in installer
    assert '"$LOGROTATE_BIN" -d /etc/logrotate.conf > "$tmp_check" 2>&1' in installer
    assert 'had_previous=0' in installer
    assert 'cp -p -- "$CONF_TARGET" "$tmp_backup"' in installer
    assert 'install -m 0644 "$tmp_backup" "$CONF_TARGET"' in installer
    assert 'rm -f -- "$CONF_TARGET"' in installer


def test_logrotate_installer_bash_syntax():
    proc = subprocess.run(
        ["bash", "-n", str(INSTALL)],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_logrotate_config_parses_with_real_binary_when_installed(tmp_path):
    binary = shutil.which("logrotate")
    if binary is None:
        pytest.skip("CI 未安装 logrotate")
    if os.name != "posix" or os.geteuid() != 0:
        pytest.skip("logrotate su nobody 模拟需要 root")
    group = grp.getgrgid(pwd.getpwnam("nobody").pw_gid).gr_name
    conf = (
        CONF.read_text(encoding="utf-8")
        .replace("@OPENVPN_LOG_DIR@", "/var/log")
        .replace("@LOG_OWNER@", "nobody")
        .replace("@LOG_GROUP@", group)
    )
    path = tmp_path / "nicevpn-openvpn.conf"
    path.write_text(conf, encoding="utf-8")
    proc = subprocess.run(
        [binary, "-d", "-s", str(tmp_path / "state"), str(path)],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_expiry_cleanup_keeps_active_rotated_logs(monkeypatch, tmp_path):
    """旧的按 mtime 清理不能 unlink 四个活动日志文件。"""
    from app.utils import log_cleanup

    log_dir = tmp_path / "openvpn" / "log"
    log_dir.mkdir(parents=True)
    data_logs = tmp_path / "data" / "logs"
    audit_dir = tmp_path / "data" / "audit"
    data_logs.mkdir(parents=True)
    audit_dir.mkdir(parents=True)

    monkeypatch.setattr(log_cleanup, "OPENVPN_DAEMON_LOG_DIR", log_dir)
    monkeypatch.setattr(log_cleanup, "OPENVPN_LOG_ROOT", log_dir)
    monkeypatch.setattr(log_cleanup, "LOGS_DIR", data_logs)
    monkeypatch.setattr(log_cleanup, "AUDIT_DIR", audit_dir)

    active_names = (
        ("openvpn-device-bind.log", "DEVICE_BIND_LOG_FILE"),
        ("openvpn-session-audit.jsonl", "VPN_SESSION_AUDIT_LOG_FILE"),
        ("openvpn-connection-audit.jsonl", "VPN_CONNECTION_AUDIT_LOG_FILE"),
    )
    for name, attr in active_names:
        file = log_dir / name
        file.write_text("old log", encoding="utf-8")
        monkeypatch.setattr(log_cleanup, attr, file)

    (log_dir / "server.log").write_text("old server log", encoding="utf-8")
    (log_dir / "old-unmanaged.log").write_text("expired file", encoding="utf-8")
    past = time.time() - 15 * 86400
    for file in log_dir.iterdir():
        os.utime(file, (past, past))
    log_cleanup.cleanup_expired_logs(retention_days=7)
    for name in ("server.log", *(name for name, _ in active_names)):
        assert (log_dir / name).exists()
    assert not (log_dir / "old-unmanaged.log").exists()
