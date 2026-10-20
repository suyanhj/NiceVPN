# -*- coding: utf-8 -*-
"""验证统一操作按钮和更多菜单。"""

import asyncio

import pytest
from nicegui.testing import user_simulation

from app.ui.components.actions import (
    MenuAction,
    danger_action,
    more_actions,
    primary_action,
    secondary_action,
)


def test_action_variants_support_text_icon_state_and_tooltip() -> None:
    """主操作、普通操作和危险操作可配置完整呈现状态。"""

    state: dict[str, object] = {}

    def page() -> None:
        state["primary"] = primary_action(
            "新建用户",
            icon="person_add",
            on_click=lambda: None,
            tooltip="创建一个 VPN 用户",
        )
        state["secondary"] = secondary_action(
            "刷新",
            icon="refresh",
            disabled=True,
        )
        state["danger"] = danger_action(
            "批量删除",
            icon="delete",
            busy=True,
        )

    async def verify_page() -> None:
        async with user_simulation(page) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("新建用户")
            await user.should_see("刷新")
            await user.should_see("批量删除")
            await user.should_see("创建一个 VPN 用户")

    asyncio.run(verify_page())

    primary = state["primary"]
    secondary = state["secondary"]
    danger = state["danger"]
    assert primary._props["icon"] == "person_add"
    assert primary._props["unelevated"] is True
    assert primary._event_listeners
    assert secondary._props["outline"] is True
    assert secondary._props["disable"] is True
    assert danger._props["color"] == "negative"
    assert danger._props["loading"] is True
    assert danger._props["disable"] is True


def test_more_menu_supports_icon_danger_disabled_and_tooltip() -> None:
    """更多菜单保留每个低频操作的图标、危险和禁用语义。"""

    state: dict[str, object] = {}

    def page() -> None:
        state["menu"] = more_actions(
            (
                MenuAction("下载配置", icon="download", on_click=lambda: None),
                MenuAction(
                    "删除",
                    icon="delete",
                    danger=True,
                    disabled=True,
                    tooltip="当前对象不可删除",
                ),
            ),
            tooltip="显示低频操作",
        )

    async def verify_page() -> None:
        async with user_simulation(page) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("更多")
            await user.should_see("当前对象不可删除")

    asyncio.run(verify_page())

    menu = state["menu"]
    assert len(menu.items) == 2
    assert menu.items[0]._event_listeners
    assert "text-negative" in menu.items[1]._classes
    assert menu.items[1]._props["disable"] is True


def test_action_components_reject_empty_labels() -> None:
    """缺失可读文案时尽早报错。"""

    with pytest.raises(ValueError, match="文案不能为空"):
        primary_action("  ")
