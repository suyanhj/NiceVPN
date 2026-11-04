# -*- coding: utf-8 -*-
"""防火墙标签独立管理页面：标签 CRUD、用户/组关联及生效集合预览。"""
from __future__ import annotations

import logging

from nicegui import ui

from app.services.firewall.tag_service import TagService
from app.services.group.crud import GroupService
from app.services.user.crud import UserService
from app.ui.components.form_dialog import create_form_dialog
from app.ui.components.actions import create_action, secondary_action, danger_action

logger = logging.getLogger(__name__)


class TagsPage:
    def __init__(self):
        self.service = TagService()
        self.list_container = None

    def render(self) -> None:
        with ui.column().classes("page-shell mgmt-page tags-page--v2"):
            with ui.element("section").classes("mgmt-panel"):
                with ui.row().classes("w-full items-center justify-between gap-3 flex-wrap"):
                    ui.label("标签管理").classes("text-lg font-medium")
                    create_action("新建标签", icon="add", on_click=self._show_create_dialog)
                ui.label(
                    "一个标签可关联多个用户与用户组。中心防火墙按标签当前成员聚合 IP/CIDR，"
                    "并通过 ipset 原子更新；空标签不会放行任何地址。"
                ).classes("section-caption")
            self.list_container = ui.column().classes("w-full gap-3")
            self._refresh()

    def _refresh(self) -> None:
        if self.list_container is None:
            return
        self.list_container.clear()
        try:
            tags = self.service.list_all()
            rules = self.service.store.list_firewall_rules()
            rule_counts = {
                tag["id"]: sum(
                    row.get("owner_type") == "tag"
                    and str(row.get("owner_id")) == str(tag["id"])
                    for row in rules
                )
                for tag in tags
            }
            with self.list_container:
                if not tags:
                    with ui.element("section").classes("mgmt-panel"):
                        ui.label("还没有标签，可以点击「新建标签」创建。").classes("section-caption")
                    return
                for tag in tags:
                    self._render_card(tag, int(rule_counts.get(tag["id"], 0)))
        except Exception as exc:
            logger.exception("加载防火墙标签失败")
            with self.list_container:
                ui.label(f"加载标签失败：{exc}").classes("text-negative")

    def _render_card(self, tag: dict, rule_count: int) -> None:
        tag_id = str(tag["id"])
        members = self.service.store.get_tag_members(tag_id)
        usernames = members["usernames"]
        group_ids = members["group_ids"]
        with ui.element("section").classes("mgmt-panel mgmt-record-card"):
            with ui.row().classes("w-full items-center justify-between gap-3 flex-wrap"):
                with ui.column().classes("gap-1 min-w-0"):
                    with ui.row().classes("items-center gap-2 flex-wrap"):
                        ui.icon("sell", size="sm")
                        ui.label(str(tag["name"])).classes("mgmt-record-title")
                    if tag.get("description"):
                        ui.label(str(tag["description"])).classes("section-caption")
                    ui.label(
                        f"关联用户 {len(usernames)} · 关联用户组 {len(group_ids)} · 引用规则 {rule_count}"
                    ).classes("section-caption")
                    ui.label(f"ID：{tag_id}").classes("section-caption")
                with ui.row().classes("items-center gap-2 flex-wrap"):
                    secondary_action(
                        "管理成员", icon="people",
                        on_click=lambda tid=tag_id: self._show_members_dialog(tid),
                    ).props("dense")
                    secondary_action(
                        "编辑", icon="edit",
                        on_click=lambda data=dict(tag): self._show_edit_dialog(data),
                    ).props("dense")
                    danger_action(
                        "删除", icon="delete",
                        on_click=lambda data=dict(tag): self._show_delete_dialog(data),
                        disabled=bool(rule_count),
                        tooltip="该标签仍被防火墙规则引用，请先删除规则" if rule_count else None,
                    ).props("dense")
            if usernames or group_ids:
                texts = [
                    *(f"用户：{name}" for name in usernames[:5]),
                    *(f"组：{gid[:8]}" for gid in group_ids[:5]),
                ]
                ui.label(" · ".join(texts) + (" …" if len(usernames) + len(group_ids) > 10 else "")).classes(
                    "section-caption"
                )

    def _show_create_dialog(self) -> None:
        fields = {}

        def body() -> None:
            fields["name"] = ui.input("标签名称").classes("w-full")
            fields["description"] = ui.input("说明（选填）").classes("w-full")

        def submit() -> bool:
            name = str(fields["name"].value or "").strip()
            if not name:
                ui.notify("请输入标签名称", type="warning")
                return False
            try:
                self.service.create(name, str(fields["description"].value or "").strip())
                self._refresh()
                ui.notify("标签已创建", type="positive")
                return True
            except Exception as exc:
                ui.notify(f"创建失败：{exc}", type="negative")
                return False

        dialog = create_form_dialog("新建标签", body=body, on_submit=submit)
        dialog.open()

    def _show_edit_dialog(self, tag: dict) -> None:
        fields = {}

        def body() -> None:
            fields["name"] = ui.input("标签名称", value=tag["name"]).classes("w-full")
            fields["description"] = ui.input(
                "说明（选填）", value=tag.get("description") or ""
            ).classes("w-full")

        def submit() -> bool:
            try:
                self.service.update(
                    tag["id"], name=fields["name"].value,
                    description=fields["description"].value,
                )
                self._refresh()
                ui.notify("标签已更新", type="positive")
                return True
            except Exception as exc:
                ui.notify(f"更新失败：{exc}", type="negative")
                return False

        dialog = create_form_dialog("编辑标签", body=body, on_submit=submit)
        dialog.open()

    def _show_members_dialog(self, tag_id: str) -> None:
        tag = self.service.store.get_tag(tag_id)
        if not tag:
            ui.notify("标签已不存在", type="negative")
            return
        try:
            current = self.service.store.get_tag_members(tag_id)
            users = UserService(store=self.service.store).list_all()
            groups = GroupService(store=self.service.store).list_all()
        except Exception as exc:
            ui.notify(f"读取成员失败：{exc}", type="negative")
            return

        def body() -> None:
            fields["users"] = ui.select(
                {u["username"]: u["username"] for u in users},
                label="关联用户（多选）",
                value=list(current["usernames"]),
                multiple=True, with_input=True,
            ).classes("w-full")
            fields["groups"] = ui.select(
                {g["id"]: f"{g.get('name') or g['id']} ({g.get('subnet') or '—'})" for g in groups},
                label="关联用户组（多选）",
                value=list(current["group_ids"]),
                multiple=True, with_input=True,
            ).classes("w-full")
            ui.label("变更成员后，仅被启用防火墙规则引用的标签才会触发 VPN_FORWARD 刷新。").classes(
                "section-caption"
            )

        fields = {}

        def submit() -> bool:
            try:
                self.service.set_members(
                    tag_id,
                    usernames=fields["users"].value or [],
                    group_ids=fields["groups"].value or [],
                )
                self._refresh()
                ui.notify("标签成员已更新", type="positive")
                return True
            except Exception as exc:
                ui.notify(f"更新成员失败：{exc}", type="negative")
                return False

        dialog = create_form_dialog(
            f"管理标签成员 · {tag['name']}", body=body, on_submit=submit
        )
        dialog.card.classes("w-[min(95vw,32rem)]")
        dialog.open()

    def _show_delete_dialog(self, tag: dict) -> None:
        with ui.dialog() as dialog, ui.card().classes("vpn-form-dialog"):
            ui.label(f"确认删除标签「{tag['name']}」？").classes("text-lg font-medium")
            ui.label("删除后，用户和用户组的标签关联也会一并删除。").classes("section-caption")

            def confirm() -> None:
                try:
                    self.service.delete(tag["id"])
                    dialog.close()
                    self._refresh()
                    ui.notify("标签已删除", type="positive")
                except Exception as exc:
                    ui.notify(f"删除失败：{exc}", type="negative")

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("取消", on_click=dialog.close).props("flat")
                ui.button("确认删除", on_click=confirm).props("color=negative")
        dialog.open()
