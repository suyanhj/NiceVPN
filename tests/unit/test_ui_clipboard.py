# -*- coding: utf-8 -*-
"""验证 HTTPS、localhost 与局域网 HTTP 剪贴板分流。"""

import asyncio
import pytest

from app.ui import copy_clipboard


@pytest.mark.parametrize(
    ("scheme", "hostname", "expected"),
    (
        ("https", "vpn.example.com", True),
        ("http", "localhost", True),
        ("http", "127.0.0.1", True),
        ("http", "::1", True),
        ("http", "192.168.1.20", False),
        ("http", "vpn.lan", False),
    ),
)
def test_clipboard_context_selection(scheme: str, hostname: str, expected: bool) -> None:
    """HTTPS/localhost 使用标准 API，局域网 HTTP 使用兼容库。"""

    assert copy_clipboard.clipboard_context_is_secure(scheme, hostname) is expected


def test_secure_context_uses_nicegui_clipboard(monkeypatch) -> None:
    """安全上下文直接调用 NiceGUI 剪贴板能力。"""

    copied: list[str] = []
    monkeypatch.setattr(copy_clipboard, "_current_clipboard_context_is_secure", lambda: True)
    monkeypatch.setattr(copy_clipboard.ui.clipboard, "write", copied.append)

    assert asyncio.run(copy_clipboard.copy_text_to_clipboard("安全文本")) is True
    assert copied == ["安全文本"]


def test_insecure_http_uses_local_clipboard_adapter(monkeypatch) -> None:
    """局域网 HTTP 调用本地 clipboard.js 适配层，不依赖 CDN。"""

    calls: list[tuple[str, float]] = []

    async def run_javascript(code: str, *, timeout: float):
        calls.append((code, timeout))
        return True

    monkeypatch.setattr(copy_clipboard, "_current_clipboard_context_is_secure", lambda: False)
    monkeypatch.setattr(copy_clipboard.ui, "run_javascript", run_javascript)

    assert asyncio.run(copy_clipboard.copy_text_to_clipboard("局域网文本")) is True
    assert "window.vpnClipboard.copy" in calls[0][0]
    assert "navigator.clipboard" not in calls[0][0]
    assert "document.execCommand" not in calls[0][0]


def test_clipboard_failure_opens_manual_copy_path(monkeypatch) -> None:
    """浏览器拒绝复制时显示手动复制路径。"""

    shown: list[str] = []

    async def run_javascript(code: str, *, timeout: float):
        return False

    monkeypatch.setattr(copy_clipboard, "_current_clipboard_context_is_secure", lambda: False)
    monkeypatch.setattr(copy_clipboard.ui, "run_javascript", run_javascript)
    monkeypatch.setattr(copy_clipboard, "_show_manual_copy_dialog", shown.append)

    assert asyncio.run(copy_clipboard.copy_text_to_clipboard("手动复制文本")) is False
    assert shown == ["手动复制文本"]
