# -*- coding: utf-8 -*-
"""VPN 管理系统主题注册入口。"""

from nicegui import ui


_CSS_FILES = (
    "tokens.css",
    "shell.css",
    "effects.css",
    "legacy.css",
)
_JS_FILES = (
    "/static/vendor/clipboard-2.0.11.min.js",
    "/static/js/clipboard-adapter.js",
)


def _register_component_defaults() -> None:
    """注册轻量 Quasar 默认值；页面仍可按需要覆盖。"""

    ui.button.default_props("no-caps")
    ui.input.default_props("outlined dense")
    ui.select.default_props("outlined dense options-dense")
    ui.card.default_classes("vpn-card")
    ui.tabs.default_props("dense no-caps active-color=primary indicator-color=primary")
    ui.dialog.default_props("transition-show=fade transition-hide=fade")


def setup_theme() -> None:
    """注册颜色、深色模式、组件默认值和静态样式。"""

    ui.colors(
        primary="#4f8fc7",
        secondary="#2dd4bf",
        accent="#f59e0b",
        dark="#111c31",
        dark_page="#0f172a",
        positive="#22c55e",
        negative="#fb7185",
        info="#60a5fa",
        warning="#f59e0b",
    )
    ui.dark_mode(True)
    _register_component_defaults()
    for css_file in _CSS_FILES:
        ui.add_head_html(f'<link rel="stylesheet" href="/static/css/{css_file}">')
    for script_file in _JS_FILES:
        ui.add_head_html(f'<script defer src="{script_file}"></script>')
