# -*- coding: utf-8 -*-
"""对端站点共享页面迁移测试。"""

from __future__ import annotations

import asyncio

import pytest
from nicegui.testing import user_simulation

from app.ui.pages.peers import PeersPage


def _peer(**extra) -> dict:
    data = {
        "id": "peer-1234567890",
        "name": "广州站点",
        "bound_username": "peer-gz",
        "lan_cidrs": ["192.168.10.0/24"],
        "mesh_route_visible_group_ids": ["group-dev"],
        "ssh_host": "203.0.113.8",
        "ssh_username": "root",
        "ssh_port": 22,
        "ssh_password": "secret",
        "ssh_private_key": "",
    }
    data.update(extra)
    return data


class FakePeerService:
    def __init__(self, peers: list[dict]) -> None:
        self.peers = list(peers)
        self.mesh_calls = 0
        self.deleted: list[str] = []

    def list_all(self) -> list[dict]:
        return [dict(item) for item in self.peers]

    def sync_all_mesh_push_routes_in_ccd(self) -> None:
        self.mesh_calls += 1

    def delete(self, peer_id: str) -> None:
        self.deleted.append(peer_id)


class FakeGroupService:
    def list_all(self):
        return [{"id": "group-dev", "name": "开发组"}]


def test_peers_server_pager_supports_page_size_changes() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / "app" / "ui" / "pages" / "peers.py").read_text(
        encoding="utf-8"
    )
    assert "render_server_pager(" in source
    assert "on_page_size_change=self._on_peer_page_size_change" in source
    assert "page_after_size_change(" in source


def test_peers_page_uses_shared_header_toolbar_status_and_cards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = PeersPage()
    page.peer_service = FakePeerService([_peer()])
    page.group_service = FakeGroupService()
    monkeypatch.setattr(page, "_has_global_ssh_key", lambda: False)

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("新建对端")
            await user.should_see("刷新 Mesh 路由")
            await user.should_see("广州站点")
            # SSH 凭据存在不代表已连通，首次探测前应保持待验证。
            await user.should_see("待验证")
            await user.should_see("peer-gz")
            await user.should_see("192.168.10.0/24")
            await user.should_see("Mesh: 开发组")
            await user.should_see("共 1 个")

    asyncio.run(verify())

    icon_actions = [
        element
        for element in page.list_container.client.elements.values()
        if getattr(element, "_props", {}).get("aria-label")
        in {
            "一键装配",
            "推送配置",
            "服务管理",
            "远程日志",
            "部署说明",
            "编辑",
            "删除",
        }
    ]
    assert len(icon_actions) == 7


def test_peers_page_disables_remote_actions_when_credentials_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = PeersPage()
    page.peer_service = FakePeerService([_peer(ssh_password="", ssh_private_key="")])
    page.group_service = FakeGroupService()
    monkeypatch.setattr(page, "_has_global_ssh_key", lambda: False)

    async def verify() -> None:
        async with user_simulation(page.render) as user:
            await user.open("/")
            await user.should_see("缺少凭据")

    asyncio.run(verify())

    buttons = [
        element
        for element in page.list_container.client.elements.values()
        if getattr(element, "_props", {}).get("aria-label")
        in {"一键装配", "推送配置"}
    ]
    assert len(buttons) == 2
    assert all(button._props.get("disable") is True for button in buttons)


def test_peers_page_empty_and_error_states(monkeypatch: pytest.MonkeyPatch) -> None:
    empty_page = PeersPage()
    empty_page.peer_service = FakePeerService([])
    empty_page.group_service = FakeGroupService()
    monkeypatch.setattr(empty_page, "_has_global_ssh_key", lambda: False)

    async def verify_empty() -> None:
        async with user_simulation(empty_page.render) as user:
            await user.open("/")
            await user.should_see("暂无对端站点")
            await user.should_see("创建对端后")

    asyncio.run(verify_empty())

    error_page = PeersPage()

    class BrokenService:
        def list_all(self):
            raise RuntimeError("peer data broken")

    error_page.peer_service = BrokenService()
    error_page.group_service = FakeGroupService()

    async def verify_error() -> None:
        async with user_simulation(error_page.render) as user:
            await user.open("/")
            await user.should_see("对端列表加载失败")
            await user.should_see("peer data broken")
            await user.should_see("重试")

    asyncio.run(verify_error())


