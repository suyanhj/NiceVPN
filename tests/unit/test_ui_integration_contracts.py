# -*- coding: utf-8 -*-
"""后台 UI 最终集成静态契约。"""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
UI_DIR = ROOT / "app" / "ui"
PAGES_DIR = UI_DIR / "pages"
CSS_DIR = UI_DIR / "static" / "css"


def _page_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(PAGES_DIR.glob("*.py"))
    )


def test_legacy_cleanup_has_backup_and_valid_css_structure() -> None:
    backup = ROOT / "backups" / "admin-ui" / "legacy.css.pre-integration.bak"
    legacy = CSS_DIR / "legacy.css"

    assert backup.is_file()
    backup_text = backup.read_text(encoding="utf-8")
    current = legacy.read_text(encoding="utf-8")

    assert len(backup_text) > len(current)
    assert current.count("{") == current.count("}")
    assert "\ufffd" not in current
    assert "\r\n" not in current


def test_removed_legacy_certificate_and_user_page_selectors_stay_removed() -> None:
    legacy = (CSS_DIR / "legacy.css").read_text(encoding="utf-8")

    assert ".cert-page" not in legacy
    assert ".cert-header-panel" not in legacy
    assert ".cert-record-list" not in legacy
    assert ".cert-actions" not in legacy

    assert ".user-page" not in legacy
    assert ".user-header-panel" not in legacy
    assert ".user-record-list" not in legacy

    # 少量 user-* 仍可能作为与 group/service 共用的响应式 selector
    # 留在兼容规则中；旧用户页主体必须保持移除。
    # 该徽标仍被共享视图引用，不能误删。
    assert ".user-session-badge" in legacy


def test_pages_do_not_reintroduce_handwritten_business_javascript() -> None:
    source = _page_source()

    assert "ui.run_javascript(" not in source
    assert "new Sortable(" not in source
    assert "<script" not in source
    assert "add_head_html(" not in source


def test_theme_is_the_only_static_style_and_script_registration_entry() -> None:
    theme = (UI_DIR / "theme.py").read_text(encoding="utf-8")
    source = _page_source()

    assert '_CSS_FILES = (' in theme
    for name in ("tokens.css", "shell.css", "effects.css", "legacy.css"):
        assert f'"{name}"' in theme
    assert "clipboard-adapter.js" in theme

    assert "add_head_html(" not in source


def test_page_sources_use_shared_admin_components() -> None:
    source = _page_source()

    # 至少覆盖统一帮助、工具栏、操作、状态、表单、表格/图表能力；
    # 内容区不再重复渲染页级大标题。
    expected_import_fragments = (
        "app.ui.components.help",
        "app.ui.components.toolbars",
        "app.ui.components.actions",
        "app.ui.components.states",
        "app.ui.components.form",
        "app.ui.components.data_grid",
        "app.ui.components.charts",
    )
    for fragment in expected_import_fragments:
        assert fragment in source

    assert "app.ui.components.page_header" not in source
    assert "render_page_header(" not in source


def test_legacy_css_does_not_contain_replacement_character_or_old_sortable() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(CSS_DIR.glob("*.css"))
    )
    assert "\ufffd" not in source
    assert "Sortable.min.js" not in source
    assert re.search(r"\.firewall-rule-drag\b", source) is None
