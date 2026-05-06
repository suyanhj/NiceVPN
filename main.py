# -*- coding: utf-8 -*-
"""OpenVPN 可视化管理系统应用入口。"""

import logging
import os
import sys

from fastapi import Request
from nicegui import app, ui

logger = logging.getLogger(__name__)

from app.core.config import load_config
from app.core.constants import DEFAULT_HOST, DEFAULT_PORT
from app.ui.theme import setup_theme
from app.utils.listen_lan import set_listen_http_base
from app.utils.logging_setup import setup_logging

_NAV_ITEMS = [
    ("仪表盘", "dashboard", "/"),
    ("用户管理", "people", "/users"),
    ("组管理", "folder_shared", "/groups"),
    ("对端站点", "hub", "/peers"),
    ("防火墙规则", "shield", "/firewall"),
    ("证书管理", "verified_user", "/certs"),
    ("服务管理", "dns", "/services"),
    ("系统设置", "settings", "/settings"),
]


def _nav_layout(page_title: str, current_path: str) -> None:
    """渲染统一导航布局。"""
    setup_theme()

    with ui.left_drawer().props("width=248 bordered").classes("text-white") as drawer:
        with ui.column().classes("w-full q-pa-lg"):
            ui.label("OpenVPN").classes("vpn-nav-title")
            ui.label("Operations Console").classes("vpn-nav-subtitle")
            ui.label("Navigation").classes("vpn-nav-group-label")

            with ui.column().classes("w-full gap-sm"):
                for label, icon, path in _NAV_ITEMS:
                    classes = "vpn-nav-button"
                    if current_path == path:
                        classes += " is-active"
                    ui.button(
                        label,
                        icon=icon,
                        on_click=lambda target=path: ui.navigate.to(target),
                    ).props("flat no-caps align=left").classes(classes)

    ui.query(".nicegui-content").classes(add="vpn-shell")


def init() -> None:
    """应用初始化：挂载静态资源、下载路由、注册页面和定时任务。"""
    from pathlib import Path
    app.add_static_files("/static", Path(__file__).parent / "app" / "ui" / "static")

    config = load_config()

    from app.api.download import router as download_router
    from app.api.vpn_ops import router as vpn_ops_router

    app.include_router(download_router)
    app.include_router(vpn_ops_router, prefix="/api")

    if config.initialized:
        from app.utils.api_basic_credentials import ensure_api_basic_credentials_file

        ensure_api_basic_credentials_file()

    _register_pages()
    _register_scheduled_tasks(config)


def _render_root_page() -> None:
    """根据当前初始化状态渲染首页。"""
    config = load_config()
    setup_theme()

    if not config.initialized:
        from app.ui.pages.init_page import InitPage
        InitPage().render()
        return

    _nav_layout("仪表盘", "/")
    from app.ui.pages.dashboard import DashboardPage
    DashboardPage().render()


def _ensure_initialized() -> bool:
    """确保系统已经初始化，未初始化时跳转回引导页。"""
    config = load_config()
    if config.initialized:
        return True

    ui.navigate.to("/")
    return False


def _register_pages() -> None:
    """注册所有功能页面路由。"""

    @ui.page("/")
    def dashboard_page():
        _render_root_page()

    @ui.page("/users")
    def users_page():
        if not _ensure_initialized():
            return
        _nav_layout("用户管理", "/users")
        from app.ui.pages.users import UsersPage
        UsersPage().render()

    @ui.page("/groups")
    def groups_page():
        if not _ensure_initialized():
            return
        _nav_layout("组管理", "/groups")
        from app.ui.pages.groups import GroupsPage
        GroupsPage().render()

    @ui.page("/peers")
    def peers_page():
        if not _ensure_initialized():
            return
        _nav_layout("对端站点", "/peers")
        from app.ui.pages.peers import PeersPage
        PeersPage().render()

    @ui.page("/peers/manual")
    def peer_manual_page(request: Request):
        if not _ensure_initialized():
            return
        _nav_layout("对端部署说明", "/peers")
        from app.ui.pages.peers import PeersPage
        peer_id = str(request.query_params.get("peer") or "")
        PeersPage().render_manual_page(peer_id)

    @ui.page("/firewall")
    def firewall_page():
        if not _ensure_initialized():
            return
        _nav_layout("防火墙规则", "/firewall")
        from app.ui.pages.firewall import FirewallPage
        FirewallPage().render()

    @ui.page("/certs")
    def certs_page():
        if not _ensure_initialized():
            return
        _nav_layout("证书管理", "/certs")
        from app.ui.pages.certs import CertsPage
        CertsPage().render()

    @ui.page("/services")
    def services_page(request: Request):
        if not _ensure_initialized():
            return
        _nav_layout("服务管理", "/services")
        from app.ui.pages.services import ServicesPage
        tab = str(request.query_params.get("tab") or "local")
        peer_id = str(request.query_params.get("peer") or "")
        ServicesPage().render(initial_tab=tab, focus_peer_id=peer_id)

    @ui.page("/settings")
    def settings_page():
        if not _ensure_initialized():
            return
        _nav_layout("系统设置", "/settings")
        from app.ui.pages.settings import SettingsPage
        SettingsPage().render()


def _register_scheduled_tasks(config) -> None:
    """注册定时任务。"""
    from app.core.scheduler import start_scheduler
    start_scheduler()


def run_web() -> None:
    """启动 Web UI。"""
    setup_logging()
    init()
    set_listen_http_base("http", DEFAULT_HOST, DEFAULT_PORT)

    try:
        ui.run(
            host=DEFAULT_HOST,
            port=DEFAULT_PORT,
            title="OpenVPN 管理系统",
            favicon="🔐",
            dark=None,
            reload=False,
        )
    except KeyboardInterrupt:
        # Ctrl+C 时 asyncio/uvicorn 会先 CancelledError 再 KeyboardInterrupt，属正常停机路径
        from app.utils.shutdown import request_shutdown

        request_shutdown()
        logger.info("收到键盘中断，Web 服务已停止")
        logging.shutdown()
        os._exit(0)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        from app.cli.entry import main as cli_main

        raise SystemExit(cli_main(sys.argv[2:]))
    run_web()
