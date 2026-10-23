# -*- coding: utf-8 -*-
"""真实 Chrome：桌面/窄屏、ECharts、菜单和表单 smoke。"""

from __future__ import annotations

import pytest
from nicegui.testing import Screen
from selenium.webdriver.common.by import By


pytestmark = pytest.mark.nondestructive


ROUTES = (
    ("/", "仪表盘"),
    ("/users", "用户管理"),
    ("/groups", "组管理"),
    ("/peers", "对端站点"),
    ("/peers/manual", "对端部署说明"),
    ("/firewall", "防火墙规则"),
    ("/certs", "证书管理"),
    ("/services", "服务管理"),
    ("/settings", "系统设置"),
    ("/init", "安全接入平台初始化"),
)


def _click_q_tab(screen: Screen, label: str) -> None:
    """精确点击 Quasar Tab，避免 Screen.click 的子串匹配命中说明文字。"""

    tabs = screen.selenium.find_elements(By.CSS_SELECTOR, ".q-tab")
    target = next(
        (
            tab for tab in tabs
            if tab.is_displayed() and tab.text.strip() == label
        ),
        None,
    )
    assert target is not None, f"missing visible q-tab: {label}"
    target.click()
    screen.wait(0.2)


def _assert_no_outer_horizontal_overflow(screen: Screen) -> None:
    result = screen.selenium.execute_script(
        """
        const root = document.documentElement;
        const body = document.body;
        return {
          inner: window.innerWidth,
          root: root.scrollWidth,
          body: body ? body.scrollWidth : 0,
        };
        """
    )
    widest = max(int(result["root"]), int(result["body"]))
    assert widest <= int(result["inner"]) + 2, result


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
def test_all_primary_routes_have_no_outer_horizontal_overflow(
    screen: Screen,
    width: int,
    height: int,
) -> None:
    screen.selenium.set_window_size(width, height)

    for route, title in ROUTES:
        screen.open(route)
        screen.should_contain(title)
        screen.wait(0.2)
        duplicate_headers = screen.selenium.execute_script(
            "return document.querySelectorAll('.vpn-page-header').length"
        )
        assert int(duplicate_headers) == 0
        _assert_no_outer_horizontal_overflow(screen)


def test_certificate_status_set_filter_opens_and_searches_values(
    screen: Screen,
) -> None:
    """Community 自定义 Set Filter 能从状态列表中搜索并多选。"""

    screen.selenium.set_window_size(1280, 900)
    screen.open("/certs")
    screen.should_contain("证书管理")
    screen.wait(0.4)

    grid_geometry = screen.selenium.execute_script(
        """
        const grid = document.querySelector('.certs-page--v2 .vpn-data-grid__grid');
        if (!grid) return null;
        const rect = grid.getBoundingClientRect();
        return {
          height: rect.height,
          bottomGap: window.innerHeight - rect.bottom,
        };
        """
    )
    assert grid_geometry is not None
    # 高度下限只防止 Grid 被意外压扁；真正的回归目标是分页栏贴近视口底部，
    # 不再用某个固定像素高度绑定页面上方组件数量。
    assert float(grid_geometry["height"]) >= 360
    assert 0 <= float(grid_geometry["bottomGap"]) <= 48

    header = screen.selenium.find_element(
        By.CSS_SELECTOR,
        '.ag-header-cell[col-id="status_label"]',
    )
    filter_button = header.find_element(
        By.CSS_SELECTOR,
        ".ag-header-cell-filter-button",
    )
    screen.selenium.execute_script("arguments[0].click()", filter_button)
    screen.wait(0.2)

    popup = screen.selenium.find_element(By.CSS_SELECTOR, ".vpn-grid-set-filter")
    assert popup.is_displayed()
    assert "(全选)" in popup.text
    assert "有效" in popup.text
    assert "即将到期" in popup.text

    search = popup.find_element(By.CSS_SELECTOR, 'input[type="search"]')
    search.send_keys("即将")
    screen.wait(0.1)

    assert "即将到期" in popup.text
    assert "有效" not in popup.text


def test_dashboard_echarts_renders_in_real_browser(screen: Screen) -> None:
    screen.selenium.set_window_size(1440, 900)
    screen.open("/")
    screen.should_contain("服务健康度")
    screen.should_contain("当前累计流量分布")
    canvas_count = screen.selenium.execute_script(
        "return document.querySelectorAll('canvas').length"
    )
    assert int(canvas_count) >= 2

    v2_cards = screen.selenium.execute_script(
        "return document.querySelectorAll('.vpn-metric-card--v2').length"
    )
    spotlight_hosts = screen.selenium.execute_script(
        "return document.querySelectorAll('.vpn-v2-spotlight-host').length"
    )
    assert int(v2_cards) == 3
    assert int(spotlight_hosts) == 3


def test_high_value_dialogs_tabs_and_more_menus_are_reachable(
    screen: Screen,
) -> None:
    screen.selenium.set_window_size(1280, 900)

    screen.open("/users")
    screen.click("新建用户")
    screen.should_contain("创建数量")
    screen.should_contain("所属组")

    screen.open("/peers")
    screen.click("新建对端")
    screen.should_contain("新建对端站点")
    screen.should_contain("SSH 认证")

    screen.open("/firewall")
    screen.click("更多")
    screen.should_contain("导入")
    screen.should_contain("导出")
    screen.click("对端远端")
    screen.should_contain("从对端拉取")

    screen.open("/certs")
    screen.click("高级操作")
    screen.should_contain("更新 CRL")
    screen.should_contain("重建 CA")

    screen.open("/services")
    screen.click("对端实例")
    screen.should_contain("对端实例")

    screen.open("/settings")
    for tab, expected in (
        ("服务端", "服务端连接配置"),
        ("设备绑定", "设备绑定策略"),
        ("SSH", "全局默认私钥"),
        ("下载配置", "GitHub 下载代理"),
        ("通知", "通知推送"),
    ):
        _click_q_tab(screen, tab)
        screen.should_contain(expected)

    screen.open("/init")
    screen.click("指定已安装路径")
    screen.should_contain("校验自定义执行路径")
    screen.click("返回上一步")
    screen.should_contain("自动安装 OpenVPN")
