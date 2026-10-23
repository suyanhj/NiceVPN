# -*- coding: utf-8 -*-
"""最终集成：代表性大数据量转换与 AG Grid 构建回归。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from time import perf_counter

from nicegui.testing import user_simulation

from app.ui.adapters.certs import adapt_cert_row
from app.ui.adapters.firewall import (
    adapt_center_rule_rows,
    adapt_remote_rule_rows,
)
from app.ui.adapters.peers import adapt_peer_view
from app.ui.components.data_grid import render_data_grid
from app.ui.pages.certs import CertsPage
from app.ui.pages.firewall import FirewallPage
from app.ui.pages.users import UsersPage


def _users(count: int) -> list[dict]:
    page = UsersPage()
    return [
        page._build_user_grid_row(
            {
                "username": f"user-{index:04d}",
                "group_id": f"group-{index % 20:02d}",
                "status": "active" if index % 7 else "disabled",
                "created_at": "2026-10-20T08:00:00+00:00",
                "cert_serial": f"SERIAL-{index:08d}",
            },
            None,
            {},
            f"10.{8 + (index // 65000)}.{(index // 250) % 250}.{index % 250 + 1}",
            {f"group-{index % 20:02d}": f"用户组 {index % 20:02d}"},
            live_available=True,
        )
        for index in range(count)
    ]


def _certs(count: int) -> list[dict]:
    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    expires = (now + timedelta(days=90)).isoformat()
    return [
        adapt_cert_row(
            {
                "common_name": f"user-{index:04d}",
                "serial": f"{index:032X}",
                "status": "valid",
                "expires_at": expires,
            },
            now=now,
        )
        for index in range(count)
    ]


def _center_rules(count: int) -> list[dict]:
    raw = [
        {
            "id": f"rule-{index:04d}",
            "owner_type": "group",
            "owner_id": f"group-{index % 20:02d}",
            "deployment_target": "center",
            "action": "accept" if index % 3 else "drop",
            "protocol": "tcp" if index % 2 else "udp",
            "source_subnet": f"10.{index % 200}.0.0/24",
            "dest_ip": f"172.16.{index % 200}.10",
            "dest_port": str(10000 + index % 500),
            "priority": (index + 1) * 10,
            "enabled": index % 5 != 0,
            "description": f"规模规则 {index}",
        }
        for index in range(count)
    ]
    names = {f"group-{i:02d}": f"用户组 {i:02d}" for i in range(20)}
    return adapt_center_rule_rows(raw, group_names=names)


def _remote_rules(count: int) -> list[dict]:
    raw = [
        {
            "rest": (
                f"-p tcp -s 10.{index % 200}.0.0/24 "
                f"-d 172.16.{index % 200}.10 "
                f"--dport {20000 + index % 500} -j ACCEPT"
            ),
            "enabled": index % 5 != 0,
            "priority": (index + 1) * 10,
            "description": f"远端规则 {index}",
        }
        for index in range(count)
    ]
    return adapt_remote_rule_rows(raw, peer_id="peer-scale")


def _peers(count: int) -> list[dict]:
    group_names = {f"group-{i:02d}": f"用户组 {i:02d}" for i in range(20)}
    return [
        adapt_peer_view(
            {
                "id": f"peer-{index:03d}",
                "name": f"站点 {index:03d}",
                "bound_username": f"peer-user-{index:03d}",
                "lan_cidrs": [f"192.168.{index % 200}.0/24"],
                "mesh_route_visible_group_ids": [
                    f"group-{index % 20:02d}"
                ],
                "ssh_host": f"203.0.113.{index % 250 + 1}",
                "ssh_username": "root",
                "ssh_port": 22,
                "ssh_password": "configured",
            },
            group_names=group_names,
            has_global_ssh_key=False,
        )
        for index in range(count)
    ]


def test_representative_scale_row_models_fit_ci_budget() -> None:
    """14.6 代表性规模：14,100 行转换应保持轻量。"""

    started = perf_counter()
    users = _users(5000)
    certs = _certs(5000)
    center = _center_rules(2000)
    remote = _remote_rules(2000)
    peers = _peers(100)
    elapsed = perf_counter() - started

    assert len(users) == 5000
    assert len(certs) == 5000
    assert len(center) == 2000
    assert len(remote) == 2000
    assert len(peers) == 100

    assert UsersPage._row_matches_query(users[-1], "user-4999")
    assert certs[-1]["common_name"] == "user-4999"
    assert certs[-1]["serial"]
    assert "rule-1999" in center[-1]["search_text"]
    assert "远端规则 1999" in remote[-1]["search_text"]
    assert peers[-1]["id"] == "peer-099"
    assert peers[-1]["remote_capable"] is True

    # Hosted runner 差异较大，仅防止明显退化到逐行 I/O / O(n²)。
    assert elapsed < 12.0


def test_large_user_and_cert_grids_keep_pagination_selection_and_filtering() -> None:
    users = _users(5000)
    certs = _certs(5000)
    captured: dict[str, object] = {}

    async def verify_users() -> None:
        async with user_simulation(
            lambda: captured.setdefault(
                "users",
                render_data_grid(
                    UsersPage._user_grid_columns(),
                    users,
                    row_id="username",
                    page_size=50,
                    selected_ids={"user-0001", "user-4999"},
                ),
            )
        ) as user:
            response = await user.open("/")
            assert response.status_code == 200

    asyncio.run(verify_users())
    user_grid = captured["users"]
    assert len(user_grid.grid.options["rowData"]) == 5000
    assert user_grid.grid.options["paginationPageSize"] == 50
    assert user_grid.grid.options["rowSelection"] == {"mode": "multiRow"}
    assert "user-4999" in user_grid.grid.options[":onFirstDataRendered"]

    captured.clear()

    async def verify_certs() -> None:
        async with user_simulation(
            lambda: captured.setdefault(
                "certs",
                render_data_grid(
                    CertsPage._grid_columns(),
                    certs,
                    row_id="id",
                    page_size=50,
                    selected_ids={"user-4999"},
                ),
            )
        ) as user:
            response = await user.open("/")
            assert response.status_code == 200

    asyncio.run(verify_certs())
    cert_grid = captured["certs"]
    assert len(cert_grid.grid.options["rowData"]) == 5000
    assert cert_grid.grid.options["paginationPageSize"] == 50
    assert "user-4999" in cert_grid.grid.options[":onFirstDataRendered"]


def test_large_center_and_remote_firewall_grids_keep_virtualized_table_shape() -> None:
    center = _center_rules(2000)
    remote = _remote_rules(2000)
    captured: dict[str, object] = {}

    async def verify_center() -> None:
        async with user_simulation(
            lambda: captured.setdefault(
                "center",
                render_data_grid(
                    FirewallPage._center_grid_columns(can_reorder=True),
                    center,
                    row_id="id",
                    page_size=50,
                    selected_ids={"rule-0001", "rule-1999"},
                ),
            )
        ) as user:
            response = await user.open("/")
            assert response.status_code == 200

    asyncio.run(verify_center())
    center_grid = captured["center"]
    assert len(center_grid.grid.options["rowData"]) == 2000
    assert center_grid.grid.options["pagination"] is True
    assert center_grid.grid.options["columnDefs"][0]["rowDrag"] is True

    captured.clear()

    async def verify_remote() -> None:
        async with user_simulation(
            lambda: captured.setdefault(
                "remote",
                render_data_grid(
                    FirewallPage._remote_grid_columns(can_reorder=True),
                    remote,
                    row_id="id",
                    page_size=50,
                    selected_ids={"peer-scale:0", "peer-scale:1999"},
                ),
            )
        ) as user:
            response = await user.open("/")
            assert response.status_code == 200

    asyncio.run(verify_remote())
    remote_grid = captured["remote"]
    assert len(remote_grid.grid.options["rowData"]) == 2000
    assert remote_grid.grid.options["paginationPageSize"] == 50
    assert remote_grid.grid.options["columnDefs"][0]["rowDrag"] is True
    assert "peer-scale:1999" in remote_grid.grid.options[
        ":onFirstDataRendered"
    ]
