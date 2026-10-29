# -*- coding: utf-8 -*-
"""OpenVPN 可视化管理系统应用入口。"""

import logging
import os
import sys
from pathlib import Path

from fastapi import Request
from nicegui import app, ui

logger = logging.getLogger(__name__)

from app.core.config import load_config
from app.core.constants import DEFAULT_HOST, DEFAULT_PORT
from app.core.scheduler import start_scheduler
from app.api.download import router as download_router
from app.api.vpn_ops import router as vpn_ops_router
from app.cli.entry import main as cli_main
from app.ui.auth import register_admin_auth
from app.ui.components.app_shell import render_app_shell
from app.ui.pages.certs import CertsPage
from app.ui.pages.dashboard import DashboardPage
from app.ui.pages.firewall import FirewallPage
from app.ui.pages.groups import GroupsPage
from app.ui.pages.init_page import InitPage
from app.ui.pages.peers import PeersPage
from app.ui.pages.services import ServicesPage
from app.ui.pages.settings import SettingsPage
from app.ui.pages.users import UsersPage
from app.ui.theme import setup_theme
from app.storage import ensure_storage_ready
from app.utils.api_basic_credentials import ensure_api_basic_credentials_file
from app.utils.listen_lan import set_listen_http_base
from app.utils.logging_setup import setup_logging
from app.utils.session_secret import load_or_create_session_secret
from app.utils.shutdown import request_shutdown

def init():
    """应用初始化：挂载静态资源、下载路由、注册页面和定时任务。"""
    app.add_static_files("/static", Path(__file__).parent / "app" / "ui" / "static")

    config = load_config()
    ensure_storage_ready()

    app.include_router(download_router)
    app.include_router(vpn_ops_router, prefix="/api")

    if config.initialized:
        ensure_api_basic_credentials_file()

    auth_enabled = bool(config.admin_username and config.admin_password_hash)
    if auth_enabled:
        register_admin_auth(config)
    _register_pages(auth_enabled=auth_enabled)
    _register_scheduled_tasks(config)
    return config


def _render_root_page(*, auth_enabled: bool = False) -> None:
    """根据当前初始化状态渲染首页。"""
    config = load_config()
    setup_theme()

    if not config.initialized:
        InitPage().render()
        return

    render_app_shell("仪表盘", "/", auth_enabled=auth_enabled)
    DashboardPage().render()


def _ensure_initialized() -> bool:
    """确保系统已经初始化，未初始化时跳转回引导页。"""
    config = load_config()
    if config.initialized:
        return True

    ui.navigate.to("/")
    return False


def _register_pages(*, auth_enabled: bool = False) -> None:
    """注册所有功能页面路由。"""

    @ui.page("/")
    def dashboard_page():
        _render_root_page(auth_enabled=auth_enabled)

    @ui.page("/users")
    def users_page():
        if not _ensure_initialized():
            return
        render_app_shell("用户管理", "/users", auth_enabled=auth_enabled)
        UsersPage().render()

    @ui.page("/groups")
    def groups_page():
        if not _ensure_initialized():
            return
        render_app_shell("组管理", "/groups", auth_enabled=auth_enabled)
        GroupsPage().render()

    @ui.page("/peers")
    def peers_page():
        if not _ensure_initialized():
            return
        render_app_shell("对端站点", "/peers", auth_enabled=auth_enabled)
        PeersPage().render()

    @ui.page("/peers/manual")
    def peer_manual_page(request: Request):
        if not _ensure_initialized():
            return
        render_app_shell("对端部署说明", "/peers/manual", auth_enabled=auth_enabled)
        peer_id = str(request.query_params.get("peer") or "")
        PeersPage().render_manual_page(peer_id)

    @ui.page("/firewall")
    def firewall_page():
        if not _ensure_initialized():
            return
        render_app_shell("防火墙规则", "/firewall", auth_enabled=auth_enabled)
        FirewallPage().render()

    @ui.page("/certs")
    def certs_page():
        if not _ensure_initialized():
            return
        render_app_shell("证书管理", "/certs", auth_enabled=auth_enabled)
        CertsPage().render()

    @ui.page("/services")
    def services_page(request: Request):
        if not _ensure_initialized():
            return
        render_app_shell("服务管理", "/services", auth_enabled=auth_enabled)
        tab = str(request.query_params.get("tab") or "local")
        peer_id = str(request.query_params.get("peer") or "")
        ServicesPage().render(initial_tab=tab, focus_peer_id=peer_id)

    @ui.page("/settings")
    def settings_page():
        if not _ensure_initialized():
            return
        render_app_shell("系统设置", "/settings", auth_enabled=auth_enabled)
        SettingsPage().render()


def _register_scheduled_tasks(config) -> None:
    """注册定时任务。"""
    start_scheduler()


def run_web() -> None:
    """启动 Web UI。"""
    setup_logging()
    config = init()
    set_listen_http_base("http", DEFAULT_HOST, DEFAULT_PORT)
    auth_enabled = bool(config.get("admin_username") and config.get("admin_password_hash"))
    storage_secret = load_or_create_session_secret() if auth_enabled else None
    session_middleware_kwargs = (
        {
            "max_age": int(config.admin_session_lifetime_minutes) * 60,
            "same_site": "lax",
            "https_only": False,
        }
        if auth_enabled
        else None
    )

    try:
        ui.run(
            host=DEFAULT_HOST,
            port=DEFAULT_PORT,
            title="OpenVPN 管理系统",
            favicon="🔐",
            dark=None,
            reload=False,
            storage_secret=storage_secret,
            session_middleware_kwargs=session_middleware_kwargs,
            reconnect_timeout=float(
                config.get("admin_reconnect_timeout_seconds", 300) or 300
            ),
        )
    except KeyboardInterrupt:
        # Ctrl+C 时 asyncio/uvicorn 会先 CancelledError 再 KeyboardInterrupt，属正常停机路径
        request_shutdown()
        logger.info("收到键盘中断，Web 服务已停止")
        logging.shutdown()
        os._exit(0)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        raise SystemExit(cli_main(sys.argv[2:]))
    run_web()
