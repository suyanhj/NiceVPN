# -*- coding: utf-8 -*-
"""conntrack 连接审计解析测试。"""

from ipaddress import IPv4Network


def test_parse_conntrack_new_event_with_username():
    """NEW 事件应解析为 vpn_connection_new，并补充 CCD 用户名。"""
    from app.services.audit.conntrack_audit import parse_conntrack_event

    line = (
        "[2026-06-08T09:21:00.123456+0000] [NEW] tcp 6 120 SYN_SENT "
        "src=10.8.0.23 dst=1.2.3.4 sport=53124 dport=443 [UNREPLIED] "
        "src=1.2.3.4 dst=203.0.113.10 sport=443 dport=53124"
    )

    event = parse_conntrack_event(
        line,
        IPv4Network("10.8.0.0/16"),
        {"10.8.0.23": "huangjiang"},
        "server",
    )

    assert event is not None
    assert event["event_time"] == "2026-06-08T09:21:00.123Z"
    assert event["event_source"] == "conntrack"
    assert event["event_dataset"] == "vpn.connection"
    assert event["event_type"] == "vpn_connection_new"
    assert event["vpn_instance"] == "server"
    assert event["vpn_username"] == "huangjiang"
    assert event["vpn_virtual_ip"] == "10.8.0.23"
    assert event["src_port"] == 53124
    assert event["dst_ip"] == "1.2.3.4"
    assert event["dst_port"] == 443
    assert event["transport_proto"] == "tcp"
    assert event["conntrack_event"] == "NEW"


def test_parse_conntrack_destroy_event_with_counters():
    """DESTROY 事件应解析连接结束，并保留原始方向 packets/bytes。"""
    from app.services.audit.conntrack_audit import parse_conntrack_event

    line = (
        "[1780905662.5] [DESTROY] udp 17 src=10.8.0.23 dst=8.8.8.8 "
        "sport=53000 dport=53 packets=2 bytes=140 "
        "src=8.8.8.8 dst=203.0.113.10 sport=53 dport=53000 packets=2 bytes=220"
    )

    event = parse_conntrack_event(
        line,
        IPv4Network("10.8.0.0/16"),
        {"10.8.0.23": "huangjiang"},
        "server",
    )

    assert event is not None
    assert event["event_type"] == "vpn_connection_end"
    assert event["event_time"] == "2026-06-08T08:01:02.500Z"
    assert event["transport_proto"] == "udp"
    assert event["dst_ip"] == "8.8.8.8"
    assert event["dst_port"] == 53
    assert event["packet_count"] == 2
    assert event["byte_count"] == 140
    assert event["conntrack_event"] == "DESTROY"


def test_parse_conntrack_ignores_non_vpn_source():
    """非 VPN 网段源地址应跳过。"""
    from app.services.audit.conntrack_audit import parse_conntrack_event

    line = "[NEW] tcp 6 120 src=192.168.1.10 dst=1.2.3.4 sport=53124 dport=443"

    event = parse_conntrack_event(
        line,
        IPv4Network("10.8.0.0/16"),
        {},
        "server",
    )

    assert event is None



def test_conntrack_run_loop_does_not_restart_after_stop(monkeypatch):
    """主动 stop 后不得进入 conntrack 自愈重启路径。"""
    from app.services.audit import conntrack_audit as module

    service = module.ConntrackAuditService(restart_delay_seconds=0)
    calls = []

    monkeypatch.setattr(module, "is_shutdown_requested", lambda: False)

    def run_once():
        calls.append("run")
        service._stop_event.set()

    monkeypatch.setattr(service, "_run_once", run_once)
    service._run_forever()

    assert calls == ["run"]


def test_conntrack_run_loop_honors_global_shutdown(monkeypatch):
    """应用全局 shutdown 一旦触发，conntrack 不得再次拉起子进程。"""
    from app.services.audit import conntrack_audit as module

    service = module.ConntrackAuditService(restart_delay_seconds=0)
    state = {"shutdown": False}
    calls = []

    monkeypatch.setattr(
        module,
        "is_shutdown_requested",
        lambda: bool(state["shutdown"]),
    )

    def run_once():
        calls.append("run")
        state["shutdown"] = True

    monkeypatch.setattr(service, "_run_once", run_once)
    service._run_forever()

    assert calls == ["run"]



def test_conntrack_subprocess_is_detached_from_terminal() -> None:
    """conntrack 子进程应独立于终端前台进程组，避免 Ctrl+C 直接打到子进程。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (
        root / "app" / "services" / "audit" / "conntrack_audit.py"
    ).read_text(encoding="utf-8")

    assert "start_new_session=True" in source
