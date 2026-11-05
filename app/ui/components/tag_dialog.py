# -*- coding: utf-8 -*-
"""用户与组通用的标签关联弹窗（业务页面复用）。"""
from __future__ import annotations

from nicegui import ui
from app.ui.components.feedback import safe_notify

from app.services.firewall.tag_service import TagService


def show_entity_tags_dialog(entity_type: str, entity_id: str) -> None:
    """打开关联管理；支持在同一弹窗创建标签。"""
    service = TagService()
    tags = service.list_all()
    choices = {tag["id"]: tag["name"] for tag in tags}
    selected = service.get_entity_tag_ids(entity_type, entity_id)
    label = "用户" if entity_type == "user" else "用户组"

    with ui.dialog() as dialog, ui.card().classes("vpn-form-dialog w-[min(92vw,28rem)]"):
        ui.label(f"{label}标签 · {entity_id}").classes("text-lg font-medium")
        ui.label("一个标签可关联多个用户和用户组，防火墙按当前成员实时收敛。").classes(
            "text-caption"
        )
        tag_select = ui.select(
            choices, value=selected, multiple=True, with_input=True,
            label="关联标签",
        ).classes("w-full")
        tag_name = ui.input(label="新标签名称（可选）").classes("w-full")

        def create_tag() -> None:
            name = str(tag_name.value or "").strip()
            if not name:
                safe_notify("请输入标签名称", type="warning")
                return
            try:
                tag = service.create(name)
                choices[tag["id"]] = tag["name"]
                tag_select.set_options(choices)
                tag_select.set_value(list(dict.fromkeys([*(tag_select.value or []), tag["id"]])))
                tag_name.set_value("")
                safe_notify("标签已创建，请保存关联", type="positive")
            except Exception as exc:
                safe_notify(f"创建标签失败：{exc}", type="negative")

        def save() -> None:
            try:
                service.set_entity_tags(
                    entity_type, entity_id, tag_select.value or [],
                )
            except Exception as exc:
                safe_notify(f"保存标签失败：{exc}", type="negative")
                return
            safe_notify("标签关联已更新，防火墙已刷新", type="positive")
            dialog.close()

        with ui.row().classes("w-full items-center justify-between gap-2"):
            ui.button("创建标签", icon="add", on_click=create_tag).props("outline")
            with ui.row().classes("items-center gap-2"):
                ui.button("取消", on_click=dialog.close).props("flat")
                ui.button("保存关联", icon="save", on_click=save)
    dialog.open()
