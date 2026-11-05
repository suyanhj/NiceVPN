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

    @staticmethod
    def _matches_filter(tag: dict, query: str, mode: str, *, users: int, groups: int, rules: int) -> bool:
        """标签列表的纯数据筛选逻辑，方便回归测试。"""
        keyword = (query or "").strip().casefold()
        if keyword and keyword not in (
            str(tag.get("name") or "") + " " + str(tag.get("description") or "")
        ).casefold():
            return False
        if mode == "assigned" and users + groups == 0:
            return False
        if mode == "empty" and users + groups != 0:
            return False
        if mode == "referenced" and rules == 0:
            return False
        return True

    def render(self) -> None:
        self.expanded_tags: set[str] = set()
        self.query = ""
        self.filter_mode = "all"
        with ui.column().classes("page-shell mgmt-page tags-page--v2 w-full gap-4"):
            with ui.element("section").classes("mgmt-panel"):
                with ui.row().classes("w-full items-center justify-between flex-wrap gap-3"):
                    with ui.column().classes("gap-1"):
                        ui.label("标签管理").classes("text-lg font-medium")
                        ui.label("管理用户与组的策略标签；点击标签行查看关联明细。").classes(
                            "section-caption"
                        )
                    create_action("新建标签", icon="add", on_click=self._show_create_dialog)
                with ui.row().classes("w-full gap-3 items-center flex-wrap mt-3"):
                    ui.input(
                        placeholder="搜索标签名称或说明",
                        on_change=self._on_search,
                    ).props("outlined dense clearable").classes("min-w-[14rem] flex-1")
                    ui.select(
                        {"all": "全部标签", "assigned": "已关联成员",
                         "empty": "空标签", "referenced": "已引用规则"},
                        value="all",
                        on_change=self._on_filter,
                        label="筛选",
                    ).props("outlined dense").classes("w-44")
            self.list_container = ui.column().classes("w-full gap-3")
            self._refresh()

    def _on_search(self, event) -> None:
        self.query = str(event.value or "")
        self._refresh()

    def _on_filter(self, event) -> None:
        self.filter_mode = str(event.value or "all")
        self._refresh()

    def _toggle_tag(self, tag_id: str) -> None:
        if tag_id in self.expanded_tags:
            self.expanded_tags.remove(tag_id)
        else:
            self.expanded_tags.add(tag_id)
        self._refresh()

    def _refresh(self) -> None:
        if self.list_container is None:
            return
        self.list_container.clear()
        try:
            tags = self.service.list_all()
            rules = self.service.store.list_firewall_rules()
            all_users = UserService(store=self.service.store).list_all()
            all_groups = GroupService(store=self.service.store).list_all()
            users_by_name = {
                str(user.get("username") or ""): user for user in all_users
            }
            groups_by_id = {
                str(group.get("id") or ""): group for group in all_groups
            }
            rules_by_tag: dict[str, list[dict]] = {}
            for rule in rules:
                if rule.get("owner_type") == "tag":
                    rules_by_tag.setdefault(str(rule.get("owner_id") or ""), []).append(rule)

            cards = []
            for tag in tags:
                tag_id = str(tag["id"])
                members = self.service.store.get_tag_members(tag_id)
                linked_rules = rules_by_tag.get(tag_id, [])
                if self._matches_filter(
                    tag, self.query, self.filter_mode,
                    users=len(members["usernames"]),
                    groups=len(members["group_ids"]),
                    rules=len(linked_rules),
                ):
                    cards.append((tag, members, linked_rules))
            with self.list_container:
                if not tags:
                    with ui.element("section").classes("mgmt-panel"):
                        ui.label("还没有标签，可以点击「新建标签」创建。").classes(
                            "section-caption"
                        )
                    return
                if not cards:
                    with ui.element("section").classes("mgmt-panel"):
                        ui.label("没有符合筛选条件的标签。").classes("section-caption")
                    return
                ui.label(f"共 {len(cards)} 个标签").classes("section-caption")
                for tag, members, linked_rules in cards:
                    self._render_card(
                        tag, members, linked_rules,
                        users_by_name=users_by_name, groups_by_id=groups_by_id,
                    )
        except Exception as exc:
            logger.exception("加载防火墙标签失败")
            with self.list_container:
                ui.label(f"加载标签失败：{exc}").classes("text-negative")

    def _render_card(
        self, tag: dict, members: dict[str, list[str]], rules: list[dict],
        *, users_by_name: dict[str, dict], groups_by_id: dict[str, dict],
    ) -> None:
        tag_id = str(tag["id"])
        usernames = members["usernames"]
        group_ids = members["group_ids"]
        expanded = tag_id in self.expanded_tags
        with ui.element("section").classes(
            "mgmt-panel w-full !p-0 overflow-hidden rounded-lg "
            + ("border-l-4 border-l-primary" if expanded else "")
        ):
            with ui.row().classes("w-full items-center no-wrap gap-2 p-3"):
                with ui.button(
                    on_click=lambda tid=tag_id: self._toggle_tag(tid),
                ).props(
                    f"flat no-caps align=left aria-label='"
                    f"{'收起' if expanded else '展开'}标签 {tag['name']}'"
                    f" aria-expanded={'true' if expanded else 'false'}"
                ).classes("flex-1 min-w-0 !justify-start !px-2"):
                    with ui.row().classes("items-center gap-3 w-full flex-wrap"):
                        ui.icon("expand_less" if expanded else "expand_more", size="sm")
                        ui.icon("sell", size="sm").classes("text-primary")
                        with ui.column().classes("gap-0 min-w-[10rem] flex-1"):
                            ui.label(str(tag["name"])).classes(
                                "font-semibold text-left break-all"
                            )
                            if tag.get("description"):
                                ui.label(str(tag["description"])).classes(
                                    "text-xs opacity-70 text-left"
                                )
                        with ui.row().classes("items-center gap-3 text-sm flex-wrap"):
                            ui.label(f"用户 {len(usernames)}")
                            ui.label(f"组 {len(group_ids)}")
                            ui.label(f"规则 {len(rules)}")
                with ui.row().classes("items-center gap-1 shrink-0"):
                    secondary_action(
                        "编辑", icon="edit",
                        on_click=lambda data=dict(tag): self._show_edit_dialog(data),
                    ).props("dense")
                    danger_action(
                        "删除", icon="delete",
                        on_click=lambda data=dict(tag): self._show_delete_dialog(data),
                        disabled=bool(rules),
                        tooltip="该标签被防火墙规则引用，请先删除规则" if rules else None,
                    ).props("dense")
            if expanded:
                ui.separator()
                with ui.column().classes("w-full gap-4 px-4 py-4 bg-slate-500/5"):
                    with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
                        ui.label(f"标签 ID：{tag_id}").classes("text-xs opacity-60 break-all")
                        secondary_action(
                            "管理关联", icon="group_add",
                            on_click=lambda tid=tag_id: self._show_members_dialog(tid),
                        ).props("dense")
                    with ui.tabs().classes("w-full").props("dense align=left") as tabs:
                        user_tab = ui.tab(f"关联用户 ({len(usernames)})", icon="people")
                        group_tab = ui.tab(f"关联组 ({len(group_ids)})", icon="folder_shared")
                        rule_tab = ui.tab(f"引用规则 ({len(rules)})", icon="shield")
                    with ui.tab_panels(tabs, value=user_tab).classes(
                        "w-full bg-transparent"
                    ).props("animated=False"):
                        with ui.tab_panel(user_tab).classes("!p-0"):
                            self._render_user_members(usernames, users_by_name)
                        with ui.tab_panel(group_tab).classes("!p-0"):
                            self._render_group_members(group_ids, groups_by_id)
                        with ui.tab_panel(rule_tab).classes("!p-0"):
                            self._render_rule_refs(rules)

    @staticmethod
    def _render_user_members(names: list[str], lookup: dict[str, dict]) -> None:
        if not names:
            ui.label("暂无关联用户").classes("section-caption p-3")
            return
        with ui.column().classes("w-full gap-0"):
            for username in names:
                info = lookup.get(username, {})
                status = str(info.get("status") or "unknown")
                with ui.row().classes(
                    "w-full items-center justify-between gap-3 p-3 border-b border-slate-500/10"
                ):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("person_outline", size="sm")
                        ui.label(username).classes("font-medium")
                    ui.badge(
                        "启用" if status == "active" else "停用" if status == "disabled"
                        else "已删除" if status == "deleted" else "未知"
                    ).props("outline")

    @staticmethod
    def _render_group_members(ids: list[str], lookup: dict[str, dict]) -> None:
        if not ids:
            ui.label("暂无关联用户组").classes("section-caption p-3")
            return
        with ui.column().classes("w-full gap-0"):
            for group_id in ids:
                info = lookup.get(group_id, {})
                with ui.row().classes(
                    "w-full items-center justify-between flex-wrap gap-3 p-3 "
                    "border-b border-slate-500/10"
                ):
                    with ui.column().classes("gap-0"):
                        ui.label(str(info.get("name") or group_id)).classes("font-medium")
                        ui.label(group_id).classes("text-xs opacity-60")
                    ui.label(str(info.get("subnet") or "—")).classes(
                        "font-mono text-sm"
                    )

    @staticmethod
    def _render_rule_refs(rules: list[dict]) -> None:
        if not rules:
            ui.label("暂无防火墙规则引用").classes("section-caption p-3")
            return
        with ui.column().classes("w-full gap-0"):
            for rule in sorted(rules, key=lambda item: int(item.get("priority") or 0)):
                with ui.row().classes(
                    "w-full items-center justify-between gap-3 flex-wrap p-3 "
                    "border-b border-slate-500/10"
                ):
                    with ui.column().classes("gap-0"):
                        ui.label(str(rule.get("description") or rule.get("id") or "未命名规则")).classes(
                            "font-medium"
                        )
                        ui.label(
                            f"优先级 {rule.get('priority', '—')} · "
                            f"{str(rule.get('protocol') or 'all').upper()} · "
                            f"{rule.get('dest_ip') or '所有目标'}"
                        ).classes("text-xs opacity-65")
                    with ui.row().classes("items-center gap-2"):
                        ui.badge(str(rule.get("action") or "—").upper()).props("outline")
                        ui.badge(
                            "启用" if rule.get("enabled", True) else "停用"
                        ).props("outline")

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