def test_mesh_refresh_has_duplicate_click_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = PeersPage()
    service = FakePeerService([])
    page.peer_service = service
    messages: list[str] = []
    monkeypatch.setattr(
        "app.ui.pages.peers.ui.notify",
        lambda message, **_kwargs: messages.append(str(message)),
    )

    page._mesh_sync_running = True
    page._sync_mesh_ccd_only()
    assert service.mesh_calls == 0
    assert messages == ["Mesh 路由正在刷新，请稍候"]

    page._mesh_sync_running = False
    page._sync_mesh_ccd_only()
    assert service.mesh_calls == 1
    assert messages[-1] == "已重写各用户 CCD 中的 Mesh 路由"


def test_peer_delete_uses_impact_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = PeersPage()
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "app.ui.pages.peers.confirm_dialog.show",
        lambda *args, **kwargs: captured.update(kwargs),
    )

    page._confirm_delete(_peer())

    assert captured["title"] == "删除对端"
    assert captured["danger_level"] == "danger"
    assert captured["object_count"] == 1
    assert "iptables" in str(captured["impact"])
    assert "CCD" in str(captured["impact"])


def test_peer_user_filter_is_case_insensitive_and_keeps_current_selection() -> None:
    assert PeersPage._filter_peer_bind_usernames(
        ["zhangmeiyun", "zhangjian", "liwei"],
        "ZHANG",
    ) == ["zhangmeiyun", "zhangjian"]
    assert PeersPage._filter_peer_bind_usernames(
        ["zhangmeiyun", "zhangjian", "liwei"],
        "li",
        selected="zhangmeiyun",
    ) == ["zhangmeiyun", "liwei"]
    assert PeersPage._filter_peer_bind_usernames(
        ["zhangmeiyun", "zhangjian"],
        "",
        selected="zhangjian",
    ) == ["zhangjian"]
    assert PeersPage._filter_peer_bind_usernames(
        ["zhangmeiyun", "zhangjian"],
        "",
    ) == []


def test_peer_mesh_optout_switch_disables_scopes_and_preserves_values() -> None:
    fields: dict = {}

    def page() -> None:
        PeersPage._render_mesh_route_policy(
            fields,
            {"g1": "开发组"},
            {"tag-a": "研发"},
            group_ids=["g1"],
            tag_ids=["tag-a"],
            disabled=True,
        )

    async def verify() -> None:
        async with user_simulation(page) as user:
            await user.open("/")
            await user.should_see("不向其他 VPN 客户端推送本站点路由")
            assert fields["mesh"]._props.get("disable") is True
            assert fields["mesh_tags"]._props.get("disable") is True
            assert fields["mesh"].value == ["g1"]
            assert fields["mesh_tags"].value == ["tag-a"]
            fields["mesh_route_push_disabled"].set_value(False)
            assert fields["mesh"]._props.get("disable") is not True
            assert fields["mesh_tags"]._props.get("disable") is not True
            assert fields["mesh"].value == ["g1"]
            assert fields["mesh_tags"].value == ["tag-a"]
            fields["mesh_route_push_disabled"].set_value(True)
            assert fields["mesh"]._props.get("disable") is True
            assert fields["mesh_tags"]._props.get("disable") is True

    asyncio.run(verify())


