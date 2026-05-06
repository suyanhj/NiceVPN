# -*- coding: utf-8 -*-
"""二次确认弹窗组件 — 用于敏感操作的二次确认"""
from nicegui import ui


def show(
    message: str,
    on_confirm,
    title: str = "确认操作",
    *,
    confirm_color: str = "negative",
):
    """
    显示二次确认弹窗。

    Args:
        message: 提示信息内容
        on_confirm: 用户点击确认后的回调函数
        title: 弹窗标题，默认「确认操作」
        confirm_color: 确认按钮颜色（Quasar），破坏性操作用 negative，启动/重启等可用 primary
    """
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-sm q-pa-md"):
        ui.label(title).classes("text-h6 text-weight-bold q-mb-sm")
        ui.label(message).classes("text-body2 q-mb-lg text-center")
        with ui.row().classes("w-full justify-center q-gutter-sm no-wrap"):
            ui.button("取消", on_click=dialog.close).props("flat no-caps")
            ui.button(
                "确认",
                on_click=lambda: _handle_confirm(dialog, on_confirm),
            ).props(f"color={confirm_color} no-caps")
    dialog.open()
    return dialog


def _handle_confirm(dialog, on_confirm):
    """处理确认按钮点击：关闭弹窗并执行回调"""
    dialog.close()
    if on_confirm:
        on_confirm()
