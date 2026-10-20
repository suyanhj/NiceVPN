# -*- coding: utf-8 -*-
"""共享表单分区：标题、说明、字段区和操作栏。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nicegui import ui


@dataclass(slots=True)
class FormSection:
    """已渲染表单分区引用。"""

    container: Any
    body: Any
    actions: Any | None


def render_form_section(
    title: str,
    *,
    description: str | None = None,
    kicker: str | None = None,
    body: Callable[[], None] | None = None,
    actions: Callable[[], None] | None = None,
) -> FormSection:
    """渲染响应式设置/向导表单分区。"""

    normalized = str(title or "").strip()
    if not normalized:
        raise ValueError("表单分区标题不能为空")

    actions_container = None
    with ui.element("section").classes("settings-panel vpn-form-section") as container:
        with ui.element("div").classes("settings-panel-head vpn-form-section__head"):
            with ui.element("div").classes("settings-panel-copy"):
                if kicker and str(kicker).strip():
                    ui.label(str(kicker).strip()).classes("settings-kicker")
                ui.label(normalized).classes("settings-panel-title")
                if description and str(description).strip():
                    ui.label(str(description).strip()).classes(
                        "settings-panel-desc"
                    )

        with ui.element("div").classes(
            "settings-stack vpn-form-section__body"
        ) as body_container:
            if body is not None:
                body()

        if actions is not None:
            with ui.element("div").classes(
                "settings-toolbar vpn-form-section__actions"
            ) as actions_container:
                actions()

    return FormSection(
        container=container,
        body=body_container,
        actions=actions_container,
    )
