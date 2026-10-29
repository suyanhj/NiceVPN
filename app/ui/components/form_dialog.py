# -*- coding: utf-8 -*-
"""统一表单对话框与提交忙碌控制。"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from nicegui import ui

from app.ui.components.feedback import AsyncOperationGuard, safe_notify


@dataclass(slots=True)
class FormDialog:
    dialog: Any
    card: Any
    body: Any
    submit_button: Any
    cancel_button: Any
    guard: AsyncOperationGuard

    def set_busy(self, busy: bool) -> None:
        """同步提交按钮的 loading/disable 状态。"""

        if busy:
            self.submit_button.props(add="loading")
            self.submit_button.disable()
            self.cancel_button.disable()
        else:
            self.submit_button.props(remove="loading")
            self.submit_button.enable()
            self.cancel_button.enable()

    def open(self) -> None:
        self.dialog.open()

    def close(self) -> None:
        self.dialog.close()


def create_form_dialog(
    title: str,
    *,
    body: Callable[[], None] | None = None,
    on_submit: Callable[[], bool | None | Awaitable[bool | None]] | None = None,
    submit_label: str = "保存",
    cancel_label: str = "取消",
    submit_color: str = "primary",
    close_on_success: bool = True,
) -> FormDialog:
    """创建统一表单对话框；失败时保留输入，成功后按配置关闭。"""

    normalized_title = str(title).strip()
    if not normalized_title:
        raise ValueError("表单标题不能为空")

    holder: dict[str, FormDialog] = {}

    async def submit() -> None:
        handle = holder["handle"]
        if not handle.guard.try_start():
            return
        handle.set_busy(True)
        try:
            result = None
            if on_submit is not None:
                result = on_submit()
                if inspect.isawaitable(result):
                    result = await result
            if close_on_success and result is not False:
                handle.dialog.close()
        except Exception as exc:
            safe_notify(f"保存失败: {exc}", type="negative")
        finally:
            handle.guard.finish()
            handle.set_busy(False)

    with ui.dialog() as dialog, ui.card().classes("vpn-form-dialog") as card:
        ui.label(normalized_title).classes("vpn-form-dialog__title")
        with ui.column().classes("vpn-form-dialog__body w-full") as body_container:
            if body is not None:
                body()
        with ui.row().classes("vpn-form-dialog__actions w-full justify-end"):
            cancel_button = ui.button(cancel_label, on_click=dialog.close).props("flat no-caps")
            submit_button = ui.button(submit_label, on_click=submit).props(
                f"unelevated no-caps color={submit_color}"
            )

    handle = FormDialog(
        dialog=dialog,
        card=card,
        body=body_container,
        submit_button=submit_button,
        cancel_button=cancel_button,
        guard=AsyncOperationGuard(),
    )
    holder["handle"] = handle
    return handle
