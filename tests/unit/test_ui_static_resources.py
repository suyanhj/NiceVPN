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
    grid_source = (UI_DIR / "components" / "data_grid.py").read_text(encoding="utf-8")
    assert '"rowDrag": bool(can_reorder)' in source
    assert '":rowDrag"' not in source
    assert '"rowDragManaged"' in grid_source
    assert 'options[":onRowDragEnd"]' in grid_source
    assert "event.overIndex" in grid_source
    assert "firewall-footer-count" not in source
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
    legacy_index = theme_source.index('"legacy.css"')
    shell_index = theme_source.index('"shell.css"')
    grid_index = theme_source.index('"ag-grid-theme.css"')
    effects_index = theme_source.index('"effects.css"')
    setup_index = theme_source.index('"setup-v2.css"')
    assert token_index < legacy_index < shell_index < grid_index < effects_index < setup_index

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

    setup_source = (STATIC_DIR / "css" / "setup-v2.css").read_text(encoding="utf-8")
    assert ".page-shell--setup .setup-brand" in setup_source
    assert "background: #0c0c0d !important;" in setup_source
    assert "#15bde8" in setup_source

    legacy_source = (STATIC_DIR / "css" / "legacy.css").read_text(encoding="utf-8")
    for retired_selector in (
        ".group-page",
        ".group-header-panel",
        ".group-toolbar-btn",
        ".service-page",
        ".service-header-panel",
        ".service-icon-btn",
        ".settings-page",
        ".settings-header-panel",
        ".settings-toolbar",
        ".dashboard-grid",
        ".dashboard-mini-grid",
        ".user-record-card",
        ".user-toolbar-btn",
    ):
        assert retired_selector not in legacy_source
    assert ".group-list-count" in legacy_source
    assert ".group-record-side" in legacy_source
    assert ".settings-panel-title" in legacy_source
    assert ".settings-status-text" in legacy_source
    assert not (STATIC_DIR / "css" / "firewall-v2-fixes.css").exists()
    assert not (UI_DIR / "components" / "page_header.py").exists()
    assert "firewall-v2-fixes.css" not in theme_source
    assert ".vpn-data-grid__grid .ag-row:has(.ag-row-drag)" in grid_source


def test_theme_fingerprints_static_assets() -> None:
    """CSS/JS 使用内容哈希版本，避免 pull+重启后浏览器继续命中旧缓存。"""

    from app.ui.theme import _versioned_static_url

    css_url = _versioned_static_url("/static/css/shell.css")
    js_url = _versioned_static_url("/static/js/clipboard-adapter.js")
    filter_url = _versioned_static_url("/static/js/ag-grid-set-filter.js")
    setup_css_url = _versioned_static_url("/static/css/setup-v2.css")

    assert css_url.startswith("/static/css/shell.css?v=")
    assert js_url.startswith("/static/js/clipboard-adapter.js?v=")
    assert filter_url.startswith("/static/js/ag-grid-set-filter.js?v=")
    assert setup_css_url.startswith("/static/css/setup-v2.css?v=")
    assert len(css_url.rsplit("=", 1)[1]) == 12
    assert len(js_url.rsplit("=", 1)[1]) == 12
    assert len(filter_url.rsplit("=", 1)[1]) == 12


def test_ag_grid_theme_is_dedicated_and_matches_theme_builder_preset() -> None:
    """AG Grid 主题独立维护，并锁定 Theme Builder 导出的关键参数。"""

    source = (STATIC_DIR / "css" / "ag-grid-theme.css").read_text(encoding="utf-8")

    assert "AG Grid Theme" in source
    assert "--ag-background-color" in source
    assert "--ag-selected-row-background-color" in source
    assert "--ag-wrapper-border-radius" in source
    assert ".vpn-grid-status-pill" in source
    assert ".vpn-grid-stacked-value" in source
    assert ".vpn-grid-inline-action" in source
    assert "--ag-accent-color: #15bde8" in source
    assert "--ag-background-color: #0c0c0d" in source
    assert "--ag-spacing: 8px" in source
    assert "--ag-border-radius: 20px" in source
    assert "--ag-header-background-color: #182226" in source
    assert "--ag-header-font-weight: 500" in source
    assert "--ag-header-vertical-padding-scale: 0.9" in source
    assert "--ag-row-vertical-padding-scale: 1.2" in source
    assert "--ag-wrapper-border: none" in source
    assert "--ag-wrapper-border-radius: 0" in source


def test_ag_grid_community_set_filter_is_shared_and_safe() -> None:
    """Community Set Filter 作为共享静态组件加载，不包含业务 I/O。"""

    theme_source = (UI_DIR / "theme.py").read_text(encoding="utf-8")
    source = (STATIC_DIR / "js" / "ag-grid-set-filter.js").read_text(encoding="utf-8")

    assert '"/static/js/ag-grid-set-filter.js"' in theme_source
    assert "class NiceVpnSetFilter" in source
    assert "window.NiceVpnSetFilter" in source
    assert "forEachLeafNode" in source
    assert "filterChangedCallback" in source
    assert "vpn-grid-set-filter__search" in source
    assert "vpn-grid-set-filter__checkbox" in source
    assert "innerHTML" not in source
    assert "fetch(" not in source
    assert "axios" not in source
    assert "/api/" not in source
