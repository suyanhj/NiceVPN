# -*- coding: utf-8 -*-
"""仪表盘共享组件、图表、刷新与响应式内容测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from nicegui.testing import user_simulation

from app.services.monitor.service_monitor import InstanceStatus
from app.ui.adapters.dashboard import DashboardSnapshot
from app.ui.pages.dashboard import DashboardPage, _format_bytes


def _snapshot(
    *,
    errors: bool = False,
) -> DashboardSnapshot:
    return DashboardSnapshot(
        updated_at=datetime(2026, 10, 20, 10, 30, tzinfo=timezone.utc),
        instances=[
            InstanceStatus(
                "server1",
                True,
                3,
                1024,
                2048,
            ),
            InstanceStatus(
                "server2",
                False,
                0,
                4096,
                8192,
            ),
        ],
        instance_errors=(
            {"broken": "status read failed"} if errors else {}
        ),
        user_count=12,
        group_count=3,
        rule_count=8,
        enabled_rule_count=6,
        cert_min_expiry_days=15,
        source_errors=(
            {"certs": "cert index broken"} if errors else {}
        ),
    )


def test_dashboard_uses_shared_metrics_charts_and_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = DashboardPage()
    snap = _snapshot()
    monkeypatch.setattr(page, "_collect_snapshot", lambda: snap)

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("在线设备")
            await user.should_see("3")
            await user.should_see("共 12 个注册用户")
            await user.should_see("服务状态")
            await user.should_see("1/2")
            await user.should_see("累计流量")
            await user.should_see("↑ 5.0 KB")
            await user.should_see("↓ 10.0 KB · 当前快照")
            await user.should_see("服务健康度与状态分布")
            await user.should_see("当前累计流量分布")
            await user.should_see("6/8 启用")
            await user.should_see("15 天后到期")

    asyncio.run(verify())

    assert page._snapshot is snap
    assert page._health_chart is not None
    assert page._health_chart.chart is not None
    assert page._traffic_chart is not None
    assert page._traffic_chart.chart is not None
    assert page._health_chart.help is not None
    assert page._traffic_chart.help is not None
    assert page._updated_label.text.startswith("最后更新 ")


def test_dashboard_no_instances_renders_chart_empty_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = DashboardPage()
    snap = DashboardSnapshot(
        updated_at=datetime.now(timezone.utc),
        user_count=0,
        group_count=0,
        rule_count=0,
        enabled_rule_count=0,
    )
    monkeypatch.setattr(page, "_collect_snapshot", lambda: snap)

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            await user.open("/")
            await user.should_see("0/0")
            await user.should_see("尚未纳管实例")
            await user.should_see("暂无图表数据")
            await user.should_see("尚未配置 OpenVPN 实例")

    asyncio.run(verify())

    assert page._health_chart.chart is None
    assert page._traffic_chart.chart is None


def test_dashboard_partial_errors_do_not_hide_other_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = DashboardPage()
    snap = _snapshot(errors=True)
    monkeypatch.setattr(page, "_collect_snapshot", lambda: snap)

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            await user.open("/")
            await user.should_see("在线设备")
            await user.should_see("共 12 个注册用户")
            await user.should_see("1 采集失败")
            await user.should_see("读取失败")
            await user.should_see("部分数据不可用")
            await user.should_see("实例 broken: status read failed")
            await user.should_see("certs: cert index broken")

    asyncio.run(verify())


def test_dashboard_manual_refresh_recollects_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = DashboardPage()
    first = _snapshot()
    second = DashboardSnapshot(
        updated_at=datetime(2026, 10, 20, 11, 0, tzinfo=timezone.utc),
        instances=[
            InstanceStatus(
                "server1",
                True,
                9,
                10,
                20,
            )
        ],
        user_count=20,
        group_count=4,
        rule_count=10,
        enabled_rule_count=10,
        cert_min_expiry_days=90,
    )
    values = iter([first, second])
    monkeypatch.setattr(page, "_collect_snapshot", lambda: next(values))

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            await user.open("/")
            assert page._snapshot is first
            page._refresh_dashboard()
            assert page._snapshot is second
            await user.should_see("共 20 个注册用户")
            await user.should_see("9")
            await user.should_see("10/10 启用")

    asyncio.run(verify())



@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0.0 B"),
        (1024, "1.0 KB"),
        (1024 * 1024, "1.0 MB"),
        (-1, "0.0 B"),
    ],
)
def test_dashboard_byte_format(value: int, expected: str) -> None:
    assert _format_bytes(value) == expected


def test_dashboard_v2_visual_contract_files_exist() -> None:
    """首页 V2 只增强视觉层，不把业务文本搬进 Vue。"""

    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    vue_source = (
        root / "app" / "ui" / "vue" / "VpnSpotlightSurface.vue"
    ).read_text(encoding="utf-8")
    dashboard_source = (
        root / "app" / "ui" / "pages" / "dashboard.py"
    ).read_text(encoding="utf-8")
    shell_css = (
        root / "app" / "ui" / "static" / "css" / "shell.css"
    ).read_text(encoding="utf-8")

    assert "vpn-v2-spotlight" in vue_source
    assert "prefers-reduced-motion" in vue_source
    assert "v2_effect=True" in dashboard_source
    assert "dashboard-page--v2" in dashboard_source
    assert "vpn-metric-card--v2" in shell_css
    assert "dashboard-v2-title" not in dashboard_source
