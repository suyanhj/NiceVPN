# -*- coding: utf-8 -*-
"""OpenVPN 脚本同步占位符替换。"""

import os
import shlex
import subprocess
import sys
from pathlib import Path


def test_sync_replaces_device_bind_log_placeholder(tmp_path: Path) -> None:
    """OpenVPN 钩子脚本占位符应替换为 constants 中的日志路径。"""
    from app.core.constants import DEVICE_BIND_LOG_FILE, VPN_SESSION_AUDIT_LOG_FILE
    from app.services.openvpn.script_sync import sync_packaged_openvpn_scripts

    sync_packaged_openvpn_scripts(tmp_path)
    sh = tmp_path / "scripts" / "device-bind.sh"
    disconnect = tmp_path / "scripts" / "vpn-session-disconnect.sh"
    audit_py = tmp_path / "scripts" / "vpn_session_audit.py"
    assert sh.is_file()
    assert disconnect.is_file()
    assert audit_py.is_file()
    assert (tmp_path / "scripts" / "device_bind_exemption.py").is_file()
    text = sh.read_text(encoding="utf-8")
    assert "__DEVICE_BIND_LOG_FILE__" not in text
    assert "__VPN_SESSION_AUDIT_LOG_FILE__" not in text
    assert DEVICE_BIND_LOG_FILE.as_posix() in text
    assert VPN_SESSION_AUDIT_LOG_FILE.as_posix() in text
    disconnect_text = disconnect.read_text(encoding="utf-8")
    assert "__VPN_SESSION_AUDIT_LOG_FILE__" not in disconnect_text
    assert VPN_SESSION_AUDIT_LOG_FILE.as_posix() in disconnect_text

    # 两个钩子使用启动 NiceVPN 的 Python，不再搜索系统 python3.6。
    expected_python = shlex.quote(os.path.abspath(sys.executable))
    assert f"PY3_BIND={expected_python}" in text
    assert f"PY3_AUDIT={expected_python}" in disconnect_text
    for script, content in ((sh, text), (disconnect, disconnect_text)):
        assert "__NICEVPN_PYTHON_BIN__" not in content
        assert "for v in 20 19 18" not in content
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_hook_python_path_is_shell_quoted(tmp_path: Path, monkeypatch) -> None:
    """Python 路径包含空格时也不会破坏 OpenVPN shell 钩子。"""
    from app.services.openvpn import script_sync

    monkeypatch.setattr(script_sync.sys, "executable", "/opt/Nice VPN/.venv/bin/python")
    script_sync.sync_packaged_openvpn_scripts(tmp_path)

    for name, variable in (
        ("device-bind.sh", "PY3_BIND"),
        ("vpn-session-disconnect.sh", "PY3_AUDIT"),
    ):
        content = (tmp_path / "scripts" / name).read_text(encoding="utf-8")
        assert f"{variable}='/opt/Nice VPN/.venv/bin/python'" in content
        assert "__NICEVPN_PYTHON_BIN__" not in content
        check = subprocess.run(
            ["bash", "-n", str(tmp_path / "scripts" / name)],
            capture_output=True, text=True,
        )
        assert check.returncode == 0, check.stderr


def test_hook_scripts_use_selected_python_without_per_connection_version_probe(
    tmp_path: Path,
) -> None:
    """部署时固定 Python 解释器，不在每次连接/断开时再次启动 Python 检测版本。"""
    from app.services.openvpn.script_sync import sync_packaged_openvpn_scripts

    sync_packaged_openvpn_scripts(tmp_path)
    bind = (tmp_path / "scripts" / "device-bind.sh").read_text(encoding="utf-8")
    disconnect = (tmp_path / "scripts" / "vpn-session-disconnect.sh").read_text(encoding="utf-8")
    assert "init_py3_for_bind()" in bind
    assert "init_py3_for_audit" not in disconnect
    assert ' -c \'import sys;' not in bind
    assert ' -c \'import sys;' not in disconnect



