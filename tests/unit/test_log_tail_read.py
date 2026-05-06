# -*- coding: utf-8 -*-
"""日志尾部读取（大块文件仅扫末尾）。"""

from pathlib import Path

from app.ui.pages.services import ServicesPage


def test_read_log_last_lines_caps_count(tmp_path: Path) -> None:
    """只返回末尾 max_lines 行。"""
    p = tmp_path / "a.log"
    p.write_text("\n".join(f"line{i}" for i in range(1000)), encoding="utf-8")
    out = ServicesPage._read_log_last_lines(p, 500)
    lines = out.splitlines()
    assert len(lines) == 500
    assert lines[0] == "line500"
    assert lines[-1] == "line999"
