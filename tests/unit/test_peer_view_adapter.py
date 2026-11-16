# -*- coding: utf-8 -*-
"""对端站点展示数据适配测试。"""

import pytest

from app.ui.adapters.peers import adapt_peer_view


BASE = {
    "id": "peer-1234567890",
    "name": "广州机房",
    "bound_username": "peer-gz",
    "lan_cidrs": ["192.168.10.0/24", "192.168.20.0/24"],
    "mesh_route_visible_group_ids": ["group-dev", "group-ops"],
    "ssh_host": "203.0.113.8",
    "ssh_username": "root",
    "ssh_port": 22,
}


@pytest.mark.parametrize(
    ("extra", "global_key", "expected_capable", "label", "tone"),
    [
        ({"ssh_password": "secret"}, False, True, "待验证", "info"),
        ({"ssh_private_key": "PRIVATE"}, False, True, "待验证", "info"),
        ({}, True, True, "待验证", "info"),
        ({}, False, False, "缺少凭据", "warning"),
        ({"ssh_host": ""}, True, False, "未配置 SSH", "unknown"),
        ({"ssh_username": ""}, True, False, "未配置 SSH", "unknown"),
        (
            {
                "ssh_password": "secret",
                "remote_status": {"ssh_status": "connected"},
            },
            False, True, "最近已连通", "success",
        ),
        (
            {
                "ssh_password": "secret",
                "remote_status": {"ssh_status": "failed", "ssh_error": "timed out"},
            },
            False, True, "最近连接失败", "failed",
        ),
    ],
)
def test_peer_adapter_auth_combinations(
    extra: dict,
    global_key: bool,
    expected_capable: bool,
    label: str,
    tone: str,
) -> None:
    row = adapt_peer_view(
        {**BASE, **extra},
        group_names={"group-dev": "开发组", "group-ops": "运维组"},
        has_global_ssh_key=global_key,
    )

    assert row["remote_capable"] is expected_capable
    assert row["connection_label"] == label
    assert row["connection_tone"] == tone
    assert row["can_install"] is expected_capable
    assert row["can_push"] is expected_capable


def test_peer_adapter_resolves_binding_lan_and_mesh_groups() -> None:
    row = adapt_peer_view(
        {**BASE, "ssh_password": "secret"},
        group_names={"group-dev": "开发组", "group-ops": "运维组"},
    )

    assert row["name"] == "广州机房"
    assert row["bound_username"] == "peer-gz"
    assert row["lan_label"] == "192.168.10.0/24、192.168.20.0/24"
    assert row["visible_group_names"] == ["开发组", "运维组"]
    assert row["mesh_label"] == "开发组、运维组"


def test_peer_adapter_empty_mesh_scope_means_all_groups() -> None:
    row = adapt_peer_view(
        {
            **BASE,
            "mesh_route_visible_group_ids": [],
            "lan_cidrs": [],
            "ssh_password": "secret",
        },
    )

    assert row["mesh_label"] == "全部用户"
    assert row["lan_label"] == "无内网"


def test_peer_adapter_busy_disables_mutating_remote_actions() -> None:
    row = adapt_peer_view(
        {**BASE, "ssh_password": "secret"},
        busy=True,
    )

    assert row["remote_capable"] is True
    assert row["connection_label"] == "远程操作中"
    assert row["connection_tone"] == "warning"
    assert row["can_install"] is False
    assert row["can_push"] is False
    assert row["can_edit"] is False
    assert row["can_delete"] is False



def test_peer_adapter_displays_tag_mesh_visibility():
    row = adapt_peer_view(
        {
            **BASE,
            "mesh_route_visible_group_ids": ["group-dev"],
            "mesh_route_visible_tag_ids": ["tag-ops", "tag-eng"],
            "ssh_password": "secret",
        },
        group_names={"group-dev": "开发组"},
        tag_names={"tag-ops": "运维", "tag-eng": "研发"},
    )

    assert row["visible_group_names"] == ["开发组"]
    assert row["visible_tag_names"] == ["运维", "研发"]
    assert row["mesh_label"] == "组：开发组；标签：运维、研发"



def test_peer_adapter_shows_remote_operation_failure_separately_from_ssh() -> None:
    row = adapt_peer_view(
        {
            **BASE,
            "ssh_password": "secret",
            "remote_status": {
                "ssh_status": "connected",
                "ssh_checked_at": "2026-11-16T18:00:00+00:00",
                "last_operation": "对端防火墙下发",
                "last_operation_status": "failed",
                "last_operation_error": "iptables timed out",
            },
        },
    )

    assert row["connection_label"] == "最近已连通"
    assert row["connection_tone"] == "success"
    assert row["ssh_verified"] is True
    assert row["remote_operation_label"] == "对端防火墙下发失败"
    assert row["remote_operation_tone"] == "failed"
    assert "iptables timed out" in row["remote_operation_tooltip"]