def test_connect_and_disconnect_hooks_use_selected_python_not_path_python3(
    tmp_path: Path,
) -> None:
    """在 PATH 的 python3 不可用时，部署的两个钩子仍可完整写入会话审计。"""
    import json

    from app.core.constants import DEVICE_BIND_LOG_FILE, VPN_SESSION_AUDIT_LOG_FILE
    from app.services.openvpn.script_sync import sync_packaged_openvpn_scripts

    sync_packaged_openvpn_scripts(tmp_path)
    script_dir = tmp_path / "scripts"
    mgmt_dir = tmp_path / "mgmt"
    mgmt_dir.mkdir()
    (mgmt_dir / "device_bind_mode").write_text("weak_log\n", encoding="utf-8")

    log_dir = tmp_path / "log"
    log_dir.mkdir()
    audit_file = log_dir / "session-audit.jsonl"
    bind_log = log_dir / "device-bind.log"

    connect = script_dir / "device-bind.sh"
    connect_content = connect.read_text(encoding="utf-8")
    connect_content = connect_content.replace('OVPN_ETC="/etc/openvpn"', f'OVPN_ETC="{tmp_path}"')
    connect_content = connect_content.replace(DEVICE_BIND_LOG_FILE.as_posix(), str(bind_log))
    connect_content = connect_content.replace(VPN_SESSION_AUDIT_LOG_FILE.as_posix(), str(audit_file))
    connect.write_text(connect_content, encoding="utf-8")

    disconnect = script_dir / "vpn-session-disconnect.sh"
    disconnect_content = disconnect.read_text(encoding="utf-8")
    disconnect_content = disconnect_content.replace(VPN_SESSION_AUDIT_LOG_FILE.as_posix(), str(audit_file))
    disconnect.write_text(disconnect_content, encoding="utf-8")

    bad_path = tmp_path / "fake-path"
    bad_path.mkdir()
    marker = tmp_path / "system-python-invoked"
    fake = bad_path / "python3"
    fake.write_text(
        '#!/bin/sh\ntouch "' + str(marker) + '"\nexit 99\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{bad_path}:{os.environ.get('PATH', '')}",
        "common_name": "audit-test-user",
        "PY3_BIND": "",
        "PY3_AUDIT": "",
    }

    for hook in (connect, disconnect):
        result = subprocess.run(
            ["bash", str(hook)],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)

    events = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines()]
    assert [event["event_type"] for event in events] == [
        "vpn_session_start",
        "vpn_session_end",
    ]
    assert not marker.exists(), "不能回退到 PATH 中不受支持的系统 python3"


def _prepare_hook_runtime(tmp_path: Path):
    """以真实 Shell 调用部署后的 Python 脚本，避免只测试字符串。"""
    from app.core.constants import DEVICE_BIND_LOG_FILE, VPN_SESSION_AUDIT_LOG_FILE
    from app.services.openvpn.script_sync import sync_packaged_openvpn_scripts

    sync_packaged_openvpn_scripts(tmp_path)
    scripts = tmp_path / "scripts"
    mgmt = tmp_path / "mgmt"
    mgmt.mkdir()
    (mgmt / "device_bind_mode").write_text("weak_log\n", encoding="utf-8")
    server = tmp_path / "server"
    server.mkdir()
    server_conf = server / "server.conf"
    server_conf.write_text("management 127.0.0.1 7505\n", encoding="utf-8")

    log_dir = tmp_path / "log"
    log_dir.mkdir()
    bind_log = log_dir / "bind.log"
    audit_log = log_dir / "sessions.jsonl"
    connect = scripts / "device-bind.sh"
    body = connect.read_text(encoding="utf-8")
    body = body.replace('OVPN_ETC="/etc/openvpn"', f'OVPN_ETC="{tmp_path}"')
    body = body.replace(DEVICE_BIND_LOG_FILE.as_posix(), str(bind_log))
    body = body.replace(VPN_SESSION_AUDIT_LOG_FILE.as_posix(), str(audit_log))
    connect.write_text(body, encoding="utf-8")
    disconnect = scripts / "vpn-session-disconnect.sh"
    body = disconnect.read_text(encoding="utf-8")
    body = body.replace(VPN_SESSION_AUDIT_LOG_FILE.as_posix(), str(audit_log))
    disconnect.write_text(body, encoding="utf-8")

    call_log = tmp_path / "python-calls.txt"
    fake_python = tmp_path / "spy-python"
    fake_python.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$1" >> "$PY_CALL_LOG"\nexec '
        + shlex.quote(os.path.abspath(sys.executable)) + ' "$@"\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = {
        **os.environ,
        "common_name": "demo-user",
        "VPN_INSTANCE_NAME": "server",
        "trusted_ip": "203.0.113.7",
        "trusted_port": "55778",
        "PY_CALL_LOG": str(call_log),
        "PY3_BIND": str(fake_python),
        "PY3_AUDIT": str(fake_python),
    }
    return {
        "connect": connect, "disconnect": disconnect, "server_conf": server_conf,
        "bind_mode": mgmt / "device_bind_mode", "bind_log": bind_log,
        "audit_log": audit_log, "call_log": call_log, "env": env,
        "scripts": scripts,
    }


