# -*- coding: utf-8 -*-
"""指标卡、说明卡和快捷入口卡。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from nicegui import ui

from app.ui.vue import VpnSpotlightSurface


CardTone = Literal["default", "success", "warning", "danger", "info"]


@dataclass(slots=True)
class MetricCard:
    container: Any
    value: Any
    label: Any
    helper: Any | None
    icon: Any | None


@dataclass(slots=True)
class InfoCard:
    container: Any
    title: Any
    body: Any
    icon: Any | None


@dataclass(slots=True)
class QuickLinkCard:
    container: Any
    title: Any
    description: Any | None
    icon: Any | None




def _make_card_keyboard_clickable(container: Any, callback: Callable[..., Any]) -> None:
    """让可点击卡片具备按钮等价的键盘与语义行为。"""

    container.classes(add="vpn-card--clickable")
    container.props("tabindex=0 role=button")
    container.on("click", callback)
    container.on("keydown.enter", callback)
    container.on("keydown.space", callback)


def _validate_tone(tone: CardTone) -> None:
    if tone not in {"default", "success", "warning", "danger", "info"}:
        raise ValueError(f"不支持的卡片状态: {tone}")


def metric_card(
    label: str,
    value: object,
    *,
    helper: str | None = None,
    icon: str | None = None,
    tone: CardTone = "default",
    on_click: Callable[..., Any] | None = None,
    v2_effect: bool = False,
    compact: bool = False,
) -> MetricCard:
    """渲染可选状态与导航动作的指标卡。"""

    _validate_tone(tone)
    if v2_effect and compact:
        raise ValueError("紧凑指标卡不能同时启用 V2 Spotlight")

    normalized_label = str(label).strip()
    if not normalized_label:
        raise ValueError("指标名称不能为空")

    with ui.card().classes(
        f"vpn-metric-card vpn-card-tone--{tone}"
        + (" vpn-metric-card--v2" if v2_effect else "")
        + (" vpn-metric-card--compact" if compact else "")
    ) as container:
        if v2_effect:
            VpnSpotlightSurface()
        if on_click is not None:
            _make_card_keyboard_clickable(container, on_click)
        with ui.row().classes("items-start justify-between no-wrap w-full"):
            with ui.column().classes("gap-1 min-w-0"):
                label_element = ui.label(normalized_label).classes("vpn-metric-card__label")
                value_element = ui.label(str(value)).classes("vpn-metric-card__value")
                helper_element = (
                    ui.label(str(helper)).classes("vpn-metric-card__helper") if helper else None
                )
            icon_element = (
                ui.icon(icon).classes("vpn-metric-card__icon") if icon else None
            )
    return MetricCard(
        container=container,
        value=value_element,
        label=label_element,
        helper=helper_element,
        icon=icon_element,
    )


def info_card(
    title: str,
    body: str,
    *,
    icon: str | None = None,
    tone: CardTone = "default",
) -> InfoCard:
    """渲染用于影响说明、帮助或运行摘要的说明卡。"""

    _validate_tone(tone)
    normalized_title = str(title).strip()
    if not normalized_title:
        raise ValueError("说明卡标题不能为空")

    with ui.card().classes(f"vpn-info-card vpn-card-tone--{tone}") as container:
        with ui.row().classes("items-start no-wrap w-full gap-3"):
            icon_element = ui.icon(icon).classes("vpn-info-card__icon") if icon else None
            with ui.column().classes("gap-1 min-w-0"):
                title_element = ui.label(normalized_title).classes("vpn-info-card__title")
                body_element = ui.label(str(body)).classes("vpn-info-card__body")
    return InfoCard(container=container, title=title_element, body=body_element, icon=icon_element)


def quick_link_card(
    title: str,
    *,
    description: str | None = None,
    icon: str | None = None,
    route: str | None = None,
    on_click: Callable[..., Any] | None = None,
) -> QuickLinkCard:
    """渲染快捷入口卡；route 与 on_click 只能二选一。"""

    normalized_title = str(title).strip()
    if not normalized_title:
        raise ValueError("快捷入口标题不能为空")
    if route and on_click:
        raise ValueError("route 与 on_click 只能设置一个")

    callback = on_click
    if route:
        callback = lambda: ui.navigate.to(route)

    with ui.card().classes("vpn-quick-card") as container:
        if callback is not None:
            _make_card_keyboard_clickable(container, callback)
        with ui.row().classes("items-center no-wrap w-full gap-3"):
            icon_element = ui.icon(icon).classes("vpn-quick-card__icon") if icon else None
            with ui.column().classes("gap-1 min-w-0 grow"):
                title_element = ui.label(normalized_title).classes("vpn-quick-card__title")
                description_element = (
                    ui.label(str(description)).classes("vpn-quick-card__description")
                    if description
                    else None
                )
            if callback is not None:
                ui.icon("chevron_right").classes("vpn-quick-card__arrow")
    return QuickLinkCard(
        container=container,
        title=title_element,
        description=description_element,
        icon=icon_element,
    )
