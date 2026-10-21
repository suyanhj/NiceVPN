# -*- coding: utf-8 -*-
"""验证统一页面标题组件。"""

import asyncio

import pytest
from nicegui import ui
from nicegui.testing import user_simulation

from app.ui.components.page_header import BreadcrumbItem, render_page_header


def _render_and_open(builder) -> dict[str, object]:
    """渲染页头并用独立测试客户端打开。"""

    state: dict[str, object] = {}

    def page() -> None:
        state["header"] = builder()

    async def verify_page() -> None:
        async with user_simulation(page) as user:
            response = await user.open("/")
            assert response.status_code == 200

    asyncio.run(verify_page())
    return state


def test_page_header_without_optional_content_keeps_simple_layout() -> None:
    """没有副标题、面包屑和操作时不生成空占位容器。"""

    header = _render_and_open(lambda: render_page_header("用户管理"))["header"]
    assert header.title.text == "用户管理"
    assert header.description is None
    assert header.breadcrumbs is None
    assert header.actions is None
    assert "vpn-page-header" in header.container._classes


def test_page_header_with_breadcrumbs_and_actions_ignores_description() -> None:
    """旧 description 参数保持兼容，但页面不再渲染副标题。"""

    def actions() -> None:
        ui.button("刷新", icon="refresh")
        ui.button("新建用户", icon="person_add")
        ui.button("更多", icon="more_horiz")

    header = _render_and_open(
        lambda: render_page_header(
            "很长的对端部署说明标题用于验证布局不会挤压页面主要操作",
            description="展示当前对端元数据、部署步骤、命令复制和配置下载入口。",
            breadcrumbs=(
                BreadcrumbItem("对端站点", "/peers"),
                BreadcrumbItem("部署说明"),
            ),
            actions=actions,
        )
    )["header"]
    assert header.description is None
    assert header.breadcrumbs is not None
    assert header.actions is not None
    assert len(header.actions.default_slot.children) == 3


def test_page_header_rejects_empty_title() -> None:
    """缺失主标题时尽早报错。"""

    with pytest.raises(ValueError, match="页面标题不能为空"):
        render_page_header("  ")
