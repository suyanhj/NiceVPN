# -*- coding: utf-8 -*-
"""仪表盘页面：隔离采集当前运行快照并使用共享后台组件展示。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from nicegui import ui

from app.core.config import load_config
from app.core.constants import CERT_EXPIRY_WARN_DAYS
from app.services.monitor.service_monitor import ServiceMonitor
from app.ui.adapters.dashboard import (
    DashboardSnapshot,
    cert_status,
    collect_dashboard_snapshot,
    service_health_chart_options,
    traffic_snapshot_chart_options,
)
from app.ui.components.actions import secondary_action
from app.ui.components.cards import info_card, metric_card
from app.ui.components.charts import render_chart_card
from app.ui.components.status import status_badge
from app.ui.components.toolbars import render_page_toolbar

logger = logging.getLogger(__name__)


def _format_bytes(size: int) -> str:
    """将字节数转换为易读格式。"""

    value = float(max(int(size or 0), 0))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


class DashboardPage:
    """仪表盘页面。"""

    def __init__(self) -> None:
        self._monitor = ServiceMonitor()
        self._content = None
        self._updated_label = None
        self._snapshot: DashboardSnapshot | None = None
        self._health_chart = None
        self._traffic_chart = None

    def _collect_snapshot(self) -> DashboardSnapshot:
        """采集当前仪表盘快照；配置读取失败也返回可渲染对象。"""

        try:
            config = load_config()
            instance_names = list((config.get("instances") or {}).keys())
        except Exception as exc:
            logger.exception("读取仪表盘配置失败")
            snapshot = DashboardSnapshot(
                updated_at=datetime.now(timezone.utc),
            )
            snapshot.source_errors["config"] = str(exc)
            return snapshot

        return collect_dashboard_snapshot(
            instance_names,
            monitor=self._monitor,
        )

    def render(self) -> None:
        """渲染共享首页外壳，并立即采集一次快照。"""

        def header_actions() -> None:
            self._updated_label = ui.label("尚未刷新").classes(
                "section-caption"
            )
            secondary_action(
                "刷新",
                icon="refresh",
                on_click=self._refresh_dashboard,
            )

        with ui.column().classes("page-shell mgmt-page dashboard-page"):
            toolbar = render_page_toolbar(secondary=header_actions)
            toolbar.container.classes(add="vpn-toolbar--end")
            self._content = ui.column().classes("w-full gap-4")

        self._refresh_dashboard()

    def _refresh_dashboard(self) -> None:
        """重新采集并局部重建首页。"""

        if self._content is None:
            return

        snapshot = self._collect_snapshot()
        self._snapshot = snapshot
        if self._updated_label is not None:
            local = snapshot.updated_at.astimezone()
            self._updated_label.text = (
                "最后更新 " + local.strftime("%Y-%m-%d %H:%M:%S")
            )

        self._content.clear()
        with self._content:
            self._render_metrics(snapshot)
            self._render_charts(snapshot)
            self._render_summary(snapshot)

    def _render_metrics(self, snapshot: DashboardSnapshot) -> None:
        with ui.element("div").classes("dashboard-overview-grid"):
            metric_card(
                "在线设备",
                snapshot.total_clients,
                helper=f"共 {snapshot.user_count} 个注册用户",
                icon="devices",
                tone="success" if snapshot.total_clients else "default",
                on_click=lambda: ui.navigate.to("/users"),
            )

            configured = snapshot.configured_instance_count
            if snapshot.instance_errors:
                service_tone = "warning"
                service_helper = (
                    f"{snapshot.active_count} 运行 · "
                    f"{len(snapshot.instance_errors)} 采集失败"
                )
            elif configured and snapshot.active_count == configured:
                service_tone = "success"
                service_helper = "全部实例运行中"
            elif configured:
                service_tone = "warning"
                service_helper = f"{snapshot.stopped_count} 个实例已停止"
            else:
                service_tone = "default"
                service_helper = "尚未纳管实例"

            metric_card(
                "服务状态",
                f"{snapshot.active_count}/{configured}",
                helper=service_helper,
                icon="dns",
                tone=service_tone,
                on_click=lambda: ui.navigate.to("/services"),
            )

            metric_card(
                "累计流量",
                f"↑ {_format_bytes(snapshot.total_rx)}",
                helper=f"↓ {_format_bytes(snapshot.total_tx)} · 当前快照",
                icon="swap_vert",
                tone="info",
            )


    def _render_charts(self, snapshot: DashboardSnapshot) -> None:
        with ui.element("div").classes("dashboard-chart-grid"):
            self._health_chart = render_chart_card(
                "服务健康度与状态分布",
                service_health_chart_options(snapshot),
                description="基于当前实例运行状态和采集失败计算。",
                empty_message="尚未配置 OpenVPN 实例。",
            )
            self._traffic_chart = render_chart_card(
                "当前累计流量分布",
                traffic_snapshot_chart_options(snapshot),
                description=(
                    "仅展示本次 status 快照中的累计上/下行字节，"
                    "不是历史趋势。"
                ),
                empty_message="尚未配置 OpenVPN 实例，暂无流量快照。",
            )

    def _render_summary(self, snapshot: DashboardSnapshot) -> None:
        cert_label, cert_tone = cert_status(
            snapshot.cert_min_expiry_days,
            warn_days=CERT_EXPIRY_WARN_DAYS,
        )
        cert_error = snapshot.source_errors.get("certs")
        cert_status_tone = (
            "warning" if cert_error else cert_tone
        )
        cert_status_label = (
            "读取失败" if cert_error else cert_label
        )
        health_tone = (
            "success"
            if snapshot.health_score >= 80
            else ("warning" if snapshot.health_score >= 50 else "failed")
        )

        with ui.card().classes("dashboard-status-strip"):
            self._status_item(
                "实例",
                f"{snapshot.active_count}/{snapshot.configured_instance_count} 运行",
            )
            self._status_item("用户", str(snapshot.user_count))
            self._status_item("用户组", str(snapshot.group_count))
            self._status_item(
                "防火墙",
                f"{snapshot.enabled_rule_count}/{snapshot.rule_count} 启用",
            )
            with ui.element("div").classes("dashboard-status-item"):
                ui.label("证书").classes("dashboard-status-item__label")
                status_badge(
                    cert_status_tone,
                    label=cert_status_label,
                )
            with ui.element("div").classes("dashboard-status-item"):
                ui.label("健康度").classes("dashboard-status-item__label")
                status_badge(
                    health_tone,
                    label=f"{snapshot.health_score}%",
                )

        if snapshot.instance_errors or snapshot.source_errors:
            messages: list[str] = []
            for name, error in snapshot.instance_errors.items():
                messages.append(f"实例 {name}: {error}")
            for source, error in snapshot.source_errors.items():
                messages.append(f"{source}: {error}")
            info_card(
                "部分数据不可用",
                "；".join(messages),
                icon="warning",
                tone="warning",
            )

    @staticmethod
    def _status_item(label: str, value: str) -> None:
        """渲染首页横向运行状态项。"""

        with ui.element("div").classes("dashboard-status-item"):
            ui.label(label).classes("dashboard-status-item__label")
            ui.label(value).classes("dashboard-status-item__value")
