# -*- coding: utf-8 -*-
"""服务管理展示适配测试。"""

from types import SimpleNamespace

import pytest

from app.ui.adapters.services import (
    adapt_local_service_view,
    adapt_peer_service_view,
    format_traffic,
)


def test_local_service_adapter_running_stopped_unknown_and_busy() -> None:
    config = {"proto": "udp", "port": 1194, "subnet": "10.8.0.0/16"}

    running = adapt_local_service_view(
        SimpleNamespace(
            name="server1",
            active=True,
            client_count=3,
            bytes_received=1536,
            bytes_sent=1048576,
        ),
        config,
    )
    stopped = adapt_local_service_view(
        SimpleNamespace(
            name="server2",
            active=False,
            client_count=0,
            bytes_received=0,
            bytes_sent=0,
        ),
        config,
    )
    unknown = adapt_local_service_view(
        SimpleNamespace(
            name="server3",
            active=None,
            client_count=0,
            bytes_received=0,
            bytes_sent=0,
        ),
        config,
    )
    busy = adapt_local_service_view(
        SimpleNamespace(
            name="server4",
            active=True,
            client_count=1,
            bytes_received=0,
            bytes_sent=0,
        ),
        config,
        busy=True,
    )

    assert running["status_label"] == "运行中"
    assert running["status_tone"] == "success"
    assert running["can_stop"] is True
    assert running["can_restart"] is True
    assert running["can_start"] is False
    assert running["traffic_received"] == "1.5 KB"
    assert running["traffic_sent"] == "1.0 MB"

    assert stopped["status_label"] == "已停止"
    assert stopped["can_start"] is True
    assert stopped["can_restart"] is False

    assert unknown["status_label"] == "未知"
    assert unknown["status_tone"] == "unknown"
    assert unknown["can_start"] is False

    assert busy["status_label"] == "操作中"
    assert busy["can_stop"] is False
    assert busy["can_restart"] is False


@pytest.mark.parametrize(
    ("status", "error", "expected_state", "label", "tone"),
    [
        (
            {
                "exists": True,
                "active_state": "active",
                "unit_file_state": "enabled",
                "config_exists": True,
            },
            None,
            "running",
            "运行中",
            "success",
        ),
        (
            {
                "exists": True,
                "active_state": "inactive",
                "unit_file_state": "disabled",
                "config_exists": True,
            },
            None,
            "stopped",
            "已停止",
            "disabled",
        ),
        (
            {
                "exists": True,
                "active_state": "failed",
                "unit_file_state": "enabled",
                "config_exists": True,
            },
            None,
            "failed",
            "运行失败",
            "failed",
        ),
        (
            {"exists": False},
            None,
            "missing",
            "服务未安装",
            "warning",
        ),
        (None, "SSH timeout", "failed", "远程不可达", "failed"),
        (None, None, "unknown", "待查询", "unknown"),
    ],
)
def test_peer_service_adapter_status_matrix(
    status,
    error,
    expected_state,
    label,
    tone,
) -> None:
    peer = {
        "id": "peer-1",
        "name": "广州站点",
        "ssh_host": "203.0.113.8",
        "ssh_username": "root",
        "bound_username": "peer-gz",
    }
    view = adapt_peer_service_view(peer, status, error=error)

    assert view["state"] == expected_state
    assert view["status_label"] == label
    assert view["status_tone"] == tone


def test_peer_service_adapter_action_availability() -> None:
    peer = {
        "id": "peer-1",
        "ssh_host": "203.0.113.8",
        "ssh_username": "root",
    }

    running = adapt_peer_service_view(
        peer,
        {
            "exists": True,
            "active_state": "active",
            "unit_file_state": "enabled",
            "config_exists": True,
        },
    )
    stopped = adapt_peer_service_view(
        peer,
        {
            "exists": True,
            "active_state": "inactive",
            "unit_file_state": "disabled",
            "config_exists": True,
        },
    )
    unreachable = adapt_peer_service_view(peer, error="network down")

    assert running["can_stop"] is True
    assert running["can_restart"] is True
    assert running["can_disable"] is True
    assert running["can_start"] is False

    assert stopped["can_start"] is True
    assert stopped["can_enable"] is True
    assert stopped["can_stop"] is False

    assert unreachable["can_start"] is False
    assert unreachable["can_restart"] is False
    assert unreachable["can_view_logs"] is False


def test_format_traffic_handles_invalid_and_large_values() -> None:
    assert format_traffic("bad") == "0 B"
    assert format_traffic(-1) == "0 B"
    assert format_traffic(1024) == "1.0 KB"
    assert format_traffic(1024 * 1024) == "1.0 MB"
