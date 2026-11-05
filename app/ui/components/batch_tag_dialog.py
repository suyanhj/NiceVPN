# -*- coding: utf-8 -*-
"""用户与组共用的批量标签关联弹窗。"""
from __future__ import annotations

from nicegui import ui

from app.services.firewall.tag_service import TagService
from app.ui.components.form_dialog import create_form_dialog


def show_batch_tags_dialog(entity_type: str, entity_ids, *, on_success=None) -> None:
    """仅对选中对象添加或移除指定标签，不覆盖其它已有标签。"""
    ids = sorted({str(v).strip() for v in entity_ids if str(v).strip()})
    if entity_type not in ("user", "group"):
        raise ValueError("仅支持 user/group")
    if not ids:
        ui.notify("请先选择用户或组", type="warning")
        return

    service = TagService()
    options = {str(tag["id"]): str(tag["name"]) for tag in service.list_all()}
    if not options:
        ui.notify("尚无可关联的标签，请先在标签管理页创建", type="warning")
        return

    fields = {}
    label = "用户" if entity_type == "user" else "用户组"

    def body():
        ui.label(f"已选择 {len(ids)} 个{label}").classes("section-caption")
        fields["operation"] = ui.select(
            {"add": "批量添加标签（保留原标签）",
             "remove": "批量移除指定标签（保留其它标签）"},
            value="add",
            label="操作方式",
        ).classes("w-full")
        fields["tags"] = ui.select(
            options, value=[], label="选择标签", multiple=True, with_input=True,
        ).classes("w-full")
        ui.label("批量修改采用 SQLite 事务，相关防火墙集合只刷新一次；"
                 "更新失败将尝试恢复原关联。").classes("section-caption")

    def submit() -> bool:
        selected = fields["tags"].value or []
        if not selected:
            ui.notify("请至少选择一个标签", type="warning")
            return False
        try:
            count = service.batch_change_entities(
                entity_type, ids, selected,
                operation=str(fields["operation"].value or "add"),
            )
        except Exception as exc:
            ui.notify(f"批量标签操作失败：{exc}", type="negative")
            return False
        ui.notify(f"已更新 {count} 个{label}的标签", type="positive")
        if on_success is not None:
            on_success()
        return True

    dialog = create_form_dialog(
        f"批量关联标签 · {label}", body=body, on_submit=submit,
    )
    dialog.open()
