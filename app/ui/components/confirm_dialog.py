# -*- coding: utf-8 -*-
"""统一确认对话框，用于普通、高影响和指定文本确认。"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from nicegui import ui


DangerLevel = Literal["normal", "warning", "danger"]
_LEVEL_COLOR: dict[DangerLevel, str] = {
    "normal": "primary",
    "warning": "warning",
    "danger": "negative",
}


@dataclass(slots=True)
class ConfirmDialog:
    """确认对话框内部元素引用，供新页面和测试精确控制。"""

    dialog: Any
    confirm_button: Any
    cancel_button: Any
    confirm_input: Any | None


def build(
    message: str,
    on_confirm: Callable[[], Any | Awaitable[Any]] | None,
    title: str = "确认操作",
    *,
    impact: str | None = None,
    object_count: int | None = None,
    confirm_text: str | None = None,
    danger_level: DangerLevel = "danger",
    confirm_color: str | None = None,
) -> ConfirmDialog:
    """构建确认对话框但不自动打开；旧颜色参数继续兼容。"""

    if danger_level not in _LEVEL_COLOR:
        raise ValueError(f"不支持的危险级别: {danger_level}")
    if object_count is not None and object_count < 0:
        raise ValueError("对象数量不能小于 0")

    normalized_message = str(message).strip()
    if not normalized_message:
        raise ValueError("确认说明不能为空")
    required_text = str(confirm_text or "").strip() or None
    holder: dict[str, Any] = {}

    async def handle_confirm() -> None:
        button = holder["confirm_button"]
        if required_text:
            confirm_input = holder["confirm_input"]
            if str(confirm_input.value or "").strip() != required_text:
                return
        button.disable()
        dialog.close()
        if on_confirm is None:
            return
        result = on_confirm()
        if inspect.isawaitable(result):
            await result

    def update_confirmation(event: Any) -> None:
        button = holder.get("confirm_button")
        if button is None or required_text is None:
            return
        if str(event.value or "").strip() == required_text:
            button.enable()
        else:
            button.disable()

    color = confirm_color or _LEVEL_COLOR[danger_level]
    with ui.dialog() as dialog, ui.card().classes("vpn-confirm-dialog"):
        ui.label(str(title).strip() or "确认操作").classes("vpn-confirm-dialog__title")
        ui.label(normalized_message).classes("vpn-confirm-dialog__message")

        if impact:
            with ui.row().classes("vpn-confirm-dialog__impact items-start no-wrap"):
                ui.icon("warning_amber").classes("vpn-confirm-dialog__impact-icon")
                ui.label(str(impact)).classes("vpn-confirm-dialog__impact-text")

        if object_count is not None:
            ui.label(f"影响对象：{object_count} 项").classes("vpn-confirm-dialog__count")

        confirm_input = None
        if required_text:
            confirm_input = ui.input(
                label=f"请输入 {required_text} 确认",
                on_change=update_confirmation,
            ).classes("w-full").props("autocomplete=off")

        with ui.row().classes("vpn-confirm-dialog__actions w-full justify-end"):
            cancel_button = ui.button("取消", on_click=dialog.close).props("flat no-caps")
            confirm_button = ui.button("确认", on_click=handle_confirm).props(
                f"unelevated no-caps color={color}"
            )
            if required_text:
                confirm_button.disable()

    holder["confirm_button"] = confirm_button
    holder["confirm_input"] = confirm_input
    return ConfirmDialog(
        dialog=dialog,
        confirm_button=confirm_button,
        cancel_button=cancel_button,
        confirm_input=confirm_input,
    )


def show(
    message: str,
    on_confirm,
    title: str = "确认操作",
    *,
    impact: str | None = None,
    object_count: int | None = None,
    confirm_text: str | None = None,
    danger_level: DangerLevel = "danger",
    confirm_color: str | None = None,
):
    """显示确认对话框，并保持旧接口返回 dialog 的兼容行为。"""

    handle = build(
        message,
        on_confirm,
        title,
        impact=impact,
        object_count=object_count,
        confirm_text=confirm_text,
        danger_level=danger_level,
        confirm_color=confirm_color,
    )
    handle.dialog.open()
    return handle.dialog


def _handle_confirm(dialog, on_confirm):
    """兼容旧测试或外部调用的同步确认辅助。"""

    dialog.close()
    if on_confirm:
        on_confirm()