def test_peer_create_and_edit_use_shared_forms_without_echoing_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = PeersPage()
    monkeypatch.setattr(page, "_peer_bind_select_usernames", lambda **_kwargs: ["peer-gz"])
    monkeypatch.setattr(page, "_group_id_to_name", lambda: {"group-dev": "开发组"})

    async def verify_create() -> None:
        async with user_simulation(page._show_create_dialog) as user:
            await user.open("/")
            await user.should_see("新建对端站点")
            await user.should_see("搜索可用用户")
            await user.should_see("不向其他 VPN 客户端推送本站点路由")
            await user.should_see("SSH 认证")
            await user.should_see("创建")

    asyncio.run(verify_create())

    secret = "TOP-SECRET-PASSWORD"
    row = _peer(
        ssh_password=secret,
        ssh_auth="password",
        auto_install_on_peer=False,
    )

    async def verify_edit() -> None:
        async with user_simulation(lambda: page._show_edit_dialog(row)) as user:
            await user.open("/")
            await user.should_see("编辑对端站点")
            await user.should_see("不向其他 VPN 客户端推送本站点路由")
            await user.should_see("当前已保存 SSH 密码")
            await user.should_not_see(secret)

    asyncio.run(verify_edit())



def test_peer_reuse_mode_pushes_firewall_without_inspecting_openvpn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = PeersPage()
    calls: list[tuple[str, bool]] = []

    class ReusePeerService:
        def probe_openvpn_via_ssh(self, _peer_id: str):
            raise AssertionError("复用模式不应探测 OpenVPN 版本")

        def fetch_peer_client_service_status_via_ssh(self, _peer_id: str):
            raise AssertionError("复用模式不应检查客户端 service 或配置文件")

        def deploy_peer_site_firewall_via_ssh(
            self, peer_id: str, *, masquerade_on_peer: bool,
        ):
            calls.append((peer_id, masquerade_on_peer))
            return {"already_current": False, "masquerade_rules": 1}

    page.peer_service = ReusePeerService()
    monkeypatch.setattr(
        "app.ui.pages.peers.peer_row_has_usable_ssh_auth",
        lambda _row: True,
    )

    async def fake_io_bound(callback, *args, **kwargs):
        return callback(*args, **kwargs)

    monkeypatch.setattr("app.ui.pages.peers.run.io_bound", fake_io_bound)

    async def verify() -> None:
        async with user_simulation(
            lambda: page._show_peer_config_push_dialog(
                _peer(masquerade_on_peer=True)
            )
        ) as user:
            await user.open("/")
            user.find("对端已安装 OpenVPN（复用现有环境）").click()
            user.find("开始推送").click()
            await user.should_see("推送完成")
            await user.should_see("复用现有 OpenVPN 环境")
            await user.should_see("已下发")

    asyncio.run(verify())
    assert calls == [("peer-1234567890", True)]

def test_peer_remote_dialogs_validate_credentials_and_build_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = PeersPage()
    messages: list[str] = []
    monkeypatch.setattr(
        "app.ui.pages.peers.ui.notify",
        lambda message, **_kwargs: messages.append(str(message)),
    )

    missing = _peer(ssh_host="", ssh_password="")
    page._show_peer_config_push_dialog(missing)
    page._show_peer_openvpn_setup_dialog(missing)
    assert messages == [
        "请先填写 SSH 主机与用户名",
        "请先填写 SSH 主机与用户名",
    ]

    monkeypatch.setattr(
        "app.ui.pages.peers.peer_row_has_usable_ssh_auth",
        lambda _row: True,
    )
    available = _peer()

    async def verify_push() -> None:
        async with user_simulation(lambda: page._show_peer_config_push_dialog(available)) as user:
            await user.open("/")
            await user.should_see("对端配置推送")
            await user.should_see("对端已安装 OpenVPN")
            await user.should_see("推送客户端配置")
            await user.should_see("推送并启用 systemd")
            await user.should_see("推送对端站点防火墙")
            await user.should_see("开始推送")

    asyncio.run(verify_push())

    async def verify_install() -> None:
        async with user_simulation(lambda: page._show_peer_openvpn_setup_dialog(available)) as user:
            await user.open("/")
            await user.should_see("OpenVPN 客户端一键装配")
            await user.should_see("开始装配")
            await user.should_see("详单见 data/logs/peer-remote.log")

    asyncio.run(verify_install())


