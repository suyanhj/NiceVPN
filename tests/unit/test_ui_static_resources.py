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


def test_firewall_uses_nicegui_sortable_without_duplicate_bundle() -> None:
    """防火墙排序只使用 NiceGUI 自带 SortableJS。"""

    source = (UI_DIR / "pages" / "firewall.py").read_text(encoding="utf-8")
    assert ".make_sortable(" in source
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
    effects_index = theme_source.index('"effects.css"')
    legacy_index = theme_source.index('"legacy.css"')
    assert token_index < shell_index < effects_index < legacy_index

    shell_source = (STATIC_DIR / "css" / "shell.css").read_text(encoding="utf-8")
    effects_source = (STATIC_DIR / "css" / "effects.css").read_text(encoding="utf-8")
    assert ".vpn-shell" in shell_source
    assert "@media (max-width: 767px)" in shell_source
    assert ".vpn-tech-grid" in effects_source
    assert "prefers-reduced-motion" in effects_source
