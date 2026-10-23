# -*- coding: utf-8 -*-
"""验证浏览器可见的静态资源边界。"""

from pathlib import Path
import re


UI_DIR = Path(__file__).resolve().parents[2] / "app" / "ui"
STATIC_DIR = UI_DIR / "static"


def test_static_resource_directories_are_explicit() -> None:
    """静态资源按自有样式、薄适配和第三方依赖分区。"""

    assert {"css", "js", "vendor"} <= {path.name for path in STATIC_DIR.iterdir() if path.is_dir()}


def test_static_resources_do_not_expose_sensitive_files() -> None:
    """浏览器可见目录不得混入配置、凭据、证书或运行日志。"""

    blocked_suffixes = {".json", ".key", ".pem", ".crt", ".csr", ".ovpn", ".log"}
    blocked_names = {"config", "password", "secret", "credential", "private_key"}

    for path in STATIC_DIR.rglob("*"):
        if not path.is_file():
            continue
        assert path.suffix.lower() not in blocked_suffixes, path
        lowered = path.name.lower()
        assert not any(name in lowered for name in blocked_names), path


def test_firewall_uses_aggrid_row_drag_without_duplicate_sortable_bundle() -> None:
    """中心与对端规则排序统一使用 AG Grid row drag，不再保留 SortableJS 路径。"""

    source = (UI_DIR / "pages" / "firewall.py").read_text(encoding="utf-8")
    assert '"rowDrag": bool(can_reorder)' in source
    assert '"rowDragManaged"' in source
    assert ".make_sortable(" not in source
    assert "new Sortable(" not in source
    assert not (STATIC_DIR / "Sortable.min.js").exists()


def test_page_modules_do_not_duplicate_clipboard_scripts() -> None:
    """页面只能调用共享剪贴板入口，不得散落浏览器复制脚本。"""

    page_sources = {
        path.name: path.read_text(encoding="utf-8")
        for path in (UI_DIR / "pages").glob("*.py")
    }
    assert "copy_text_to_clipboard" in page_sources["firewall.py"]
    for name, source in page_sources.items():
        assert "navigator.clipboard" not in source, name
        assert "document.execCommand" not in source, name


def test_services_uses_nicegui_scroll_area_without_inline_scroll_script() -> None:
    """日志跟随滚动只使用 NiceGUI 滚动区事件与方法。"""

    source = (UI_DIR / "pages" / "services.py").read_text(encoding="utf-8")
    assert "ui.scroll_area(" in source
    assert ".scroll_to(percent=1.0)" in source
    assert "ui.run_javascript" not in source
    assert "document.addEventListener" not in source
    assert "requestAnimationFrame" not in source


def test_focus_behavior_uses_quasar_and_nicegui_methods() -> None:
    """登录与手动复制焦点由 Quasar 属性和组件方法管理。"""

    auth_source = (UI_DIR / "auth.py").read_text(encoding="utf-8")
    clipboard_source = (UI_DIR / "copy_clipboard.py").read_text(encoding="utf-8")
    assert "autofocus" in auth_source
    assert 'run_method("focus")' in auth_source
    assert 'run_method("focus")' in clipboard_source
    assert 'run_method("select")' in clipboard_source
    assert "document.querySelector" not in auth_source + clipboard_source


def test_design_tokens_are_complete_and_unique() -> None:
    """设计令牌覆盖约定类别，且同一变量只定义一次。"""

    source = (STATIC_DIR / "css" / "tokens.css").read_text(encoding="utf-8")
    names = re.findall(r"^\s*(--vpn-[\w-]+)\s*:", source, flags=re.MULTILINE)
    assert len(names) == len(set(names))

    required = {
        "--vpn-color-brand",
        "--vpn-color-success",
        "--vpn-color-warning",
        "--vpn-color-danger",
        "--vpn-bg",
        "--vpn-panel",
        "--vpn-text",
        "--vpn-space-4",
        "--vpn-radius-lg",
        "--vpn-shadow",
        "--vpn-font-mono",
    }
    assert required <= set(names)

    theme_source = (UI_DIR / "theme.py").read_text(encoding="utf-8")
    assert ":root {" not in theme_source
    assert '"tokens.css"' in theme_source
    assert "<style>" not in theme_source


def test_shell_and_effect_styles_load_after_tokens() -> None:
    """壳层与效果样式独立存在，并按令牌、布局、效果顺序加载。"""

    theme_source = (UI_DIR / "theme.py").read_text(encoding="utf-8")
    token_index = theme_source.index('"tokens.css"')
    shell_index = theme_source.index('"shell.css"')
    grid_index = theme_source.index('"ag-grid-theme.css"')
    effects_index = theme_source.index('"effects.css"')
    legacy_index = theme_source.index('"legacy.css"')
    assert token_index < shell_index < grid_index < effects_index < legacy_index

    shell_source = (STATIC_DIR / "css" / "shell.css").read_text(encoding="utf-8")
    grid_source = (STATIC_DIR / "css" / "ag-grid-theme.css").read_text(encoding="utf-8")
    effects_source = (STATIC_DIR / "css" / "effects.css").read_text(encoding="utf-8")
    assert ".vpn-shell" in shell_source
    assert "@media (max-width: 767px)" in shell_source
    assert ".vpn-data-grid__grid" in grid_source
    assert "--ag-background-color" in grid_source
    assert ".vpn-grid-status-pill" in grid_source
    assert ".vpn-grid-inline-action" in grid_source
    assert ".vpn-tech-grid" in effects_source
    assert "prefers-reduced-motion" in effects_source


def test_theme_fingerprints_static_assets() -> None:
    """CSS/JS 使用内容哈希版本，避免 pull+重启后浏览器继续命中旧缓存。"""

    from app.ui.theme import _versioned_static_url

    css_url = _versioned_static_url("/static/css/shell.css")
    js_url = _versioned_static_url("/static/js/clipboard-adapter.js")

    assert css_url.startswith("/static/css/shell.css?v=")
    assert js_url.startswith("/static/js/clipboard-adapter.js?v=")
    assert len(css_url.rsplit("=", 1)[1]) == 12
    assert len(js_url.rsplit("=", 1)[1]) == 12


def test_ag_grid_theme_is_dedicated_and_uses_design_tokens() -> None:
    """AG Grid 品牌主题独立维护，不重新散落为页面私有 CSS。"""

    source = (STATIC_DIR / "css" / "ag-grid-theme.css").read_text(encoding="utf-8")

    assert "AG Grid Theme" in source
    assert "--ag-background-color" in source
    assert "--ag-selected-row-background-color" in source
    assert "--ag-wrapper-border-radius" in source
    assert "var(--vpn-color-brand)" in source
    assert "var(--vpn-font-sans)" in source
    assert ".vpn-grid-status-pill" in source
    assert ".vpn-grid-stacked-value" in source
    assert ".vpn-grid-inline-action" in source
