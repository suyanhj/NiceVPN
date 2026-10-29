# -*- coding: utf-8 -*-
"""安全上下文与局域网 HTTP 均可用的统一剪贴板入口。"""
from __future__ import annotations

import json
import logging

from nicegui import context, ui


logger = logging.getLogger(__name__)


def clipboard_context_is_secure(scheme: str, hostname: str | None) -> bool:
    """判断浏览器地址是否可使用标准 Clipboard API。"""

    normalized_host = str(hostname or "").strip().lower().strip("[]")
    return str(scheme).lower() == "https" or normalized_host in {
        "localhost",
        "127.0.0.1",
        "::1",
    }


def _current_clipboard_context_is_secure() -> bool:
    """从当前 NiceGUI 客户端请求识别 HTTPS、localhost 或局域网 HTTP。"""

    try:
        request = context.client.request
        forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
        scheme = forwarded_proto or request.url.scheme
        return clipboard_context_is_secure(scheme, request.url.hostname)
    except RuntimeError:
        return False


def _show_manual_copy_dialog(text: str) -> None:
    """浏览器拒绝自动复制时提供明确的手动复制路径。"""

    with ui.dialog() as dialog, ui.card().classes("w-full max-w-2xl"):
        ui.label("浏览器未允许自动复制").classes("text-h6")
        ui.label("请在下方文本框中按 Ctrl+C（macOS 使用 Command+C）复制。")
        textarea = ui.textarea(value=text).props("readonly autogrow").classes("w-full")
        with ui.row().classes("w-full justify-end"):
            ui.button("关闭", on_click=dialog.close).props("flat")
    dialog.open()
    textarea.run_method("focus")
    textarea.run_method("select")


async def copy_text_to_clipboard(text: str) -> bool:
    """复制文本；安全上下文用 NiceGUI，局域网 HTTP 用本地 clipboard.js。"""

    payload = str(text)
    if _current_clipboard_context_is_secure():
        ui.clipboard.write(payload)
        return True

    try:
        copied = await ui.run_javascript(
            f"return await window.vpnClipboard.copy({json.dumps(payload)});",
            timeout=2.0,
        )
    except Exception:
        logger.exception("局域网 HTTP 剪贴板兼容调用失败")
        copied = False

    if not copied:
        _show_manual_copy_dialog(payload)
        return False
    return True
