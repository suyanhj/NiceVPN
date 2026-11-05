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
        """仅更新仍存在的元素；保存回调可能已清空它们的父容器。"""

        # 检查每个按钮，避免父组件被 clear() 后继续更改被删除的按钮。
        for button in (self.submit_button, self.cancel_button):
            if getattr(button, "is_deleted", False):
                continue
            client = getattr(button, "client", None)
            if client is not None and getattr(client, "is_deleted", False):
                continue
            if button is self.submit_button:
                if busy:
                    button.props(add="loading")
                else:
                    button.props(remove="loading")
            if busy:
                button.disable()
            else:
                button.enable()

    def open(self) -> None:
        if not getattr(self.dialog, "is_deleted", False):
            self.dialog.open()

    def close(self) -> None:
        if not getattr(self.dialog, "is_deleted", False):
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
        try:
            handle.set_busy(True)
            result = None
            if on_submit is not None:
                result = on_submit()
                if inspect.isawaitable(result):
                    result = await result
            if close_on_success and result is not False:
                handle.close()
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
