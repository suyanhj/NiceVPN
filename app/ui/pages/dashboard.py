# -*- coding: utf-8 -*-
"""仪表盘页面：共享指标卡、ECharts、快捷入口与局部错误隔离。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nicegui import ui

from app.core.config import load_config
from app.core.constants import (
    ALERTS_FILE,
    CERT_EXPIRY_WARN_DAYS,
    FIREWALL_DIR,
    GROUPS_DIR,
    USERS_DIR,
)
from app.services.monitor.service_monitor import ServiceMonitor
from app.ui.components.cards import info_card, metric_card, quick_link_card
from app.ui.components.charts import render_chart_card
from app.ui.components.page_header import render_page_header
from app.ui.components.status import status_badge

logger = logging.getLogger(__name__)


def _format_bytes(size: int) -> str:
    """将字节数转换为易读格式。"""

    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024:
            return f"{value:.1f} {unit}".upper()
        value /= 1024
    return f"{value:.1f} PB".upper()


def _count_json_files(directory: Path) -> int:
    if not directory.exists():
        return 0
    return sum(1 for _ in directory.glob("*.json"))


def _count_active_rules() -> int:
    return _count_json_files(FIREWALL_DIR)


def _load_alerts() -> list[dict]:
    if not ALERTS_FILE.exists():
        return []
    data = json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def _get_cert_min_expiry_days() -> int | None:
    from app.services.cert.cert_service import CertService

    certs = CertService().list_all()
    now = datetime.now(timezone.utc)
    min_days: int | None = None
    for cert in certs:
        if cert.get("status") != "valid":
            continue
        try:
            expires_at = datetime.fromisoformat(str(cert.get("expires_at") or ""))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            days_left = (expires_at - now).days
        except (ValueError, TypeError):
            continue
        if min_days is None or days_left < min_days:
            min_days = days_left
    return min_days


@dataclass(slots=True)
class DashboardSnapshot:
    """仪表盘一次刷新所需的稳定数据快照。"""

    total_clients: int = 0
    active_instances: int = 0
    total_instances: int = 0
    total_rx: int = 0
    total_tx: int = 0
    alerts: list[dict] = field(default_factory=list)
    rule_count: int = 0
    user_count: int = 0
    group_count: int = 0
    cert_min_days: int | None = None
    health_score: int = 100
    updated_at: str = ""
    errors: dict[str, str] = field(default_factory=dict)


def collect_dashboard_snapshot(
    *,
    config: Any | None = None,
    monitor: ServiceMonitor | None = None,
) -> DashboardSnapshot:
    """独立采集各数据源，单项失败不会阻断其他指标。"""

    snapshot = DashboardSnapshot(
        updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    resolved_config = config
    if resolved_config is None:
        try:
            resolved_config = load_config()
        except Exception as exc:
            logger.exception("仪表盘加载配置失败")
            snapshot.errors["config"] = str(exc)
            resolved_config = {}

    instance_names = list(resolved_config.get("instances", {}).keys())
    try:
        statuses = (monitor or ServiceMonitor()).check_all_instances(instance_names)
        snapshot.total_instances = len(statuses)
        snapshot.active_instances = sum(1 for item in statuses if item.active)
        snapshot.total_clients = sum(int(item.client_count or 0) for item in statuses)
        snapshot.total_rx = sum(int(item.bytes_received or 0) for item in statuses)
        snapshot.total_tx = sum(int(item.bytes_sent or 0) for item in statuses)
    except Exception as exc:
        logger.exception("仪表盘采集实例状态失败")
        snapshot.errors["services"] = str(exc)
        snapshot.total_instances = len(instance_names)

    try:
        snapshot.alerts = _load_alerts()
    except Exception as exc:
        logger.exception("仪表盘读取告警失败")
        snapshot.errors["alerts"] = str(exc)
        snapshot.alerts = []

    for key, directory in (
        ("users", USERS_DIR),
        ("groups", GROUPS_DIR),
        ("rules", FIREWALL_DIR),
    ):
        try:
            value = _count_json_files(directory)
            if key == "users":
                snapshot.user_count = value
            elif key == "groups":
                snapshot.group_count = value
            else:
                snapshot.rule_count = value
        except Exception as exc:
            logger.exception("仪表盘统计 %s 失败", key)
            snapshot.errors[key] = str(exc)

    try:
        snapshot.cert_min_days = _get_cert_min_expiry_days()
    except Exception as exc:
        logger.exception("仪表盘证书到期统计失败")
        snapshot.errors["certs"] = str(exc)

    if snapshot.total_instances:
        base = int((snapshot.active_instances / snapshot.total_instances) * 100)
    else:
        base = 100
    snapshot.health_score = max(
        0,
        min(100, base - len(snapshot.alerts) * 2 - len(snapshot.errors) * 5),
    )
    return snapshot


class DashboardPage:
    """仪表盘页面。"""

    def __init__(self) -> None:
        self._monitor = ServiceMonitor()
        self.snapshot: DashboardSnapshot | None = None

    def render(self) -> None:
        self.snapshot = collect_dashboard_snapshot(monitor=self._monitor)
        snapshot = self.snapshot

        def header_actions() -> None:
            ui.button("刷新", icon="refresh", on_click=lambda: ui.navigate.to("/")).props(
                "outline no-caps color=primary"
            )

        with ui.column().classes("page-shell dashboard-v2"):
            render_page_header(
                "运行状态概览",
                description="实时汇总 VPN 服务、在线设备、流量、证书、防火墙与安全告警。",
                actions=header_actions,
            )
            ui.label(f"最后更新 {snapshot.updated_at}").classes("dashboard-v2-updated")

            if snapshot.errors:
                info_card(
                    "部分数据源不可用",
                    "；".join(f"{name}: {message}" for name, message in snapshot.errors.items()),
                    icon="warning_amber",
                    tone="warning",
                )

            with ui.element("div").classes("dashboard-v2-metrics"):
                metric_card(
                    "在线设备",
                    snapshot.total_clients,
                    helper=f"共 {snapshot.user_count} 个注册用户",
                    icon="devices",
                    tone="success" if snapshot.total_clients else "default",
                    on_click=lambda: ui.navigate.to("/users"),
                )
                metric_card(
                    "服务状态",
                    f"{snapshot.active_instances}/{snapshot.total_instances}",
                    helper="运行中的 OpenVPN 实例",
                    icon="dns",
                    tone=(
                        "success"
                        if snapshot.total_instances and snapshot.active_instances == snapshot.total_instances
                        else "warning"
                    ),
                    on_click=lambda: ui.navigate.to("/services"),
                )
                metric_card(
                    "总上行",
                    _format_bytes(snapshot.total_rx),
                    helper=f"总下行 {_format_bytes(snapshot.total_tx)}",
                    icon="swap_vert",
                    tone="info",
                )
                metric_card(
                    "安全告警",
                    len(snapshot.alerts),
                    helper="无活动告警" if not snapshot.alerts else "存在待处理事件",
                    icon="security",
                    tone="success" if not snapshot.alerts else "danger",
                )

            with ui.element("div").classes("dashboard-v2-charts"):
                render_chart_card(
                    "服务健康度",
                    {
                        "tooltip": {"trigger": "item"},
                        "series": [
                            {
                                "type": "pie",
                                "radius": ["58%", "78%"],
                                "label": {"show": False},
                                "data": (
                                    [
                                        {"name": "运行", "value": snapshot.active_instances},
                                        {
                                            "name": "停止/异常",
                                            "value": max(
                                                0,
                                                snapshot.total_instances
                                                - snapshot.active_instances,
                                            ),
                                        },
                                    ]
                                    if snapshot.total_instances
                                    else []
                                ),
                            }
                        ],
                    },
                    empty_message="当前没有已纳管实例。",
                )
                render_chart_card(
                    "当前流量分布",
                    {
                        "tooltip": {"trigger": "item"},
                        "series": [
                            {
                                "type": "pie",
                                "radius": "70%",
                                "data": (
                                    [
                                        {"name": "上行", "value": snapshot.total_rx},
                                        {"name": "下行", "value": snapshot.total_tx},
                                    ]
                                    if snapshot.total_rx or snapshot.total_tx
                                    else []
                                ),
                            }
                        ],
                    },
                    description="当前快照分布，不伪造历史趋势。",
                    empty_message="当前快照没有流量数据。",
                )

            with ui.element("div").classes("dashboard-v2-summary"):
                with ui.card().classes("vpn-info-card dashboard-v2-summary-card"):
                    ui.label("运行摘要").classes("vpn-info-card__title")
                    self._summary_row("已纳管实例", str(snapshot.total_instances))
                    self._summary_row("防火墙规则", str(snapshot.rule_count))
                    self._summary_row("用户组", str(snapshot.group_count))
                    self._summary_row("注册用户", str(snapshot.user_count))
                    cert_display = (
                        f"{snapshot.cert_min_days} 天"
                        if snapshot.cert_min_days is not None
                        else "无有效证书"
                    )
                    self._summary_row("最近证书到期", cert_display)
                    with ui.row().classes("items-center gap-2 q-mt-sm"):
                        ui.label("健康度").classes("section-caption")
                        status_badge(
                            (
                                "success"
                                if snapshot.health_score >= 90
                                else "warning"
                                if snapshot.health_score >= 60
                                else "failed"
                            ),
                            label=f"{snapshot.health_score}%",
                            icon="monitor_heart",
                        )

                with ui.card().classes("vpn-info-card dashboard-v2-summary-card"):
                    ui.label("最新告警").classes("vpn-info-card__title")
                    if snapshot.alerts:
                        for alert in snapshot.alerts[:5]:
                            ui.label(
                                str(alert.get("title") or alert.get("message") or "待处理告警")
                            ).classes("vpn-info-card__body")
                    else:
                        ui.label("当前无活动告警记录。").classes("vpn-info-card__body")

            ui.label("快捷入口").classes("vpn-info-card__title q-mt-md")
            with ui.element("div").classes("dashboard-v2-links"):
                quick_link_card(
                    "用户管理",
                    description="账号、在线会话与客户端配置",
                    icon="people",
                    route="/users",
                )
                quick_link_card(
                    "服务管理",
                    description="本机与对端 OpenVPN 服务",
                    icon="dns",
                    route="/services",
                )
                quick_link_card(
                    "证书管理",
                    description="到期、续签、吊销与 CA",
                    icon="verified_user",
                    route="/certs",
                )
                quick_link_card(
                    "防火墙",
                    description="中心与对端访问控制策略",
                    icon="security",
                    route="/firewall",
                )

    @staticmethod
    def _summary_row(label: str, value: str) -> None:
        with ui.row().classes("w-full items-center justify-between gap-4"):
            ui.label(label).classes("section-caption")
            ui.label(value).classes("vpn-data-value__text")

    # 保留旧调用入口；新页面统一使用共享 MetricCard。
    def _traffic_metric_card(self, total_rx: int, total_tx: int) -> None:
        metric_card(
            "总流量",
            f"上行 {_format_bytes(total_rx)}",
            helper=f"下行 {_format_bytes(total_tx)}",
            icon="swap_vert",
            tone="info",
        )

    def _metric_card(
        self,
        label: str,
        value: str,
        footer: str,
        icon: str,
        icon_color: str,
        with_pulse: bool = False,
    ) -> None:
        metric_card(
            label,
            value,
            helper=footer,
            icon=icon,
            tone="success" if with_pulse else "default",
        )
