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
        ({"ssh_password": "secret"}, False, True, "可远程运维", "success"),
        ({"ssh_private_key": "PRIVATE"}, False, True, "可远程运维", "success"),
        ({}, True, True, "可远程运维", "success"),
        ({}, False, False, "缺少凭据", "warning"),
        ({"ssh_host": ""}, True, False, "未配置 SSH", "unknown"),
        ({"ssh_username": ""}, True, False, "未配置 SSH", "unknown"),
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

    assert row["mesh_label"] == "全部组"
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
