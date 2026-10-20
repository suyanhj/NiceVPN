# -*- coding: utf-8 -*-
"""仪表盘页面：隔离采集当前运行快照并使用共享后台组件展示。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

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
from app.ui.components.cards import info_card, metric_card, quick_link_card
from app.ui.components.charts import render_chart_card
from app.ui.components.page_header import render_page_header
from app.ui.components.status import status_badge

logger = logging.getLogger(__name__)

_SHORTCUT_ROUTES = {
    "users": "/users",
    "services": "/services",
    "certs": "/certs",
    "firewall": "/firewall",
}


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
            render_page_header(
                "运行状态概览",
                description=(
                    "查看当前连接、服务、累计流量与安全状态。"
                    "仪表盘展示实时快照，不伪造历史趋势。"
                ),
                actions=header_actions,
            )
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
            self._render_shortcuts()

    def _render_metrics(self, snapshot: DashboardSnapshot) -> None:
        with ui.element("div").classes("dashboard-metric-grid"):
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

            alert_tone = "danger" if snapshot.alerts else (
                "warning" if "alerts" in snapshot.source_errors else "success"
            )
            alert_helper = (
                "告警文件读取失败"
                if "alerts" in snapshot.source_errors
                else (
                    "存在待处理事件"
                    if snapshot.alerts
                    else "当前无活动告警"
                )
            )
            metric_card(
                "安全告警",
                len(snapshot.alerts),
                helper=alert_helper,
                icon="security",
                tone=alert_tone,
                on_click=self._refresh_dashboard,
            )

    def _render_charts(self, snapshot: DashboardSnapshot) -> None:
        with ui.element("div").classes("dashboard-bottom-grid items-stretch"):
            self._health_chart = render_chart_card(
                "服务健康度与状态分布",
                service_health_chart_options(snapshot),
                description=(
                    "基于当前实例运行状态、采集失败和活动告警计算。"
                ),
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
        with ui.element("div").classes("dashboard-bottom-grid items-stretch"):
            with ui.card().classes("vpn-info-card"):
                ui.label("运行摘要").classes("vpn-info-card__title")
                self._summary_row(
                    "已纳管实例",
                    str(snapshot.configured_instance_count),
                )
                self._summary_row(
                    "运行实例",
                    str(snapshot.active_count),
                )
                self._summary_row(
                    "防火墙规则",
                    (
                        f"{snapshot.enabled_rule_count} 启用 / "
                        f"{snapshot.rule_count} 总计"
                    ),
                )
                self._summary_row("用户组", str(snapshot.group_count))
                self._summary_row("注册用户", str(snapshot.user_count))
                with ui.row().classes("items-center gap-2 q-mt-sm"):
                    ui.label("综合健康度").classes("dashboard-summary-name")
                    status_badge(
                        (
                            "success"
                            if snapshot.health_score >= 80
                            else (
                                "warning"
                                if snapshot.health_score >= 50
                                else "failed"
                            )
                        ),
                        label=f"{snapshot.health_score}%",
                    )

            cert_label, cert_tone = cert_status(
                snapshot.cert_min_expiry_days,
                warn_days=CERT_EXPIRY_WARN_DAYS,
            )
            cert_error = snapshot.source_errors.get("certs")
            info_card(
                "证书状态",
                (
                    f"证书数据读取失败：{cert_error}"
                    if cert_error
                    else cert_label
                ),
                icon="verified_user",
                tone=(
                    "warning"
                    if cert_error
                    else {
                        "success": "success",
                        "warning": "warning",
                        "failed": "danger",
                        "unknown": "default",
                    }[cert_tone]
                ),
            )

            alert_error = snapshot.source_errors.get("alerts")
            if alert_error:
                info_card(
                    "最新告警",
                    f"告警文件读取失败：{alert_error}",
                    icon="error_outline",
                    tone="warning",
                )
            elif snapshot.alerts:
                latest = snapshot.alerts[0]
                info_card(
                    "最新告警",
                    str(
                        latest.get("title")
                        or latest.get("message")
                        or "存在待处理告警"
                    ),
                    icon="error_outline",
                    tone="danger",
                )
            else:
                info_card(
                    "最新告警",
                    "当前无活动告警记录。",
                    icon="check_circle",
                    tone="success",
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

    def _render_shortcuts(self) -> None:
        ui.label("快捷入口").classes("mgmt-kicker")
        with ui.element("div").classes("dashboard-metric-grid"):
            quick_link_card(
                "用户管理",
                description="用户、连接与下载配置",
                icon="people",
                on_click=lambda: self._navigate_shortcut("users"),
            )
            quick_link_card(
                "服务管理",
                description="实例状态、日志与配置",
                icon="dns",
                on_click=lambda: self._navigate_shortcut("services"),
            )
            quick_link_card(
                "证书管理",
                description="证书有效期、续签与吊销",
                icon="verified_user",
                on_click=lambda: self._navigate_shortcut("certs"),
            )
            quick_link_card(
                "防火墙规则",
                description="中心与对端访问策略",
                icon="shield",
                on_click=lambda: self._navigate_shortcut("firewall"),
            )
            quick_link_card(
                "刷新安全告警",
                description="重新读取当前 alerts.json 快照",
                icon="notifications_active",
                on_click=self._refresh_dashboard,
            )

    @staticmethod
    def shortcut_routes() -> dict[str, str]:
        """返回可测试的快捷入口路由映射。"""

        return dict(_SHORTCUT_ROUTES)

    def _navigate_shortcut(self, key: str) -> None:
        route = _SHORTCUT_ROUTES.get(str(key))
        if not route:
            raise ValueError(f"未知快捷入口: {key}")
        ui.navigate.to(route)

    @staticmethod
    def _summary_row(
        label: str,
        value: str,
        value_class: str = "",
    ) -> None:
        """渲染摘要行。"""

        with ui.element("div").classes("dashboard-summary-row"):
            ui.label(label).classes("dashboard-summary-name")
            classes = "dashboard-summary-val"
            if value_class:
                classes += f" {value_class}"
            ui.label(value).classes(classes)
