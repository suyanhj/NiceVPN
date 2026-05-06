# -*- coding: utf-8 -*-
"""仪表盘页面，展示实时指标与告警信息。"""

import json
import logging
from datetime import datetime, timezone, timedelta

from nicegui import ui

from app.core.config import load_config
from app.core.constants import (
    ALERTS_FILE, FIREWALL_DIR, USERS_DIR, GROUPS_DIR,
    CERT_EXPIRY_WARN_DAYS,
)
from app.services.monitor.service_monitor import ServiceMonitor

logger = logging.getLogger(__name__)


def _format_bytes(size: int) -> str:
    """将字节数转换为易读格式（单位字母大写，与仪表盘展示一致）。"""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024:
            return f"{value:.1f} {unit}".upper()
        value /= 1024
    return f"{value:.1f} PB".upper()


def _count_json_files(directory) -> int:
    """统计目录中的 JSON 文件数量。"""
    if not directory.exists():
        return 0
    return sum(1 for _ in directory.glob("*.json"))


def _count_active_rules() -> int:
    """统计防火墙规则数量。"""
    return _count_json_files(FIREWALL_DIR)


def _load_alerts() -> list[dict]:
    """加载告警列表。"""
    if not ALERTS_FILE.exists():
        return []
    try:
        data = json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _get_cert_min_expiry_days() -> int | None:
    """获取最近到期证书的剩余天数，无有效证书时返回 None。"""
    try:
        from app.services.cert.cert_service import CertService
        certs = CertService().list_all()
        now = datetime.now(timezone.utc)
        min_days = None
        for cert in certs:
            if cert["status"] != "valid":
                continue
            try:
                expires_at = datetime.fromisoformat(cert["expires_at"])
                days_left = (expires_at - now).days
                if min_days is None or days_left < min_days:
                    min_days = days_left
            except (ValueError, TypeError):
                continue
        return min_days
    except Exception as exc:
        logger.debug("获取证书到期天数失败: %s", exc)
        return None


