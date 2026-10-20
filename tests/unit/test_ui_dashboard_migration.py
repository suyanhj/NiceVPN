# -*- coding: utf-8 -*-
"""仪表盘快照、图表与局部错误隔离测试。"""

from __future__ import annotations

import asyncio

import pytest
from box import Box
from nicegui.testing import user_simulation

from app.services.monitor.service_monitor import InstanceStatus
from app.ui.pages import dashboard
from app.ui.pages.dashboard import DashboardPage, collect_dashboard_snapshot


class FakeMonitor:
    def __init__(self, statuses=None, error: Exception | None = None):
        self.statuses = statuses or []
        self.error = error

    def check_all_instances(self, names):
        if self.error:
            raise self.error
        return self.statuses


def _config(*names: str):
    return Box(
        {"instances": {name: {} for name in names}},
        default_box=True,
        default_box_attr=None,
    )


def test_dashboard_snapshot_collects_current_state_without_fake_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dashboard, "_load_alerts", lambda: [{"title": "test"}])
    monkeypatch.setattr(dashboard, "_count_json_files", lambda path: 3)
    monkeypatch.setattr(dashboard, "_get_cert_min_expiry_days", lambda: 12)

    snapshot = collect_dashboard_snapshot(
        config=_config("server-a", "server-b"),
        monitor=FakeMonitor(
            [
                InstanceStatus(
                    name="server-a",
                    active=True,
                    client_count=2,
                    bytes_received=1024,
                    bytes_sent=2048,
                ),
                InstanceStatus(
                    name="server-b",
                    active=False,
                    client_count=0,
                    bytes_received=0,
                    bytes_sent=0,
                ),
            ]
        ),
    )

    assert snapshot.total_clients == 2
    assert snapshot.active_instances == 1
    assert snapshot.total_instances == 2
    assert snapshot.total_rx == 1024
    assert snapshot.total_tx == 2048
    assert snapshot.alerts == [{"title": "test"}]
    assert snapshot.user_count == 3
    assert snapshot.group_count == 3
    assert snapshot.rule_count == 3
    assert snapshot.cert_min_days == 12
    assert snapshot.health_score < 100
    assert snapshot.errors == {}


def test_dashboard_snapshot_isolates_service_alert_and_cert_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_alerts():
        raise ValueError("bad alerts")

    def fail_cert():
        raise RuntimeError("bad certs")

    monkeypatch.setattr(dashboard, "_load_alerts", fail_alerts)
    monkeypatch.setattr(dashboard, "_count_json_files", lambda path: 0)
    monkeypatch.setattr(dashboard, "_get_cert_min_expiry_days", fail_cert)

    snapshot = collect_dashboard_snapshot(
        config=_config("server"),
        monitor=FakeMonitor(error=RuntimeError("systemctl unavailable")),
    )

    assert snapshot.total_instances == 1
    assert snapshot.active_instances == 0
    assert snapshot.total_clients == 0
    assert snapshot.alerts == []
    assert "services" in snapshot.errors
    assert "alerts" in snapshot.errors
    assert "certs" in snapshot.errors
    assert snapshot.health_score < 100


def test_dashboard_no_instances_is_valid_empty_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard, "_load_alerts", lambda: [])
    monkeypatch.setattr(dashboard, "_count_json_files", lambda path: 0)
    monkeypatch.setattr(dashboard, "_get_cert_min_expiry_days", lambda: None)

    snapshot = collect_dashboard_snapshot(
        config=_config(),
        monitor=FakeMonitor([]),
    )

    assert snapshot.total_instances == 0
    assert snapshot.active_instances == 0
    assert snapshot.health_score == 100


def test_dashboard_page_builds_shared_metrics_charts_and_quick_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dashboard, "_load_alerts", lambda: [])
    monkeypatch.setattr(dashboard, "_count_json_files", lambda path: 2)
    monkeypatch.setattr(dashboard, "_get_cert_min_expiry_days", lambda: 15)
    monkeypatch.setattr(
        dashboard,
        "load_config",
        lambda: _config("server"),
    )
    monkeypatch.setattr(
        "app.ui.pages.dashboard.ServiceMonitor.check_all_instances",
        lambda self, names: [
            InstanceStatus(
                name="server",
                active=True,
                client_count=4,
                bytes_received=4096,
                bytes_sent=8192,
            )
        ],
    )

    async def verify_page() -> None:
        async with user_simulation(lambda: DashboardPage().render()) as user:
            response = await user.open("/")
            assert response.status_code == 200
            for text in (
                "运行状态概览",
                "在线设备",
                "服务状态",
                "总上行",
                "安全告警",
                "服务健康度",
                "当前流量分布",
                "当前快照分布，不伪造历史趋势。",
                "运行摘要",
                "快捷入口",
                "用户管理",
                "服务管理",
                "证书管理",
                "防火墙",
            ):
                await user.should_see(text)

    asyncio.run(verify_page())