def test_peer_remote_log_view_reads_tail_and_registers_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    page = PeersPage()
    log_file = tmp_path / "peer-remote.log"
    log_file.write_text("line-1\nline-2\nline-3\n", encoding="utf-8")
    monkeypatch.setattr("app.ui.pages.peers.LOGS_DIR", tmp_path)

    async def verify_log() -> None:
        async with user_simulation(lambda: page._show_peer_remote_log_dialog(_peer())) as user:
            await user.open("/")
            await user.should_see("对端远程日志")
            await user.should_see("line-3")
            await user.should_see("实时")
            await user.should_see("刷新")
            await user.should_see("关闭")

    asyncio.run(verify_log())

    import inspect

    source = inspect.getsource(PeersPage._show_peer_remote_log_dialog)
    assert 'dialog.on("hide", on_hide)' in source
    assert "timer.deactivate()" in source


def test_peer_manual_page_uses_shared_shell_and_exposes_downloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = PeersPage()
    row = _peer()

    class ManualService:
        def get(self, peer_id: str):
            assert peer_id == row["id"]
            return dict(row)

        def export_peer_manual_markdown(self, peer_id: str):
            return "# 部署说明"

        def export_peer_manual_context(self, peer_id: str):
            return {
                "overview": {
                    "peer_id": peer_id,
                    "peer_name": row["name"],
                    "bound_username": row["bound_username"],
                    "global_subnet": "10.8.0.0/16",
                    "client_config_path": "/etc/openvpn/client/client.conf",
                    "client_log_path": "/var/log/openvpn-client.log",
                    "lan_cidrs": row["lan_cidrs"],
                },
                "highlights": ["先验证 SSH，再执行部署"],
                "commands": ["systemctl status openvpn-client@client"],
                "steps": [
                    {
                        "title": "检查服务",
                        "summary": "确认客户端服务状态",
                        "command": "systemctl status openvpn-client@client",
                    }
                ],
            }

    page.peer_service = ManualService()

    async def verify_manual() -> None:
        async with user_simulation(lambda: page.render_manual_page(row["id"])) as user:
            await user.open("/")
            await user.should_see("广州站点")
            await user.should_see("下载配置")
            await user.should_see("下载 Markdown")
            await user.should_see("检查服务")
            await user.should_see("systemctl status openvpn-client@client")

    asyncio.run(verify_manual())


def test_peer_manual_missing_peer_uses_empty_state() -> None:
    page = PeersPage()

    class MissingService:
        def get(self, _peer_id: str):
            return None

    page.peer_service = MissingService()

    async def verify() -> None:
        async with user_simulation(lambda: page.render_manual_page("missing")) as user:
            await user.open("/")
            await user.should_see("对端部署说明不可用")
            await user.should_see("返回对端站点")

    asyncio.run(verify())


def test_download_bound_user_ovpn_uses_existing_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    page = PeersPage()
    profile = tmp_path / "peer-gz.ovpn"
    profile.write_bytes(b"client\nremote vpn.example 1194\n")
    downloads: list[tuple[bytes, str]] = []

    monkeypatch.setattr("app.ui.pages.peers.OVPN_PROFILES_DIR", tmp_path)
    monkeypatch.setattr(
        "app.ui.pages.peers.ui.download",
        lambda data, filename: downloads.append((data, filename)),
    )

    page._download_bound_user_ovpn(_peer())

    assert downloads == [(profile.read_bytes(), "peer-gz.ovpn")]

def test_peer_help_keeps_user_and_firewall_shortcuts() -> None:
    page = PeersPage()

    async def verify() -> None:
        async with user_simulation(page._show_peers_help_dialog) as user:
            await user.open("/")
            await user.should_see("绑定与运维说明")
            await user.should_see("用户管理")
            await user.should_see("防火墙")

    asyncio.run(verify())

def test_peer_manual_expansion_and_command_styles_fill_available_width() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    css = (root / "app" / "ui" / "static" / "css" / "legacy.css").read_text(
        encoding="utf-8"
    )
    assert ".peer-manual-step-card .q-expansion-item__content" in css
    assert "align-items: stretch;" in css
    assert ".peer-manual-step-card .peer-manual-command" in css
    assert "width: 100%;" in css
    assert "overflow-x: auto;" in css
