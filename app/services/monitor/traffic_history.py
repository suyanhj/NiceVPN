# -*- coding: utf-8 -*-
"""首页最近 5 分钟流量采样。

仅保存在当前 NiceVPN 进程内：每分钟最多一个点，最多 5 点；服务重启后重新开始。
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from app.core.config import load_config
from app.services.monitor.service_monitor import ServiceMonitor

logger = logging.getLogger(__name__)

TRAFFIC_HISTORY_LIMIT = 5


@dataclass(frozen=True, slots=True)
class TrafficSample:
    """一分钟流量快照；字节口径与 OpenVPN status 当前累计值一致。"""

    timestamp: datetime
    rx_bytes: int
    tx_bytes: int

    def model_dump(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "rx_bytes": self.rx_bytes,
            "tx_bytes": self.tx_bytes,
        }


class TrafficHistory:
    """线程安全的固定 5 点内存环形窗口。"""

    def __init__(self, limit: int = TRAFFIC_HISTORY_LIMIT) -> None:
        safe_limit = max(1, int(limit))
        self._samples: deque[TrafficSample] = deque(maxlen=safe_limit)
        self._lock = threading.RLock()

    @staticmethod
    def _minute(timestamp: datetime) -> datetime:
        value = timestamp
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(second=0, microsecond=0)

    def record(
        self,
        *,
        timestamp: datetime,
        rx_bytes: int,
        tx_bytes: int,
    ) -> TrafficSample:
        sample = TrafficSample(
            timestamp=self._minute(timestamp),
            rx_bytes=max(0, int(rx_bytes or 0)),
            tx_bytes=max(0, int(tx_bytes or 0)),
        )
        with self._lock:
            if self._samples and self._samples[-1].timestamp == sample.timestamp:
                self._samples[-1] = sample
            else:
                self._samples.append(sample)
        return sample

    def recent(self) -> list[TrafficSample]:
        with self._lock:
            return list(self._samples)

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()


_TRAFFIC_HISTORY = TrafficHistory()


def get_recent_traffic_samples() -> list[TrafficSample]:
    """返回当前进程最近 5 个一分钟采样点。"""

    return _TRAFFIC_HISTORY.recent()


def sample_current_traffic(
    *,
    instance_names: list[str] | None = None,
    monitor: ServiceMonitor | None = None,
    now: Callable[[], datetime] | None = None,
) -> TrafficSample | None:
    """采样一次所有已纳管实例的当前累计上下行字节。

    单个实例读取失败不阻断其他实例；若没有配置实例或全部实例采集失败，
    本分钟不写入伪造的 0 点。
    """

    if instance_names is None:
        try:
            cfg = load_config()
            instance_names = list((cfg.get("instances") or {}).keys())
        except Exception as exc:
            logger.warning("流量采样读取配置失败: %s", exc)
            return None

    names = [str(name).strip() for name in instance_names if str(name).strip()]
    if not names:
        return None

    monitor = monitor or ServiceMonitor()
    total_rx = 0
    total_tx = 0
    successes = 0

    for name in names:
        try:
            rows = monitor.check_all_instances([name])
            if not rows:
                logger.warning("流量采样实例未返回状态: %s", name)
                continue
            status = rows[0]
            total_rx += max(0, int(status.bytes_received or 0))
            total_tx += max(0, int(status.bytes_sent or 0))
            successes += 1
        except Exception as exc:
            logger.warning("流量采样实例失败 instance=%s: %s", name, exc)

    if successes <= 0:
        return None

    timestamp = (now or (lambda: datetime.now(timezone.utc)))()
    return _TRAFFIC_HISTORY.record(
        timestamp=timestamp,
        rx_bytes=total_rx,
        tx_bytes=total_tx,
    )
