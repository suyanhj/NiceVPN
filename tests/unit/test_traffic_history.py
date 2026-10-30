# -*- coding: utf-8 -*-
"""首页最近 5 分钟流量采样测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import app.services.monitor.traffic_history as history_mod
from app.services.monitor.service_monitor import InstanceStatus
from app.services.monitor.traffic_history import TrafficHistory


def test_traffic_history_keeps_exactly_five_minutes_and_replaces_same_minute() -> None:
    history = TrafficHistory()
    start = datetime(2026, 10, 30, 1, 0, tzinfo=timezone.utc)

    for index in range(6):
        history.record(
            timestamp=start + timedelta(minutes=index),
            rx_bytes=100 + index,
            tx_bytes=200 + index,
        )

    samples = history.recent()
    assert len(samples) == 5
    assert samples[0].timestamp == start + timedelta(minutes=1)
    assert samples[-1].timestamp == start + timedelta(minutes=5)

    history.record(
        timestamp=start + timedelta(minutes=5, seconds=45),
        rx_bytes=999,
        tx_bytes=1999,
    )
    samples = history.recent()
    assert len(samples) == 5
    assert samples[-1].timestamp == start + timedelta(minutes=5)
    assert samples[-1].rx_bytes == 999
    assert samples[-1].tx_bytes == 1999


class _Monitor:
    def check_all_instances(self, names: list[str]):
        name = names[0]
        if name == "broken":
            raise RuntimeError("status unavailable")
        return [
            InstanceStatus(
                name=name,
                active=True,
                client_count=1,
                bytes_received=1000 if name == "a" else 2000,
                bytes_sent=3000 if name == "a" else 4000,
            )
        ]


def test_sample_current_traffic_isolates_instance_failure_and_records_one_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = TrafficHistory()
    monkeypatch.setattr(history_mod, "_TRAFFIC_HISTORY", history)
    now = datetime(2026, 10, 30, 2, 3, 45, tzinfo=timezone.utc)

    sample = history_mod.sample_current_traffic(
        instance_names=["a", "broken", "b"],
        monitor=_Monitor(),
        now=lambda: now,
    )

    assert sample is not None
    assert sample.timestamp == now.replace(second=0, microsecond=0)
    assert sample.rx_bytes == 3000
    assert sample.tx_bytes == 7000
    assert history.recent() == [sample]


def test_sample_current_traffic_does_not_write_fake_zero_without_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = TrafficHistory()
    monkeypatch.setattr(history_mod, "_TRAFFIC_HISTORY", history)

    assert history_mod.sample_current_traffic(
        instance_names=[],
        monitor=_Monitor(),
    ) is None
    assert history.recent() == []


def test_scheduler_samples_traffic_once_per_minute() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / "app" / "core" / "scheduler.py").read_text(
        encoding="utf-8"
    )

    assert "schedule.every(1).minutes.do(sample_current_traffic)" in source
    assert "sample_current_traffic()" in source
