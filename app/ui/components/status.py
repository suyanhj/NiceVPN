# -*- coding: utf-8 -*-
"""统一状态标识与结构化数据值组件。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from nicegui import ui


StatusTone = Literal["running", "disabled", "warning", "failed", "unknown", "success", "info"]
ValueKind = Literal["text", "ip", "cidr", "port", "serial", "rule"]

_STATUS_META: dict[str, tuple[str, str, str]] = {
    "running": ("运行", "check_circle", "positive"),
    "disabled": ("停用", "pause_circle", "grey-6"),
    "warning": ("警告", "warning", "warning"),
    "failed": ("失败", "error", "negative"),
    "unknown": ("未知", "help", "grey-5"),
    "success": ("正常", "check_circle", "positive"),
    "info": ("信息", "info", "info"),
}


@dataclass(slots=True)
class StatusBadge:
    """状态标识元素引用。"""

    container: Any
    icon: Any
    label: Any


@dataclass(slots=True)
class StructuredValue:
    """结构化数据值元素引用。"""

    container: Any
    label: Any
    copy_button: Any | None


def status_badge(
    tone: StatusTone = "unknown",
    *,
    label: str | None = None,
    icon: str | None = None,
    tooltip: str | None = None,
) -> StatusBadge:
    """渲染带文字与图标的统一状态标识，避免只依赖颜色传达含义。"""

    if tone not in _STATUS_META:
        raise ValueError(f"不支持的状态类型: {tone}")
    default_label, default_icon, color = _STATUS_META[tone]
    text = str(label if label is not None else default_label).strip()
    if not text:
        raise ValueError("状态文案不能为空")

    with ui.row().classes(f"vpn-status vpn-status--{tone} items-center no-wrap") as container:
        status_icon = ui.icon(icon or default_icon, color=color).classes("vpn-status__icon")
        status_label = ui.label(text).classes("vpn-status__label")
    if tooltip:
        container.tooltip(str(tooltip))
    return StatusBadge(container=container, icon=status_icon, label=status_label)


def structured_value(
    value: object,
    *,
    kind: ValueKind = "text",
    copyable: bool = False,
    copy_message: str = "已复制",
    tooltip: str | None = None,
) -> StructuredValue:
    """以一致的数据字体展示 IP/CIDR/端口/序列号/规则等结构化值。"""

    if kind not in {"text", "ip", "cidr", "port", "serial", "rule"}:
        raise ValueError(f"不支持的数据值类型: {kind}")
    text = "" if value is None else str(value)

    async def copy_value() -> None:
        from app.ui.copy_clipboard import copy_text_to_clipboard
        from app.ui.components.feedback import safe_notify

        if await copy_text_to_clipboard(text):
            safe_notify(copy_message, type="positive")

    with ui.row().classes(f"vpn-data-value vpn-data-value--{kind} items-center no-wrap") as container:
        value_label = ui.label(text or "—").classes("vpn-data-value__text")
        copy_button = None
        if copyable and text:
            copy_button = (
                ui.button(icon="content_copy", on_click=copy_value)
                .props("flat round dense color=primary aria-label=复制")
                .classes("vpn-data-value__copy")
            )
    if tooltip:
        container.tooltip(str(tooltip))
    return StructuredValue(container=container, label=value_label, copy_button=copy_button)
