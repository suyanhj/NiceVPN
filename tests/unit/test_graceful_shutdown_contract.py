# -*- coding: utf-8 -*-
"""应用退出生命周期契约测试。"""

from pathlib import Path


def _main_source() -> str:
    root = Path(__file__).resolve().parents[2]
    return (root / "main.py").read_text(encoding="utf-8")


def test_main_registers_graceful_shutdown_hook() -> None:
    source = _main_source()

    assert "app.on_shutdown(_shutdown_background_services)" in source
    assert "request_shutdown()" in source
    assert "stop_conntrack_audit_listener" in source


def test_main_uses_cooperative_shutdown_without_forced_process_exit() -> None:
    source = _main_source()

    for forbidden in (
        "os._exit(",
        "_start_uvicorn_exit_watcher",
        "server.should_exit",
        "server.force_exit",
        "signal.signal(",
        "timeout_graceful_shutdown=",
    ):
        assert forbidden not in source
