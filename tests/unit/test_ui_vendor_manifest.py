# -*- coding: utf-8 -*-
"""验证本地第三方前端资源清单和离线加载边界。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


UI_DIR = Path(__file__).resolve().parents[2] / "app" / "ui"
STATIC_DIR = UI_DIR / "static"
VENDOR_DIR = STATIC_DIR / "vendor"
MANIFEST_PATH = VENDOR_DIR / "manifest.toml"


def _load_manifest() -> dict:
    """读取第三方库清单。"""

    with MANIFEST_PATH.open("rb") as file:
        return tomllib.load(file)


def test_vendor_manifest_matches_distributed_javascript() -> None:
    """每个本地第三方脚本都固定版本、可追溯且校验值一致。"""

    manifest = _load_manifest()
    assert manifest["schema_version"] == 1
    libraries = manifest["libraries"]
    assert isinstance(libraries, list)

    required = {
        "name",
        "version",
        "file",
        "source_url",
        "license",
        "purpose",
        "sha256",
    }
    registered_files: set[str] = set()
    for library in libraries:
        assert required <= library.keys()
        assert all(str(library[key]).strip() for key in required)
        assert re.fullmatch(r"[0-9a-f]{64}", library["sha256"])
        file_path = VENDOR_DIR / library["file"]
        assert file_path.parent == VENDOR_DIR
        assert file_path.is_file()
        assert hashlib.sha256(file_path.read_bytes()).hexdigest() == library["sha256"]
        registered_files.add(library["file"])

    distributed_files = {
        path.name
        for path in VENDOR_DIR.iterdir()
        if path.is_file() and path.suffix.lower() == ".js"
    }
    assert registered_files == distributed_files


def test_nicegui_frontend_dependencies_are_not_duplicated() -> None:
    """不重复打包 NiceGUI 已提供的前端依赖。"""

    blocked_names = ("quasar", "ag-grid", "aggrid", "echarts", "sortable")
    for path in VENDOR_DIR.iterdir():
        lowered = path.name.lower()
        assert not any(name in lowered for name in blocked_names), path


def test_ui_runtime_does_not_load_frontend_dependencies_from_cdn() -> None:
    """UI 运行时代码和样式不得通过远程标签或 CSS 导入加载依赖。"""

    remote_markup = re.compile(r"(?:src|href)\s*=\s*['\"]https?://", re.IGNORECASE)
    remote_css_import = re.compile(r"@import\s+(?:url\()?['\"]?https?://", re.IGNORECASE)

    for path in UI_DIR.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".py", ".js", ".css", ".html"}:
            continue
        source = path.read_text(encoding="utf-8")
        assert not remote_markup.search(source), path
        assert not remote_css_import.search(source), path
