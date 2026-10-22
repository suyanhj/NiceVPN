# -*- coding: utf-8 -*-
"""服务管理共享页面、操作保护与生命周期测试。"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest
from nicegui.testing import user_simulation

from app.services.monitor.service_monitor import InstanceStatus
from app.ui.pages.services import ServicesPage


class FakeMonitor:
    def __init__(self, statuses: list[InstanceStatus]):
        self.statuses = statuses

    def check_all_instances(self, names: list[str]) -> list[InstanceStatus]:
        wanted = set(names)
        return [item for item in self.statuses if item.name in wanted]


class FakePeerService:
    def __init__(self, peers: list[dict] | None = None):
        self.peers = list(peers or [])
        self.actions: list[tuple[str, str]] = []
        self.status_by_id: dict[str, dict] = {}

    def list_all(self):
        return [dict(item) for item in self.peers]

    def fetch_peer_client_service_status_via_ssh(self, peer_id: str):
        value = self.status_by_id.get(peer_id)
        if isinstance(value, Exception):
            raise value
        return dict(value or {
            "exists": True,
            "service": "openvpn-client@client",
            "active_state": "active",
            "unit_file_state": "enabled",
            "config_exists": True,
        })

    def control_peer_client_service_via_ssh(self, peer_id: str, action: str):
        self.actions.append((peer_id, action))
        return {"ok": True}


def _local_config(tmp_path: Path) -> dict:
    return {
        "openvpn_conf_dir": str(tmp_path),
        "instances": {
            "server1": {
                "proto": "udp",
                "port": 1194,
                "subnet": "10.8.0.0/16",
            },
            "server2": {
                "proto": "tcp",
                "port": 443,
                "subnet": "10.9.0.0/16",
            },
        },
    }


def test_services_page_shared_shell_local_status_and_empty_peer_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = ServicesPage()
    page._monitor = FakeMonitor([
        InstanceStatus("server1", True, 3, 1536, 1048576),
        InstanceStatus("server2", False, 0, 0, 0),
    ])
    page._peer_service = FakePeerService([])

    monkeypatch.setattr(
        "app.ui.pages.services.load_config",
        lambda: _local_config(tmp_path),
    )

    async def verify() -> None:
        async with user_simulation(lambda: page.render(initial_tab="local")) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("SERVICES")
            await user.should_see("本机实例 · 对端服务 · 日志运维")
            await user.should_see("本机实例")
            await user.should_see("对端实例")
            await user.should_see("刷新")
            await user.should_see("server1")
            await user.should_see("运行中")
            await user.should_see("在线 3")
            await user.should_see("1 / 2 运行")
            await user.should_see("server2")
            await user.should_see("已停止")
            await user.should_see("暂无对端服务")

    asyncio.run(verify())


def test_services_page_monitor_failure_is_isolated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = ServicesPage()

    class BrokenMonitor:
        def check_all_instances(self, _names):
            raise RuntimeError("systemctl unavailable")

    page._monitor = BrokenMonitor()
    page._peer_service = FakePeerService([])
    monkeypatch.setattr(
        "app.ui.pages.services.load_config",
        lambda: _local_config(tmp_path),
    )

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            await user.open("/")
            await user.should_see("实例状态加载失败")
            await user.should_see("systemctl unavailable")
            await user.should_see("重试")

    asyncio.run(verify())


def test_peer_focus_is_rendered_first(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = ServicesPage()
    page._monitor = FakeMonitor([])
    page._peer_service = FakePeerService([
        {"id": "peer-a", "name": "A"},
        {"id": "peer-b", "name": "B"},
    ])
    order: list[str] = []

    async def no_op() -> None:
        return None

    def fake_render(peer: dict):
        order.append(str(peer["id"]))
        return no_op

    monkeypatch.setattr(page, "_render_peer_client_row", fake_render)
    monkeypatch.setattr(
        "app.ui.pages.services.load_config",
        lambda: {"openvpn_conf_dir": str(tmp_path), "instances": {}},
    )

    async def verify() -> None:
        async with user_simulation(
            lambda: page.render(initial_tab="peers", focus_peer_id="peer-b")
        ) as user:
            await user.open("/")

    asyncio.run(verify())
    assert order[:2] == ["peer-b", "peer-a"]


def test_peer_remote_unreachable_updates_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = ServicesPage()
    page._active_tab = "peers"
    peer = {
        "id": "peer-1",
        "name": "广州站点",
        "ssh_host": "203.0.113.8",
        "ssh_username": "root",
        "bound_username": "peer-gz",
    }
    service = FakePeerService([peer])
    service.status_by_id["peer-1"] = RuntimeError("SSH timeout")
    page._peer_service = service
    holder: dict[str, object] = {}

    def build() -> None:
        holder["refresh"] = page._render_peer_client_row(peer)

    async def verify() -> None:
        async with user_simulation(build) as user:
            await user.open("/")
            refresh = holder["refresh"]
            await refresh()
            await user.should_see("远程不可达")
            await user.should_see("SSH timeout")

    asyncio.run(verify())


def test_local_action_is_reserved_before_background_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = ServicesPage()
    queued: list[object] = []
    messages: list[str] = []

    def fake_create(coro, **_kwargs):
        queued.append(coro)

    monkeypatch.setattr(
        "app.ui.pages.services.background_tasks.create",
        fake_create,
    )
    monkeypatch.setattr(
        "app.ui.pages.services.ui.notify",
        lambda message, **_kwargs: messages.append(str(message)),
    )

    assert page._queue_local_action("server1", "restart") is True
    assert "server1" in page._local_instance_busy
    assert page._queue_local_action("server1", "stop") is False
    assert messages[-1] == "server1 尚有本机操作进行中，请稍候"

    for coro in queued:
        coro.close()


def test_local_confirmations_describe_service_impact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = ServicesPage()
    captured: list[dict] = []

    monkeypatch.setattr(
        "app.ui.pages.services.confirm_dialog.show",
        lambda *args, **kwargs: captured.append(kwargs),
    )

    page._confirm_start("server1")
    page._confirm_stop("server1")
    page._confirm_restart("server1")

    assert captured[0]["danger_level"] == "normal"
    assert captured[1]["danger_level"] == "danger"
    assert "在线用户" in captured[1]["impact"]
    assert captured[2]["danger_level"] == "warning"
    assert "重新加载当前配置" in captured[2]["impact"]


def test_peer_service_action_guard_and_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = ServicesPage()
    service = FakePeerService([])
    page._peer_service = service
    messages: list[str] = []

    async def fake_io_bound(func, *args):
        return func(*args)

    monkeypatch.setattr("app.ui.pages.services.run.io_bound", fake_io_bound)
    monkeypatch.setattr(
        "app.ui.pages.services.ui.notify",
        lambda message, **_kwargs: messages.append(str(message)),
    )

    page._peer_action_busy.add("peer-1")
    assert asyncio.run(page._run_peer_service_action("peer-1", "restart")) is False
    assert service.actions == []

    page._peer_action_busy.clear()
    assert asyncio.run(page._run_peer_service_action("peer-1", "restart")) is True
    assert service.actions == [("peer-1", "restart")]
    assert "peer-1" not in page._peer_action_busy
    assert messages[-1] == "已执行 restart"


def test_service_log_dialog_lifecycle_and_log_paths_are_guarded() -> None:
    source = inspect.getsource(ServicesPage._open_log_tail_dialog)
    assert 'dialog.on("hide"' in source
    assert "poll_timer.deactivate()" in source
    assert "ui.scroll_area" in source
    assert ".scroll_to(percent=1.0)" in source

    peer_source = inspect.getsource(ServicesPage._show_peer_client_file_log_viewer)
    assert 'dialog.on("hide"' in peer_source
    assert 'state["open"]' in peer_source
    assert 'state["generation"]' in peer_source


def test_status_log_viewer_tolerates_missing_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = ServicesPage()
    page._conf_dir = str(tmp_path)
    captured: dict[str, object] = {}

    def fake_open(title, load, **kwargs):
        captured["title"] = title
        captured["load"] = load
        captured.update(kwargs)

    monkeypatch.setattr(page, "_open_log_tail_dialog", fake_open)
    monkeypatch.setattr(
        "app.ui.pages.services.resolve_status_log_path",
        lambda *_args, **_kwargs: tmp_path / "missing-status.log",
    )

    page._show_status_log_viewer("server1")

    caption, body = captured["load"]()
    assert captured["precheck_file"] is False
    assert "missing-status.log" in caption
    assert "(尚无文件)" in body


def test_config_save_failure_returns_false_and_success_warns_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = ServicesPage()
    messages: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "app.ui.pages.services.ui.notify",
        lambda message, type="info", **_kwargs: messages.append((str(message), type)),
    )

    def fail_save(_path, _content):
        raise OSError("read-only filesystem")

    monkeypatch.setattr("app.ui.pages.services.save_with_backup", fail_save)
    assert page._save_config_content("/tmp/server.conf", "port 1194") is False
    assert messages[-1] == ("保存失败: read-only filesystem", "negative")

    monkeypatch.setattr(
        "app.ui.pages.services.save_with_backup",
        lambda _path, _content: None,
    )
    assert page._save_config_content("/tmp/server.conf", "port 443") is True
    assert messages[-1] == (
        "已保存配置；请重启实例使新配置生效",
        "warning",
    )


def test_config_editor_uses_shared_form_and_does_not_open_missing_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = ServicesPage()
    page._conf_dir = str(tmp_path)
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    (server_dir / "server1.conf").write_text("port 1194\n", encoding="utf-8")

    monkeypatch.setattr(
        "app.ui.pages.services.backup_before_edit",
        lambda _path: tmp_path / "backup.conf",
    )

    async def verify() -> None:
        async with user_simulation(lambda: page._open_config_editor("server1")) as user:
            await user.open("/")
            await user.should_see("编辑配置 — server1.conf")
            await user.should_see("保存成功后需要重启实例")
            await user.should_see("port 1194")
            await user.should_see("保存")

    asyncio.run(verify())


def test_services_v2_visual_contract_keeps_operations_in_python() -> None:
    """Services 只迁移视觉层，不把服务控制或日志逻辑搬进 Vue。"""

    root = Path(__file__).resolve().parents[2]
    source = (root / "app" / "ui" / "pages" / "services.py").read_text(encoding="utf-8")
    shell_css = (
        root / "app" / "ui" / "static" / "css" / "shell.css"
    ).read_text(encoding="utf-8")

    assert "services-page--v2" in source
    assert "vpn-v2-page-intro" in source
    assert "start_instance" in source
    assert "stop_instance" in source
    assert "restart_instance" in source
    assert ".services-page--v2 .firewall-compact-tabs" in shell_css
    assert "fetch(" not in source
