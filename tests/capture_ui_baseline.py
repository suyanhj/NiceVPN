# -*- coding: utf-8 -*-
"""启动只读的界面基线应用，并生成桌面与窄屏截图。"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_DIR = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_DIR))

from box import Box
from nicegui import app, ui

from app.services.firewall.rule_service import FirewallRuleService
from app.services.group.crud import GroupService
from app.services.monitor.service_monitor import InstanceStatus, ServiceMonitor
from app.services.peer_instance.service import PeerService
from app.services.user.crud import UserService
from app.services.user.device_bind import DeviceBindingService
from app.ui.pages.dashboard import DashboardPage
from app.ui.pages.firewall import FirewallPage
from app.ui.pages.services import ServicesPage
from app.ui.pages.settings import SettingsPage
from app.ui.pages.users import UsersPage
from app.ui.components.app_shell import render_app_shell


PORT = 8897
OUTPUT_DIR = REPOSITORY_DIR / "openspec" / "changes" / "vpn-admin-ui" / "evidence" / "baseline"
EDGE_PATH = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


SAMPLE_CONFIG = Box(
    {
        "instances": {
            "server": {"port": 1194, "proto": "udp", "subnet": "10.8.0.0/24"},
        },
        "openvpn_conf_dir": "Z:/path-not-present",
    },
    default_box=True,
    default_box_attr=None,
)


def _install_sample_data() -> None:
    """为截图进程安装只读示例数据，不触碰实际数据目录。"""

    users = [
        {
            "username": "alice",
            "group_id": "group-main",
            "status": "active",
            "created_at": "2026-10-20T08:00:00+00:00",
            "cert_serial": "0123456789ABCDEF",
        },
        {
            "username": "disabled-user",
            "group_id": "group-ops",
            "status": "disabled",
            "created_at": "2026-10-19T08:00:00+00:00",
            "cert_serial": "FEDCBA9876543210",
        },
    ]
    groups = [
        {
            "id": "group-main",
            "name": "默认接入组",
            "subnet": "10.8.0.0/24",
            "status": "active",
            "user_count": 1,
        },
        {
            "id": "group-ops",
            "name": "运维组",
            "subnet": "10.9.0.0/24",
            "status": "active",
            "user_count": 1,
        },
    ]
    rules = [
        {
            "id": "rule-001",
            "owner_id": "group-main",
            "source_subnet": "10.8.0.0/24",
            "dest_ip": "192.168.10.10",
            "dest_port": "443",
            "protocol": "tcp",
            "action": "accept",
            "enabled": True,
            "description": "允许访问内部控制台",
        },
        {
            "id": "rule-002",
            "owner_id": "group-ops",
            "source_subnet": "10.9.0.0/24",
            "dest_ip": "10.20.0.0/16",
            "dest_port": "*",
            "protocol": "all",
            "action": "accept",
            "enabled": False,
            "description": "维护窗口期间停用",
        },
    ]
    statuses = [
        InstanceStatus(
            name="server",
            active=True,
            client_count=3,
            bytes_received=18_874_368,
            bytes_sent=8_388_608,
        )
    ]

    UserService.list_all = lambda self: users
    UserService.list_ccd_virtual_ipv4_by_username = lambda self: {"alice": "10.8.0.10"}
    DeviceBindingService.build_user_binding_aux = lambda self: {}
    GroupService.list_all = lambda self: groups
    PeerService.list_all = lambda self: []
    FirewallRuleService.list_unified_flat = lambda self: rules
    ServiceMonitor.check_all_instances = lambda self, names: statuses

    import app.ui.pages.dashboard as dashboard_module
    import app.ui.pages.services as services_module

    dashboard_module.load_config = lambda: SAMPLE_CONFIG
    services_module.load_config = lambda: SAMPLE_CONFIG


def _register_pages() -> None:
    """注册截图所需的代表性页面。"""

    app.add_static_files("/static", PROJECT_DIR / "app" / "ui" / "static")

    @ui.page("/dashboard")
    def dashboard_page() -> None:
        render_app_shell("仪表盘", "/")
        DashboardPage().render()

    @ui.page("/users")
    def users_page() -> None:
        render_app_shell("用户管理", "/users")
        UsersPage().render()

    @ui.page("/firewall")
    def firewall_page() -> None:
        render_app_shell("防火墙规则", "/firewall")
        FirewallPage().render()

    @ui.page("/settings")
    def settings_page() -> None:
        render_app_shell("系统设置", "/settings")
        SettingsPage().render()

    @ui.page("/log")
    def log_page() -> None:
        render_app_shell("日志视图", "/services")
        page = ServicesPage()
        with ui.column().classes("page-shell mgmt-page"):
            with ui.element("section").classes("mgmt-panel"):
                ui.label("日志视图基线").classes("mgmt-title")
                ui.label("用于核对日志弹窗、等宽内容和操作区。 ").classes("mgmt-desc")
                ui.button(
                    "打开日志",
                    icon="article",
                    on_click=lambda: page._open_log_tail_dialog(
                        "server 实例日志",
                        lambda: (
                            "日志: /var/log/openvpn/server.log",
                            "2026-10-20 08:00:00 服务启动\n"
                            "2026-10-20 08:00:05 alice 已连接\n"
                            "2026-10-20 08:02:00 路由同步完成",
                        ),
                        precheck_file=False,
                    ),
                ).props("unelevated no-caps")


def serve() -> None:
    """运行本地截图服务。"""

    _install_sample_data()
    _register_pages()
    ui.run(host="127.0.0.1", port=PORT, title="VPN UI Baseline", reload=False, show=False)


def _wait_for_server(process: subprocess.Popen[str]) -> None:
    """等待截图服务就绪，并在提前退出时立即报告。"""

    url = f"http://127.0.0.1:{PORT}/dashboard"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"基线服务提前退出，退出码 {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError("等待基线服务启动超时")


def capture() -> None:
    """使用本机 Edge 生成视觉基线截图。"""

    if not EDGE_PATH.exists():
        raise FileNotFoundError(f"未找到 Edge: {EDGE_PATH}")

    from playwright.sync_api import sync_playwright

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--serve"],
        cwd=PROJECT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        _wait_for_server(process)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(EDGE_PATH), headless=True)
            desktop = browser.new_page(viewport={"width": 1440, "height": 900})
            narrow = browser.new_page(viewport={"width": 390, "height": 844})
            browser_errors: list[str] = []

            for css_file in ("tokens.css", "shell.css", "effects.css", "legacy.css"):
                response = desktop.context.request.get(
                    f"http://127.0.0.1:{PORT}/static/css/{css_file}"
                )
                assert response.ok, f"静态样式加载失败: {css_file} ({response.status})"
                assert "text/css" in response.headers.get("content-type", "")
            for script_file in (
                "vendor/clipboard-2.0.11.min.js",
                "js/clipboard-adapter.js",
            ):
                response = desktop.context.request.get(
                    f"http://127.0.0.1:{PORT}/static/{script_file}"
                )
                assert response.ok, f"静态脚本加载失败: {script_file} ({response.status})"
                assert "javascript" in response.headers.get("content-type", "")

            def collect_browser_errors(page, page_name: str) -> None:
                """收集页面控制台错误和未捕获异常，避免截图掩盖前端故障。"""

                page.on(
                    "console",
                    lambda message: browser_errors.append(
                        f"{page_name} console: {message.text}"
                    )
                    if message.type == "error"
                    else None,
                )
                page.on(
                    "pageerror",
                    lambda error: browser_errors.append(f"{page_name} pageerror: {error}"),
                )

            collect_browser_errors(desktop, "desktop")
            collect_browser_errors(narrow, "narrow")

            desktop.goto(f"http://127.0.0.1:{PORT}/dashboard", wait_until="networkidle")
            assert desktop.evaluate("typeof window.ClipboardJS") == "function"
            assert desktop.evaluate("typeof window.vpnClipboard.copy") == "function"
            assert desktop.evaluate(
                "async () => await window.vpnClipboard.copy('vpn-http-copy-check')"
            ) is True
            desktop.screenshot(path=OUTPUT_DIR / "dashboard-wide.png", full_page=True)

            desktop.goto(f"http://127.0.0.1:{PORT}/users", wait_until="networkidle")
            desktop.screenshot(path=OUTPUT_DIR / "users-wide.png", full_page=True)
            desktop.get_by_role("button", name="切换主导航").click()
            desktop.locator(".q-drawer").wait_for(state="hidden")
            desktop.screenshot(
                path=OUTPUT_DIR / "users-navigation-collapsed-wide.png",
                full_page=True,
            )
            desktop.get_by_role("button", name="切换主导航").click()
            desktop.locator(".q-drawer").wait_for(state="visible")
            desktop.get_by_role("button", name="新建用户").click()
            desktop.locator(".q-dialog").wait_for(state="visible")
            desktop.screenshot(path=OUTPUT_DIR / "users-dialog-wide.png", full_page=True)

            narrow.goto(f"http://127.0.0.1:{PORT}/firewall", wait_until="networkidle")
            narrow.screenshot(path=OUTPUT_DIR / "firewall-narrow.png", full_page=True)
            narrow.get_by_role("button", name="切换主导航").click()
            narrow.locator(".q-drawer").wait_for(state="visible")
            narrow.get_by_role("button", name="对端站点").wait_for(state="visible")
            narrow.wait_for_timeout(350)
            drawer_box = narrow.locator(".q-drawer").bounding_box()
            assert drawer_box is not None
            assert drawer_box["x"] <= 1
            assert drawer_box["width"] >= 240
            narrow.screenshot(
                path=OUTPUT_DIR / "firewall-navigation-narrow.png",
                full_page=True,
            )

            narrow.goto(f"http://127.0.0.1:{PORT}/settings", wait_until="networkidle")
            narrow.screenshot(path=OUTPUT_DIR / "settings-narrow.png", full_page=True)

            desktop.goto(f"http://127.0.0.1:{PORT}/log", wait_until="networkidle")
            desktop.get_by_role("button", name="打开日志").click()
            desktop.locator(".q-dialog").wait_for(state="visible")
            desktop.screenshot(path=OUTPUT_DIR / "log-dialog-wide.png", full_page=True)
            browser.close()
            if browser_errors:
                raise AssertionError("浏览器控制台存在错误：\n" + "\n".join(browser_errors))
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if process.stdout is not None:
            output = process.stdout.read().strip()
            if output:
                print(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    if args.serve:
        serve()
    else:
        capture()


if __name__ == "__main__":
    main()
