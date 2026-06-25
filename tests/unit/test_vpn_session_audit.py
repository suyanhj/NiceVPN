# -*- coding: utf-8 -*-
"""VPN 会话审计 JSONL 输出测试。"""

import importlib.util
import json
from pathlib import Path


def _load_module():
    """从 app/scripts 加载独立脚本模块。"""
    script_path = Path(__file__).resolve().parents[2] / "app" / "scripts" / "vpn_session_audit.py"
    spec = importlib.util.spec_from_file_location("vpn_session_audit", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_event_uses_openvpn_env(monkeypatch):
    """OpenVPN 环境变量应映射为统一扁平字段。"""
    mod = _load_module()
    monkeypatch.setenv("VPN_INSTANCE_NAME", "srv1")
    monkeypatch.setenv("common_name", "huangjiang")
    monkeypatch.setenv("ifconfig_pool_remote_ip", "10.8.0.23")
    monkeypatch.setenv("trusted_ip", "1.2.3.4")
    monkeypatch.setenv("trusted_port", "54321")
    monkeypatch.setenv("proto_1", "udp")
    monkeypatch.setenv("time_unix", "1780905662")
    monkeypatch.setenv("script_type", "client-connect")

    event = mod.build_event("vpn_session_start")

    assert event["schema_version"] == "vpn_audit_v1"
    assert event["event_source"] == "openvpn"
    assert event["event_dataset"] == "vpn.session"
    assert event["event_type"] == "vpn_session_start"
    assert event["vpn_instance"] == "srv1"
    assert event["vpn_username"] == "huangjiang"
    assert event["vpn_virtual_ip"] == "10.8.0.23"
    assert event["client_real_ip"] == "1.2.3.4"
    assert event["client_real_port"] == 54321
    assert event["transport_proto"] == "udp"
    assert event["openvpn_time_unix"] == 1780905662


def test_append_event_writes_jsonl(tmp_path: Path):
    """审计事件应按 JSONL 追加写入。"""
    mod = _load_module()
    log_file = tmp_path / "openvpn-session-audit.jsonl"
    mod.append_event(log_file, {"schema_version": "vpn_audit_v1", "event_type": "vpn_session_end"})

    data = json.loads(log_file.read_text(encoding="utf-8"))
    assert data == {"schema_version": "vpn_audit_v1", "event_type": "vpn_session_end"}