class DashboardPage:
    """仪表盘页面。"""

    def __init__(self) -> None:
        self._monitor = ServiceMonitor()

    def render(self) -> None:
        """渲染仪表盘。"""
        config = load_config()
        instance_names = list(config.get("instances", {}).keys())
        statuses = self._monitor.check_all_instances(instance_names)
        alerts = _load_alerts()
        total_clients = sum(item.client_count for item in statuses)
        total_rx = sum(item.bytes_received for item in statuses)
        total_tx = sum(item.bytes_sent for item in statuses)
        active_count = sum(1 for item in statuses if item.active)
        rule_count = _count_active_rules()
        user_count = _count_json_files(USERS_DIR)
        group_count = _count_json_files(GROUPS_DIR)
        cert_min_days = _get_cert_min_expiry_days()
        health_score = 100
        if statuses:
            health_score = max(0, min(100, int((active_count / len(statuses)) * 100) - len(alerts) * 2))

        updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with ui.column().classes("page-shell dashboard-page"):
            with ui.element("div").classes("dashboard-header"):
                with ui.element("div").classes("dashboard-header-copy"):
                    ui.label("Global Overview").classes("dashboard-kicker")
                    ui.label("运行状态概览").classes("dashboard-title")
                ui.label(f"最后更新 {updated_at}").classes("dashboard-updated")

            with ui.element("div").classes("dashboard-metric-grid"):
                self._metric_card(
                    "在线设备", str(total_clients),
                    f"共 {user_count} 个注册用户",
                    "devices", "#2dd4bf",
                )
                self._metric_card(
                    "服务状态",
                    f"{active_count}/{len(statuses) or 0}",
                    "运行中" if active_count else "未运行",
                    "dns", "#60a5fa",
                    with_pulse=active_count > 0,
                )
                self._traffic_metric_card(total_rx, total_tx)
                self._metric_card(
                    "安全告警", str(len(alerts)),
                    "系统处于稳定状态" if not alerts else "存在待处理事件",
                    "security", "#fb7185",
                )

            with ui.element("div").classes("dashboard-bottom-grid items-stretch"):
                with ui.element("section").classes("dashboard-glass-card flex flex-col"):
                    ui.label("运行摘要 (Summary)").classes("dashboard-card-title mb-2")
                    with ui.element("div").classes("dashboard-summary-list flex-1"):
                        self._summary_row("已纳管实例", str(len(statuses)))
                        cert_display = f"{cert_min_days} 天" if cert_min_days is not None else "无证书"
                        cert_class = "dashboard-summary-warn" if cert_min_days is not None and cert_min_days <= CERT_EXPIRY_WARN_DAYS else ""
                        self._summary_row("最近证书到期", cert_display, value_class=cert_class)
                        self._summary_row("防火墙规则", str(rule_count))
                        self._summary_row("用户组", str(group_count))
                        self._summary_row("注册用户", str(user_count))

                    with ui.element("div").classes("dashboard-health-box mt-auto"):
                        ui.label("健康度评估").classes("dashboard-health-label")
                        ui.label(f"{health_score}%").classes("dashboard-health-value")

                with ui.element("section").classes("dashboard-glass-card flex flex-col"):
                    with ui.row().classes("items-center gap-3 mb-2"):
                        ui.icon("error_outline", color="#fb7185")
                        ui.label("最新告警").classes("dashboard-mini-title")
                    if alerts:
                        ui.label(alerts[0].get("title", "存在待处理告警")).classes("dashboard-mini-copy flex-1")
                    else:
                        ui.label("当前无活动告警记录。").classes("dashboard-mini-copy flex-1")
                    
                    with ui.element("div").classes("dashboard-health-box mt-auto bg-transparent border-none p-0"):
                        ui.label("系统稳定").classes("dashboard-health-label text-[#2dd4bf] opacity-80")

                with ui.element("section").classes("dashboard-glass-card flex flex-col"):
                    with ui.row().classes("items-center gap-3 mb-2"):
                        ui.icon("history", color="#2dd4bf")
                        ui.label("规则统计").classes("dashboard-mini-title")
                    ui.label(f"已生效 {rule_count} 条防火墙规则。配置了基础的内外网隔离和访问控制策略。").classes("dashboard-mini-copy flex-1")
                    
                    with ui.element("div").classes("dashboard-health-box mt-auto bg-transparent border-none p-0"):
                        ui.label("防护中").classes("dashboard-health-label text-[#2dd4bf] opacity-80")

    def _traffic_metric_card(self, total_rx: int, total_tx: int) -> None:
        """总上行 / 总下行（OpenVPN status：服务端收=用户侧上行，服务端发=用户侧下行）。"""
        with ui.element("section").classes("dashboard-glass-card dashboard-metric--traffic-balance"):
            with ui.element("div").classes("dashboard-metric-top"):
                ui.label("总流量").classes("dashboard-metric-label")
                ui.icon("swap_vert").style("color:#f59e0b;opacity:0.35;font-size:24px;")
            ui.label(f"总上行 {_format_bytes(total_rx)}").classes("dashboard-metric-value")
            ui.label(f"总下行 {_format_bytes(total_tx)}").classes("dashboard-metric-footer")

    def _metric_card(
        self,
        label: str,
        value: str,
        footer: str,
        icon: str,
        icon_color: str,
        with_pulse: bool = False,
    ) -> None:
        """渲染顶部指标卡。"""
        with ui.element("section").classes("dashboard-glass-card"):
            with ui.element("div").classes("dashboard-metric-top"):
                ui.label(label).classes("dashboard-metric-label")
                ui.icon(icon).style(f"color:{icon_color};opacity:0.35;font-size:24px;")
            ui.label(value).classes("dashboard-metric-value")
            if with_pulse:
                with ui.element("div").classes("dashboard-metric-footer dashboard-status-footer"):
                    ui.element("span").classes("dashboard-badge-pulse")
                    ui.label(footer)
            else:
                ui.label(footer).classes("dashboard-metric-footer")

    def _summary_row(self, label: str, value: str, value_class: str = "") -> None:
        """渲染摘要行。"""
        with ui.element("div").classes("dashboard-summary-row"):
            ui.label(label).classes("dashboard-summary-name")
            classes = "dashboard-summary-val"
            if value_class:
                classes += f" {value_class}"
            ui.label(value).classes(classes)
