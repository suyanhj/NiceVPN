# -*- coding: utf-8 -*-
"""VPN 管理系统主题注册入口。"""

from hashlib import sha256
from pathlib import Path

from nicegui import ui


_CSS_FILES = (
    "tokens.css",
    "shell.css",
    "ag-grid-theme.css",
    "effects.css",
    "legacy.css",
)
_JS_FILES = (
    "/static/vendor/clipboard-2.0.11.min.js",
    "/static/js/clipboard-adapter.js",
    "/static/js/ag-grid-set-filter.js",
)
_STATIC_ROOT = Path(__file__).resolve().parent / "static"


def _versioned_static_url(url: str) -> str:
    """为本地静态资源附加内容哈希，避免浏览器继续使用旧 CSS/JS。"""

    normalized = str(url).strip()
    relative = normalized.removeprefix("/static/")
    path = _STATIC_ROOT / relative
    if not path.is_file():
        return normalized

    digest = sha256(path.read_bytes()).hexdigest()[:12]
    separator = "&" if "?" in normalized else "?"
    return f"{normalized}{separator}v={digest}"


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
        url = _versioned_static_url(f"/static/css/{css_file}")
        ui.add_head_html(f'<link rel="stylesheet" href="{url}">')
    for script_file in _JS_FILES:
        url = _versioned_static_url(script_file)
        ui.add_head_html(f'<script defer src="{url}"></script>')
