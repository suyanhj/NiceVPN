# -*- coding: utf-8 -*-
"""仪表盘快照、局部错误和图表 option 测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.monitor.service_monitor import InstanceStatus
from app.services.monitor.traffic_history import TrafficSample
from app.ui.adapters.dashboard import (
    cert_status,
    collect_dashboard_snapshot,
    service_health_chart_options,
    traffic_history_chart_options,
)


class FakeMonitor:
    def __init__(self, *, fail: set[str] | None = None):
        self.fail = set(fail or set())

    def check_all_instances(self, names: list[str]):
        name = names[0]
        if name in self.fail:
            raise RuntimeError(f"{name} status failed")
        return [
            InstanceStatus(
                name=name,
                active=name != "stopped",
                client_count=2 if name == "server1" else 1,
                bytes_received=1024,
                bytes_sent=2048,
            )
        ]


class FakeCertService:
    def __init__(self, days: int = 20):
        self.days = days

    def list_all(self):
        expires = (
            datetime.now(timezone.utc) + timedelta(days=self.days, hours=1)
        ).isoformat()
        return [
            {
                "status": "valid",
                "expires_at": expires,
            }
        ]


class FakeRuleService:
    def list_unified_flat(self):
        return [
            {"id": "a", "enabled": True},
            {"id": "b", "enabled": False},
            {"id": "c"},
        ]


class FakeCountService:
    def __init__(self, count: int):
        self.value = count

    def count(self) -> int:
        return self.value


def test_snapshot_normal_values(tmp_path) -> None:
    snap = collect_dashboard_snapshot(
        ["server1", "stopped"],
        monitor=FakeMonitor(),
        user_service_factory=lambda: FakeCountService(2),
        group_service_factory=lambda: FakeCountService(1),
        cert_service_factory=lambda: FakeCertService(20),
        rule_service_factory=FakeRuleService,
    )

    assert snap.configured_instance_count == 2
    assert snap.active_count == 1
    assert snap.stopped_count == 1
    assert snap.total_clients == 3
    assert snap.total_rx == 2048
    assert snap.total_tx == 4096
    assert snap.user_count == 2
    assert snap.group_count == 1
    assert snap.rule_count == 3
    assert snap.enabled_rule_count == 2
    assert snap.cert_min_expiry_days in {19, 20}
    assert snap.source_errors == {}


def test_snapshot_partial_instance_failure_is_isolated(tmp_path) -> None:
    snap = collect_dashboard_snapshot(
        ["server1", "broken", "stopped"],
        monitor=FakeMonitor(fail={"broken"}),
        user_service_factory=lambda: FakeCountService(0),
        group_service_factory=lambda: FakeCountService(0),
        cert_service_factory=lambda: FakeCertService(30),
        rule_service_factory=FakeRuleService,
    )

    assert [item.name for item in snap.instances] == [
        "server1",
        "stopped",
    ]
    assert snap.instance_errors == {
        "broken": "broken status failed"
    }
    assert snap.configured_instance_count == 3
    assert snap.active_count == 1
    assert snap.health_score < 100


def test_snapshot_no_instances_stays_healthy(tmp_path) -> None:
    snap = collect_dashboard_snapshot(
        [],
        monitor=FakeMonitor(),
        user_service_factory=lambda: FakeCountService(0),
        group_service_factory=lambda: FakeCountService(0),
        cert_service_factory=lambda: FakeCertService(90),
        rule_service_factory=FakeRuleService,
    )

    assert snap.instances == []
    assert snap.configured_instance_count == 0
    assert snap.total_clients == 0
    assert snap.health_score == 100
    assert snap.source_errors == {}


def test_service_health_chart_normal_empty_and_single() -> None:
    normal = collect_dashboard_snapshot(
        ["server1", "stopped"],
        monitor=FakeMonitor(),
        cert_service_factory=lambda: FakeCertService(),
        rule_service_factory=FakeRuleService,
    )
    option = service_health_chart_options(normal)
    assert option["series"][0]["type"] == "pie"
    assert option["series"][0]["data"] == [
        {"name": "运行中", "value": 1},
        {"name": "已停止", "value": 1},
    ]
    assert option["graphic"][0]["style"]["fill"] == "#e6edf7"
    assert option["graphic"][0]["style"]["fontWeight"] == 600

    empty = collect_dashboard_snapshot(
        [],
        monitor=FakeMonitor(),
        cert_service_factory=lambda: FakeCertService(),
        rule_service_factory=FakeRuleService,
    )
    assert service_health_chart_options(empty)["series"][0]["data"] == []

    one = collect_dashboard_snapshot(
        ["server1"],
        monitor=FakeMonitor(),
        cert_service_factory=lambda: FakeCertService(),
        rule_service_factory=FakeRuleService,
    )
    assert service_health_chart_options(one)["series"][0]["data"] == [
        {"name": "运行中", "value": 1}
    ]


def test_traffic_history_chart_uses_only_latest_five_samples() -> None:
    start = datetime(2026, 10, 30, 1, 0, tzinfo=timezone.utc)
    samples = [
        TrafficSample(
            timestamp=start + timedelta(minutes=index),
            rx_bytes=1000 + index,
            tx_bytes=2000 + index,
        )
        for index in range(6)
    ]

    option = traffic_history_chart_options(samples)

    assert len(option["xAxis"]["data"]) == 5
    expected_labels = [
        sample.timestamp.astimezone().strftime("%H:%M")
        for sample in samples[-5:]
    ]
    assert option["xAxis"]["data"] == expected_labels
    assert option["series"][0]["name"] == "累计上行"
    assert option["series"][0]["type"] == "line"
    assert option["series"][0]["data"] == [1001, 1002, 1003, 1004, 1005]
    assert option["series"][1]["name"] == "累计下行"
    assert option["series"][1]["data"] == [2001, 2002, 2003, 2004, 2005]
    assert option["yAxis"]["name"] == "Bytes"


def test_cert_status_tones() -> None:
    assert cert_status(None) == ("无有效证书", "unknown")
    assert cert_status(-2) == ("已过期 2 天", "failed")
    assert cert_status(10, warn_days=30)[1] == "warning"
    assert cert_status(120, warn_days=30)[1] == "success"