def _run_hook(path: Path, env: dict[str, str]):
    return subprocess.run(["bash", str(path)], env=env, capture_output=True, text=True)


def test_connect_without_sso_starts_python_only_for_session_audit(tmp_path: Path) -> None:
    runtime = _prepare_hook_runtime(tmp_path)
    result = _run_hook(runtime["connect"], runtime["env"])
    assert result.returncode == 0, result.stderr
    calls = runtime["call_log"].read_text(encoding="utf-8").splitlines()
    assert [Path(item).name for item in calls] == ["vpn_session_audit.py"]


def test_connect_with_management_auth_checks_sso_grant_before_audit(tmp_path: Path) -> None:
    runtime = _prepare_hook_runtime(tmp_path)
    runtime["server_conf"].write_text(
        "management /etc/openvpn/mgmt/sso.sock unix\n"
        "management-client-user root\n"
        "management-client-auth\n",
        encoding="utf-8",
    )
    result = _run_hook(runtime["connect"], runtime["env"])
    assert result.returncode == 0, result.stderr
    calls = runtime["call_log"].read_text(encoding="utf-8").splitlines()
    assert [Path(item).name for item in calls] == [
        "sso_grant.py", "vpn_session_audit.py",
    ]


def test_audit_failures_do_not_reject_connect_or_disconnect(tmp_path: Path) -> None:
    runtime = _prepare_hook_runtime(tmp_path)
    # 目录路径无法作为 JSONL 文件追加，模拟文件写入失败。
    runtime["audit_log"].mkdir()

    connected = _run_hook(runtime["connect"], runtime["env"])
    assert connected.returncode == 0, connected.stderr
    assert "警告: 会话开始审计写入失败" in runtime["bind_log"].read_text(encoding="utf-8")

    disconnected = _run_hook(runtime["disconnect"], runtime["env"])
    assert disconnected.returncode == 0
    assert "警告: 写入 vpn_session_end 失败" in disconnected.stderr


def test_audit_missing_or_python_missing_is_nonblocking(tmp_path: Path) -> None:
    runtime = _prepare_hook_runtime(tmp_path)
    (runtime["scripts"] / "vpn_session_audit.py").unlink()
    assert _run_hook(runtime["connect"], runtime["env"]).returncode == 0
    assert _run_hook(runtime["disconnect"], runtime["env"]).returncode == 0

    runtime["env"]["PY3_BIND"] = "/not-a-real-python-interpreter"
    assert _run_hook(runtime["connect"], runtime["env"]).returncode == 0
    runtime["env"]["PY3_AUDIT"] = "/not-a-real-python-interpreter"
    assert _run_hook(runtime["disconnect"], runtime["env"]).returncode == 0


def test_hardware_binding_rejections_remain_fail_closed(tmp_path: Path) -> None:
    runtime = _prepare_hook_runtime(tmp_path)
    runtime["bind_mode"].write_text("strict_hwaddr\n", encoding="utf-8")
    result = _run_hook(runtime["connect"], runtime["env"])
    assert result.returncode != 0, "强制模式缺 IV_HWADDR 应拒绝连接"

    env = {
        **runtime["env"],
        "IV_HWADDR": "device-fingerprint",
        "PY3_BIND": "/not-a-real-python-interpreter",
    }
    result = _run_hook(runtime["connect"], env)
    assert result.returncode != 0, "设备绑定写入依赖故障必须拒绝连接"
