# -*- coding: utf-8 -*-
"""应用退出生命周期契约测试。"""

from pathlib import Path


def test_main_registers_graceful_shutdown_hook() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "main.py").read_text(encoding="utf-8")

    assert "app.on_shutdown(_shutdown_background_services)" in source
    assert "request_shutdown()" in source
    assert "stop_conntrack_audit_listener" in source


def test_main_does_not_force_os_exit_after_keyboard_interrupt() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "main.py").read_text(encoding="utf-8")

    assert "os._exit(0)" not in source
