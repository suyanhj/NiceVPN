# -*- coding: utf-8 -*-
"""仪表盘快照采集与图表 option 构建。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from app.core.constants import (
    CERT_EXPIRY_WARN_DAYS,
    GROUPS_DIR,
    USERS_DIR,
)
from app.services.monitor.service_monitor import InstanceStatus, ServiceMonitor

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DashboardSnapshot:
    """一次仪表盘采样；只表示当前快照，不伪造历史时序。"""

    updated_at: datetime
    instances: list[InstanceStatus] = field(default_factory=list)
    instance_errors: dict[str, str] = field(default_factory=dict)
    user_count: int = 0
    group_count: int = 0
    rule_count: int = 0
    enabled_rule_count: int = 0
    cert_min_expiry_days: int | None = None
    source_errors: dict[str, str] = field(default_factory=dict)

    @property
    def total_clients(self) -> int:
        return sum(max(int(item.client_count or 0), 0) for item in self.instances)

    @property
    def total_rx(self) -> int:
        return sum(max(int(item.bytes_received or 0), 0) for item in self.instances)

    @property
    def total_tx(self) -> int:
        return sum(max(int(item.bytes_sent or 0), 0) for item in self.instances)

    @property
    def active_count(self) -> int:
        return sum(1 for item in self.instances if item.active)

    @property
    def stopped_count(self) -> int:
        return sum(1 for item in self.instances if not item.active)

    @property
    def configured_instance_count(self) -> int:
        return len(self.instances) + len(self.instance_errors)

    @property
    def health_score(self) -> int:
        total = self.configured_instance_count
        if total <= 0:
            base = 100
        else:
            base = int((self.active_count / total) * 100)
        penalty = len(self.instance_errors) * 5
        return max(0, min(100, base - penalty))


def _count_json_files(directory: Path) -> int:
    if not directory.exists():
        return 0
    return sum(1 for _ in directory.glob("*.json"))


def _cert_min_expiry_days(cert_service: Any) -> int | None:
    now = datetime.now(timezone.utc)
    minimum: int | None = None
    for cert in cert_service.list_all():
        if str(cert.get("status") or "") != "valid":
            continue
        raw = str(cert.get("expires_at") or "").strip()
        if not raw:
            continue
        try:
            expires_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        days = (expires_at.astimezone(timezone.utc) - now).days
        minimum = days if minimum is None else min(minimum, days)
    return minimum


def collect_dashboard_snapshot(
    instance_names: list[str],
    *,
    monitor: ServiceMonitor | None = None,
    users_dir: Path = USERS_DIR,
    groups_dir: Path = GROUPS_DIR,
    cert_service_factory: Callable[[], Any] | None = None,
    rule_service_factory: Callable[[], Any] | None = None,
    now: Callable[[], datetime] | None = None,
) -> DashboardSnapshot:
    """采集当前快照；各数据源彼此隔离，单点失败不阻断首页。"""

    monitor = monitor or ServiceMonitor()
    snapshot = DashboardSnapshot(
        updated_at=(now or (lambda: datetime.now(timezone.utc)))(),
    )

    for name in instance_names:
        try:
            result = monitor.check_all_instances([name])
            if result:
                snapshot.instances.append(result[0])
            else:
                snapshot.instance_errors[name] = "未返回实例状态"
        except Exception as exc:
            logger.exception("采集仪表盘实例失败 instance=%s", name)
            snapshot.instance_errors[name] = str(exc)


    try:
        snapshot.user_count = _count_json_files(users_dir)
    except Exception as exc:
        logger.error("统计用户失败: %s", exc)
        snapshot.source_errors["users"] = str(exc)

    try:
        snapshot.group_count = _count_json_files(groups_dir)
    except Exception as exc:
        logger.error("统计用户组失败: %s", exc)
        snapshot.source_errors["groups"] = str(exc)

    if cert_service_factory is None:
        from app.services.cert.cert_service import CertService

        cert_service_factory = CertService
    try:
        snapshot.cert_min_expiry_days = _cert_min_expiry_days(
            cert_service_factory()
        )
    except Exception as exc:
        logger.error("采集证书到期时间失败: %s", exc)
        snapshot.source_errors["certs"] = str(exc)

    if rule_service_factory is None:
        from app.services.firewall.rule_service import FirewallRuleService

        rule_service_factory = FirewallRuleService
    try:
        rules = list(rule_service_factory().list_unified_flat())
        snapshot.rule_count = len(rules)
        snapshot.enabled_rule_count = sum(
            1 for row in rules if bool(row.get("enabled", True))
        )
    except Exception as exc:
        logger.error("采集防火墙规则失败: %s", exc)
        snapshot.source_errors["firewall"] = str(exc)

    return snapshot


def service_health_chart_options(snapshot: DashboardSnapshot) -> dict[str, Any]:
    """服务运行状态分布；无配置实例时返回空 series data。"""

    total = snapshot.configured_instance_count
    if total <= 0:
        data: list[dict[str, Any]] = []
    else:
        data = [
            {"name": "运行中", "value": snapshot.active_count},
            {"name": "已停止", "value": snapshot.stopped_count},
            {"name": "采集失败", "value": len(snapshot.instance_errors)},
        ]
        data = [item for item in data if int(item["value"]) > 0]

    return {
        "tooltip": {"trigger": "item"},
        "legend": {"bottom": 0},
        "series": [
            {
                "name": "服务状态",
                "type": "pie",
                "radius": ["52%", "72%"],
                "center": ["50%", "44%"],
                "label": {"formatter": "{b}: {c}"},
                "data": data,
            }
        ],
        "graphic": [
            {
                "type": "text",
                "left": "center",
                "top": "39%",
                "style": {
                    "text": f"{snapshot.health_score}%\n健康度",
                    "textAlign": "center",
                    "fontSize": 16,
                },
            }
        ] if total > 0 else [],
    }


def traffic_snapshot_chart_options(snapshot: DashboardSnapshot) -> dict[str, Any]:
    """当前累计上下行快照；不是历史趋势。"""

    if snapshot.configured_instance_count <= 0:
        data: list[int] = []
    else:
        data = [snapshot.total_rx, snapshot.total_tx]
    return {
        "tooltip": {"trigger": "axis"},
        "grid": {"left": 20, "right": 20, "top": 20, "bottom": 30, "containLabel": True},
        "xAxis": {
            "type": "category",
            "data": ["累计上行", "累计下行"] if data else [],
        },
        "yAxis": {
            "type": "value",
            "name": "Bytes",
        },
        "series": [
            {
                "name": "当前累计流量",
                "type": "bar",
                "data": data,
                "barMaxWidth": 56,
            }
        ],
    }


def cert_status(
    days: int | None,
    *,
    warn_days: int = CERT_EXPIRY_WARN_DAYS,
) -> tuple[str, str]:
    """证书摘要状态文案与 tone。"""

    if days is None:
        return "无有效证书", "unknown"
    if days < 0:
        return f"已过期 {-days} 天", "failed"
    if days <= warn_days:
        return f"{days} 天后到期", "warning"
    return f"{days} 天后到期", "success"
