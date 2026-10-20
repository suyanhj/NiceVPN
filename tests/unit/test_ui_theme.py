# -*- coding: utf-8 -*-
"""验证主题入口、Quasar 默认值和静态样式注册。"""

import asyncio

from nicegui import ui
from nicegui.testing import user_simulation

from app.ui.theme import setup_theme


def test_theme_registers_component_defaults_and_example_components() -> None:
    """按钮、输入、卡片、页签和对话框使用统一的安全默认值。"""

    state: dict[str, object] = {}

    def page() -> None:
        setup_theme()
        state["button"] = ui.button("主操作")
        state["input"] = ui.input("名称")
        state["card"] = ui.card()
        state["tabs"] = ui.tabs()
        state["dialog"] = ui.dialog()

    async def verify_page() -> None:
        async with user_simulation(page) as user:
            response = await user.open("/")
            assert response.status_code == 200
            html = response.head_html
            for name in ("tokens.css", "shell.css", "effects.css", "legacy.css"):
                assert f'/static/css/{name}' in html
            assert "/static/vendor/clipboard-2.0.11.min.js" in html
            assert "/static/js/clipboard-adapter.js" in html
            assert html.index("clipboard-2.0.11.min.js") < html.index("clipboard-adapter.js")

    asyncio.run(verify_page())

    button = state["button"]
    input_element = state["input"]
    card = state["card"]
    tabs = state["tabs"]
    dialog = state["dialog"]
    assert button._props["no-caps"] is True
    assert input_element._props["outlined"] is True
    assert input_element._props["dense"] is True
    assert "vpn-card" in card._classes
    assert tabs._props["dense"] is True
    assert tabs._props["active-color"] == "primary"
    assert dialog._props["transition-show"] == "fade"
