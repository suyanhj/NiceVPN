# -*- coding: utf-8 -*-
"""防火墙标签独立管理页面：标签 CRUD、用户/组关联及生效集合预览。"""
from __future__ import annotations

import logging

from nicegui import ui

from app.ui.components.feedback import safe_notify
from app.ui.components.server_pager import render_server_pager

from app.services.firewall.tag_service import TagService
from app.ui.components.form_dialog import create_form_dialog
from app.ui.components.actions import create_action, secondary_action, danger_action

logger = logging.getLogger(__name__)


class TagsPage:
    TAG_PAGE_SIZE = 12
    DETAIL_PAGE_SIZE = 20

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
        self.tag_page = 1
        self.detail_pages: dict[tuple[str, str], int] = {}
        self.detail_tabs: dict[str, str] = {}
        self.detail_search: dict[tuple[str, str], str] = {}
        self.detail_containers: dict[str, object] = {}
        self.query = ""
        self.filter_mode = "all"
        with ui.column().classes("page-shell mgmt-page tags-page--v2"):
            with ui.element("section").classes("vpn-toolbar tags-toolbar"):
                with ui.element("div").classes("vpn-toolbar__search"):
                    ui.input(
                        placeholder="搜索标签名称或说明",
                        on_change=self._on_search,
                    ).props("outlined dense clearable debounce=250").classes(
                        "tags-search"
                    )
                    ui.select(
                        {"all": "全部标签", "assigned": "已关联成员",
                         "empty": "空标签", "referenced": "已引用规则"},
                        value="all",
                        on_change=self._on_filter,
                        label="筛选",
                    ).props("outlined dense").classes("tags-filter")
                with ui.element("div").classes("vpn-toolbar__primary"):
                    create_action("新建标签", icon="add", on_click=self._show_create_dialog)
            with ui.element("section").classes("mgmt-panel mgmt-panel-list tags-list-panel"):
                with ui.element("div").classes("mgmt-list-head"):
                    ui.label("标签列表").classes("mgmt-kicker")
                    self.total_label = ui.label("共 0 个标签").classes("group-list-count")
                self.list_container = ui.column().classes("mgmt-record-list tags-record-list w-full")
            self._refresh()

    def _on_search(self, event) -> None:
        self.query = str(event.value or "")
        self.tag_page = 1
        self._refresh()

    def _on_filter(self, event) -> None:
        self.filter_mode = str(event.value or "all")
        self.tag_page = 1
        self._refresh()

    def _change_tag_page(self, page: int) -> None:
        self.tag_page = max(1, int(page))
        self._refresh()

    def _change_detail_tab(self, tag_id: str, kind: str) -> None:
        if kind not in ("users", "groups", "rules"):
            return
        self.detail_tabs[tag_id] = kind
        self._refresh_detail(tag_id)

    def _change_detail_page(self, tag_id: str, kind: str, page: int) -> None:
        self.detail_pages[(tag_id, kind)] = max(1, int(page))
        self._refresh_detail(tag_id)

    def _apply_detail_search(self, tag_id: str, kind: str, query: str) -> None:
        self.detail_search[(tag_id, kind)] = str(query or "").strip()
        self.detail_pages[(tag_id, kind)] = 1
        self._refresh_detail(tag_id)

    def _refresh_detail(self, tag_id: str) -> None:
        container = self.detail_containers.get(tag_id)
        if container is None or getattr(container, "is_deleted", False):
            return
        container.clear()
        with container:
            self._render_detail_page(tag_id, self.detail_tabs.get(tag_id, "users"))

    def _toggle_tag(self, tag_id: str) -> None:
        if tag_id in self.expanded_tags:
            self.expanded_tags.remove(tag_id)
        else:
            self.expanded_tags.add(tag_id)
        self._refresh()

    def _refresh(self) -> None:
        if self.list_container is None or getattr(self.list_container, "is_deleted", False):
            return
        self.detail_containers.clear()
        self.list_container.clear()
        try:
            tags = self.service.list_all()
            counts_by_tag = self.service.store.list_tag_counts()
            cards = []
            for tag in tags:
                tag_id = str(tag["id"])
                counts = counts_by_tag.get(tag_id, {"users": 0, "groups": 0, "rules": 0})
                if self._matches_filter(
                    tag, self.query, self.filter_mode,
                    users=counts["users"], groups=counts["groups"], rules=counts["rules"],
                ):
                    cards.append((tag, counts))
            if hasattr(self, "total_label"):
                self.total_label.text = f"共 {len(cards)} / {len(tags)} 个标签" if self.query or self.filter_mode != "all" else f"共 {len(tags)} 个标签"
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
                total_pages = max(1, (len(cards) + self.TAG_PAGE_SIZE - 1) // self.TAG_PAGE_SIZE)
                self.tag_page = min(max(1, self.tag_page), total_pages)
                start = (self.tag_page - 1) * self.TAG_PAGE_SIZE
                for tag, counts in cards[start:start + self.TAG_PAGE_SIZE]:
                    self._render_card(tag, counts)
                if len(cards) > self.TAG_PAGE_SIZE:
                    render_server_pager(
                        total=len(cards), page=self.tag_page,
                        page_size=self.TAG_PAGE_SIZE, on_change=self._change_tag_page,
                    )
        except Exception as exc:
            logger.exception("加载防火墙标签失败")
            with self.list_container:
                ui.label(f"加载标签失败：{exc}").classes("text-negative")

    def _render_card(self, tag: dict, counts: dict[str, int]) -> None:
        tag_id = str(tag["id"])
        expanded = tag_id in self.expanded_tags

        with ui.element("section").classes(
            "tags-record-card" + (" is-expanded" if expanded else "")
        ):
            with ui.row().classes("tags-record-header items-center no-wrap"):
                with ui.button(
                    on_click=lambda tid=tag_id: self._toggle_tag(tid),
                ).props(
                    f"flat no-caps align=left aria-label='展开或收起标签'"
                    f" aria-expanded={'true' if expanded else 'false'}"
                ).classes("tags-record-toggle"):
                    with ui.row().classes("items-center no-wrap tags-record-main"):
                        ui.icon("expand_less" if expanded else "expand_more").classes(
                            "tags-record-chevron"
                        )
                        ui.icon("sell").classes("tags-record-icon")
                        with ui.column().classes("tags-record-copy"):
                            ui.label(str(tag["name"])).classes("mgmt-record-title")
                            if tag.get("description"):
                                ui.label(str(tag["description"])).classes(
                                    "tags-record-description"
                                )
                with ui.row().classes("tags-record-meta items-center"):
                    with ui.element("span").classes("mgmt-meta-item"):
                        ui.icon("person_outline", size="14px")
                        ui.label(f"用户 {counts['users']}")
                    with ui.element("span").classes("mgmt-meta-item"):
                        ui.icon("folder_shared", size="14px")
                        ui.label(f"组 {counts['groups']}")
                    with ui.element("span").classes("mgmt-meta-item"):
                        ui.icon("shield", size="14px")
                        ui.label(f"规则 {counts['rules']}")
                with ui.element("div").classes("mgmt-actions tags-record-actions"):
                    secondary_action(
                        "编辑", icon="edit",
                        on_click=lambda data=dict(tag): self._show_edit_dialog(data),
                    ).props("dense")
                    danger_action(
                        "删除", icon="delete",
                        on_click=lambda data=dict(tag): self._show_delete_dialog(data),
                        disabled=bool(counts["rules"]),
                        tooltip="该标签被防火墙规则引用，请先删除规则" if counts["rules"] else None,
                    ).props("dense")
            if expanded:
                with ui.column().classes("tags-record-details"):
                    with ui.row().classes("tags-detail-toolbar items-center justify-between"):
                        ui.label(f"标签 ID：{tag_id}").classes("tags-detail-id")
                        secondary_action(
                            "管理关联", icon="group_add",
                            on_click=lambda tid=tag_id: self._show_members_dialog(tid),
                        ).props("dense")
                    kind = self.detail_tabs.get(tag_id, "users")
                    with ui.tabs(value=kind, on_change=lambda e, tid=tag_id: self._change_detail_tab(tid, str(e.value))).classes("firewall-compact-tabs tags-detail-tabs w-full").props(
                        "dense align=left inline-label"
                    ):
                        ui.tab("users", label=f"关联用户 ({counts['users']})", icon="people")
                        ui.tab("groups", label=f"关联组 ({counts['groups']})", icon="folder_shared")
                        ui.tab("rules", label=f"引用规则 ({counts['rules']})", icon="shield")
                    with ui.column().classes("tags-detail-panel tags-detail-panels w-full") as detail:
                        self.detail_containers[tag_id] = detail
                        self._render_detail_page(tag_id, kind)

    def _render_detail_page(self, tag_id: str, kind: str) -> None:
        key = (tag_id, kind)
        page = self.detail_pages.get(key, 1)
        search = self.detail_search.get(key, "")
        if kind in ("users", "groups"):
            with ui.row().classes("w-full items-center gap-2"):
                search_input = ui.input(
                    placeholder="搜索用户名" if kind == "users" else "搜索组名称或 ID",
                    value=search,
                ).props("outlined dense clearable").classes("flex-grow")
                secondary_action(
                    "查询", icon="search",
                    on_click=lambda: self._apply_detail_search(tag_id, kind, search_input.value),
                ).props("dense")
        rows, total = self.service.store.list_tag_detail_page(
            tag_id, kind, offset=(page - 1) * self.DETAIL_PAGE_SIZE,
            limit=self.DETAIL_PAGE_SIZE, search=search,
        )
        last_page = max(1, (total + self.DETAIL_PAGE_SIZE - 1) // self.DETAIL_PAGE_SIZE)
        if page > last_page:
            page = last_page
            self.detail_pages[key] = page
            rows, total = self.service.store.list_tag_detail_page(
                tag_id, kind, offset=(page - 1) * self.DETAIL_PAGE_SIZE,
                limit=self.DETAIL_PAGE_SIZE, search=search,
            )
        if kind == "users":
            self._render_user_members(rows)
        elif kind == "groups":
            self._render_group_members(rows)
        else:
            self._render_rule_refs(rows)
        if total > self.DETAIL_PAGE_SIZE:
            render_server_pager(
                total=total, page=page, page_size=self.DETAIL_PAGE_SIZE,
                on_change=lambda target: self._change_detail_page(tag_id, kind, target),
            )

    @staticmethod
    def _render_user_members(users: list[dict]) -> None:
        if not users:
            ui.label("暂无关联用户").classes("tags-detail-empty")
            return
        with ui.column().classes("tags-detail-list w-full"):
            for info in users:
                username = str(info.get("username") or "")
                status = str(info.get("status") or "unknown")
                with ui.row().classes(
                    "tags-detail-row w-full items-center justify-between gap-3"
                ):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("person_outline", size="sm")
                        ui.label(username).classes("tags-detail-name")
                    ui.badge(
                        "启用" if status == "active" else "停用" if status == "disabled"
                        else "已删除" if status == "deleted" else "未知"
                    ).props("outline")

    @staticmethod
    def _render_group_members(groups: list[dict]) -> None:
        if not groups:
            ui.label("暂无关联用户组").classes("tags-detail-empty")
            return
        with ui.column().classes("tags-detail-list w-full"):
            for info in groups:
                group_id = str(info.get("id") or "")
                with ui.row().classes(
                    "tags-detail-row w-full items-center justify-between gap-3 flex-wrap"
                ):
                    with ui.column().classes("gap-0"):
                        ui.label(str(info.get("name") or group_id)).classes("tags-detail-name")
                        ui.label(group_id).classes("tags-detail-muted")
                    ui.label(str(info.get("subnet") or "—")).classes(
                        "font-mono text-sm"
                    )

    @staticmethod
    def _render_rule_refs(rules: list[dict]) -> None:
        if not rules:
            ui.label("暂无防火墙规则引用").classes("tags-detail-empty")
            return
        with ui.column().classes("tags-detail-list w-full"):
            for rule in sorted(rules, key=lambda item: int(item.get("priority") or 0)):
                with ui.row().classes(
                    "tags-detail-row w-full items-center justify-between gap-3 flex-wrap"
                ):
                    with ui.column().classes("gap-0"):
                        ui.label(str(rule.get("description") or rule.get("id") or "未命名规则")).classes(
                            "font-medium"
                        )
                        ui.label(
                            f"优先级 {rule.get('priority', '—')} · "
                            f"{str(rule.get('protocol') or 'all').upper()} · "
                            f"{rule.get('dest_ip') or '所有目标'}"
                        ).classes("tags-detail-muted")
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
                safe_notify("请输入标签名称", type="warning")
                return False
            try:
                self.service.create(name, str(fields["description"].value or "").strip())
                safe_notify("标签已创建", type="positive")
                self._refresh()
                return True
            except Exception as exc:
                safe_notify(f"创建失败：{exc}", type="negative")
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
                safe_notify("标签已更新", type="positive")
                self._refresh()
                return True
            except Exception as exc:
                safe_notify(f"更新失败：{exc}", type="negative")
                return False

        dialog = create_form_dialog("编辑标签", body=body, on_submit=submit)
        dialog.open()

    def _show_members_dialog(self, tag_id: str) -> None:
        tag = self.service.store.get_tag(tag_id)
        if not tag:
            safe_notify("标签已不存在", type="negative")
            return
        try:
            current = self.service.store.get_tag_members(tag_id)
        except Exception as exc:
            safe_notify(f"读取成员失败：{exc}", type="negative")
            return

        # 仅当前页候选项进入 UI。已选集合独立维护，搜索、翻页均不丢失选择。
        selected = {
            "users": set(current["usernames"]),
            "groups": set(current["group_ids"]),
        }
        state = {
            kind: {"page": 1, "search": "", "selected_only": False}
            for kind in ("users", "groups")
        }
        containers = {}
        count_labels = {}

        def set_selected(kind: str, item_id: str, checked: bool) -> None:
            if checked:
                selected[kind].add(item_id)
            else:
                selected[kind].discard(item_id)
            count_labels[kind].text = f"已选 {len(selected[kind])} 项（跨页保留）"

        def set_selected_many(kind: str, item_ids: list[str], checked: bool) -> None:
            if checked:
                selected[kind].update(item_ids)
            else:
                selected[kind].difference_update(item_ids)
            count_labels[kind].text = f"已选 {len(selected[kind])} 项（跨页保留）"
            render_choices(kind)

        def set_matching_selected(kind: str, checked: bool) -> None:
            # 明确涵盖所有搜索结果，而非仅当前页；仅获取 ID，避免加载全量用户 payload。
            try:
                ids = self.service.store.list_tag_candidate_ids(
                    kind, search=str(state[kind]["search"]),
                )
            except Exception as exc:
                safe_notify(f"批量选择失败：{exc}", type="negative")
                return
            set_selected_many(kind, ids, checked)

        def render_choices(kind: str) -> None:
            container = containers[kind]
            if getattr(container, "is_deleted", False):
                return
            container.clear()
            config = state[kind]
            page = int(config["page"])
            if config["selected_only"]:
                ids = sorted(selected[kind])
                total = len(ids)
                last = max(1, (total + self.DETAIL_PAGE_SIZE - 1) // self.DETAIL_PAGE_SIZE)
                page = min(page, last)
                start = (page - 1) * self.DETAIL_PAGE_SIZE
                rows = [{"username" if kind == "users" else "id": value} for value in ids[start:start + self.DETAIL_PAGE_SIZE]]
            else:
                rows, total = self.service.store.list_tag_candidates_page(
                    kind, search=str(config["search"]),
                    offset=(page - 1) * self.DETAIL_PAGE_SIZE,
                    limit=self.DETAIL_PAGE_SIZE,
                )
                last = max(1, (total + self.DETAIL_PAGE_SIZE - 1) // self.DETAIL_PAGE_SIZE)
                if page > last:
                    page = last
                    rows, total = self.service.store.list_tag_candidates_page(
                        kind, search=str(config["search"]),
                        offset=(page - 1) * self.DETAIL_PAGE_SIZE,
                        limit=self.DETAIL_PAGE_SIZE,
                    )
            config["page"] = page
            with container:
                if not rows:
                    ui.label("暂无匹配成员").classes("tags-detail-empty")
                else:
                    with ui.row().classes(
                        "tags-member-bulk-actions w-full items-center gap-1 flex-wrap"
                    ):
                        page_ids = [
                            str(row["username" if kind == "users" else "id"])
                            for row in rows
                        ]
                        if config["selected_only"]:
                            ui.button(
                                "取消本页选择",
                                on_click=lambda: set_selected_many(kind, page_ids, False),
                            ).props("flat dense no-caps color=primary")
                            ui.button(
                                "清空已选",
                                on_click=lambda: set_selected_many(kind, list(selected[kind]), False),
                            ).props("flat dense no-caps color=primary")
                        else:
                            ui.button(
                                "全选本页",
                                on_click=lambda: set_selected_many(kind, page_ids, True),
                            ).props("flat dense no-caps color=primary")
                            all_button = ui.button(
                                f"全选搜索结果（{total}）",
                                on_click=lambda: set_matching_selected(kind, True),
                            ).props("flat dense no-caps color=primary")
                            all_button.tooltip("包含符合当前搜索条件的所有分页记录")
                            ui.button(
                                "取消搜索结果已选",
                                on_click=lambda: set_matching_selected(kind, False),
                            ).props("flat dense no-caps color=primary")
                for row in rows:
                    item_id = str(row["username" if kind == "users" else "id"])
                    label = (
                        item_id if kind == "users"
                        else f"{row.get('name') or item_id} ({item_id})"
                    )
                    ui.checkbox(
                        label, value=item_id in selected[kind],
                        on_change=lambda e, k=kind, ident=item_id: set_selected(k, ident, bool(e.value)),
                    ).classes("w-full")
                if total > self.DETAIL_PAGE_SIZE:
                    render_server_pager(
                        total=total, page=page, page_size=self.DETAIL_PAGE_SIZE,
                        on_change=lambda target, k=kind: change_page(k, target),
                    )

        def change_page(kind: str, page: int) -> None:
            state[kind]["page"] = max(1, int(page))
            render_choices(kind)

        def apply_search(kind: str, search: str) -> None:
            state[kind]["page"] = 1
            state[kind]["search"] = str(search or "").strip()
            render_choices(kind)

        def toggle_selected_only(kind: str, checked: bool) -> None:
            state[kind]["page"] = 1
            state[kind]["selected_only"] = checked
            render_choices(kind)

        def search_click(kind: str, field):
            # 生成零参回调，避免 NiceGUI 把点击事件注入捕获参数。
            return lambda: apply_search(kind, field.value)

        def body() -> None:
            with ui.tabs(value="users").classes("w-full firewall-compact-tabs") as tabs:
                ui.tab("users", label="关联用户", icon="people")
                ui.tab("groups", label="关联组", icon="folder_shared")
            with ui.tab_panels(tabs, value="users").classes("w-full"):
                for kind, title in (("users", "用户名"), ("groups", "组名称或 ID")):
                    with ui.tab_panel(kind).classes("w-full"):
                        with ui.row().classes("w-full items-center gap-2"):
                            search_input = ui.input(
                                placeholder=f"搜索{title}",
                            ).props("outlined dense clearable").classes("flex-grow")
                            secondary_action(
                                "搜索", icon="search",
                                on_click=search_click(kind, search_input),
                            ).props("dense")
                        with ui.row().classes("w-full items-center justify-between"):
                            count_labels[kind] = ui.label(
                                f"已选 {len(selected[kind])} 项（跨页保留）"
                            ).classes("tags-detail-muted")
                            ui.checkbox(
                                "只看已选", value=False,
                                on_change=lambda e, k=kind: toggle_selected_only(k, bool(e.value)),
                            ).props("dense")
                        containers[kind] = ui.column().classes(
                            "w-full tags-detail-list tags-member-choice-list"
                        )
                        render_choices(kind)
            with ui.row().classes("w-full justify-end items-center"):
                info = ui.icon("info_outline", size="19px").classes(
                    "tags-member-info-icon cursor-help"
                ).props("aria-label='标签生效说明'")
                info.tooltip(
                    "变更成员后，仅被启用防火墙规则引用的标签才会触发 VPN_FORWARD 刷新。"
                )

        def submit() -> bool:
            try:
                self.service.set_members(
                    tag_id,
                    usernames=sorted(selected["users"]),
                    group_ids=sorted(selected["groups"]),
                )
                safe_notify("标签成员已更新", type="positive")
                self._refresh()
                return True
            except Exception as exc:
                safe_notify(f"更新成员失败：{exc}", type="negative")
                return False

        dialog = create_form_dialog(
            f"管理标签成员 · {tag['name']}", body=body, on_submit=submit
        )
        dialog.card.classes("tags-members-dialog")
        dialog.open()

    def _delete_tag_and_refresh(self, tag_id: str, dialog) -> bool:
        """成功时先通知再销毁卡片，防止使用已删除的事件 slot。"""
        try:
            self.service.delete(tag_id)
        except Exception as exc:
            safe_notify(f"删除失败：{exc}", type="negative")
            return False
        safe_notify("标签已删除", type="positive")
        dialog.close()
        self._refresh()
        return True

    def _show_delete_dialog(self, tag: dict) -> None:
        with ui.dialog() as dialog, ui.card().classes("vpn-form-dialog"):
            ui.label(f"确认删除标签「{tag['name']}」？").classes("text-lg font-medium")
            ui.label("删除后，用户和用户组的标签关联也会一并删除。").classes("section-caption")

            def confirm() -> None:
                self._delete_tag_and_refresh(str(tag["id"]), dialog)

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("取消", on_click=dialog.close).props("flat")
                ui.button("确认删除", on_click=confirm).props("color=negative")
        dialog.open()
