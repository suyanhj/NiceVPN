# -*- coding: utf-8 -*-
"""用户组管理页面。"""

from __future__ import annotations

import logging
from typing import Any

from nicegui import ui
from nicegui.elements.checkbox import Checkbox

from app.core.config import load_config
from app.services.group.crud import GroupService
from app.services.group.subnet import check_subnet_conflict
from app.ui.components import confirm_dialog
from app.ui.components.actions import create_action, danger_action, secondary_action
from app.ui.components.cards import metric_card
from app.ui.components.form_dialog import create_form_dialog
from app.ui.components.list_selection import (
    apply_row_checkboxes,
    sync_select_all_checkbox,
    update_visible_selection,
)
from app.ui.components.states import empty_state, error_state
from app.ui.components.server_pager import render_server_pager
from app.ui.components.status import status_badge, structured_value
from app.ui.components.toolbars import render_bulk_action_bar
from app.ui.copy_clipboard import copy_text_to_clipboard

logger = logging.getLogger(__name__)


class GroupsPage:
    """用户组管理页面。"""

    def __init__(self):
        self.group_service = GroupService()
        self.selected_ids: set[str] = set()
        self.select_all_checkbox: Checkbox | None = None
        self.visible_group_ids: list[str] = []
        self.group_checkboxes: dict[str, Checkbox] = {}
        self.bulk_bar = None
        self.bulk_enable_button = None
        self.bulk_disable_button = None
        self.bulk_delete_button = None
        self.page = 1
        self.page_size = 50
        self.total_groups = 0
        self.list_container = None
        self.total_label = None

    def render(self):
        """渲染用户组管理页面；列表仅读取当前 SQLite 页。"""

        try:
            summary = self._load_summary()
            summary_error: Exception | None = None
        except Exception as exc:
            logger.exception("加载用户组摘要失败")
            summary = {"total": 0, "active": 0, "disabled": 0, "users": 0}
            summary_error = exc

        def bulk_actions() -> None:
            create_action(
                "新建用户组",
                icon="add",
                on_click=self._show_create_dialog,
            )
            disabled = not self.selected_ids
            self.bulk_enable_button = secondary_action(
                "批量启用",
                icon="check_circle",
                on_click=self._batch_enable,
                disabled=disabled,
                tooltip="请先选择用户组" if disabled else None,
            )
            self.bulk_disable_button = secondary_action(
                "批量停用",
                icon="block",
                on_click=self._batch_disable_confirm,
                disabled=disabled,
                tooltip="请先选择用户组" if disabled else None,
            )
            self.bulk_delete_button = danger_action(
                "批量删除",
                icon="delete_sweep",
                on_click=self._batch_delete_confirm,
                disabled=disabled,
                tooltip="请先选择用户组" if disabled else None,
            )

        with ui.column().classes("page-shell mgmt-page groups-page--v2"):
            self._render_summary_metrics(summary)

            self.bulk_bar = render_bulk_action_bar(
                len(self.selected_ids),
                actions=bulk_actions,
            )

            with ui.element("section").classes("mgmt-panel mgmt-panel-list"):
                with ui.element("div").classes("mgmt-list-head"):
                    with ui.row().classes("items-center gap-2"):
                        self.select_all_checkbox = ui.checkbox(
                            value=False,
                            on_change=lambda e: self._toggle_select_all(bool(e.value)),
                        ).classes("mgmt-checkbox").tooltip("选择当前页全部用户组")
                        ui.label("用户组列表").classes("mgmt-kicker")
                    self.total_label = ui.label(
                        "摘要加载失败" if summary_error else "共 0 个用户组"
                    ).classes("group-list-count")

                self.list_container = ui.column().classes("w-full")

        self._refresh_group_page()
        self._sync_bulk_state()

    def _load_summary(self) -> dict[str, int]:
        summary_method = getattr(self.group_service, "summary", None)
        if callable(summary_method):
            return dict(summary_method())
        groups = self.group_service.list_all()
        active = sum(
            str(group.get("status") or "active") == "active"
            for group in groups
        )
        return {
            "total": len(groups),
            "active": active,
            "disabled": len(groups) - active,
            "users": sum(int(group.get("user_count") or 0) for group in groups),
        }

    def _load_group_page(self) -> tuple[list[dict], int, str]:
        page_method = getattr(self.group_service, "list_page", None)
        offset = max(0, (self.page - 1) * self.page_size)
        if callable(page_method):
            groups, total = page_method(offset=offset, limit=self.page_size)
        else:
            all_groups = self.group_service.list_all()
            total = len(all_groups)
            groups = all_groups[offset : offset + self.page_size]

        root_method = getattr(self.group_service, "first_group_id", None)
        if callable(root_method):
            root_id = str(root_method() or "")
        else:
            all_groups = self.group_service.list_all()
            root_id = str(all_groups[0].get("id") or "") if all_groups else ""
        return [dict(group) for group in groups], int(total), root_id

    def _refresh_group_page(self) -> None:
        if self.list_container is None:
            return
        try:
            groups, total, root_id = self._load_group_page()
            pages = max(1, (total + self.page_size - 1) // self.page_size)
            if self.page > pages:
                self.page = pages
                groups, total, root_id = self._load_group_page()
        except Exception as exc:
            logger.exception("加载用户组列表失败")
            if self.total_label is not None:
                self.total_label.text = "加载失败"
            self.list_container.clear()
            with self.list_container:
                error_state(
                    str(exc),
                    title="用户组列表加载失败",
                    on_retry=self._refresh_group_page,
                )
            return

        self.total_groups = total
        if self.total_label is not None:
            self.total_label.text = f"共 {total} 个用户组"
        self.list_container.clear()
        with self.list_container:
            self._render_group_list(groups, root_id=root_id)
            if total > self.page_size:
                render_server_pager(
                    total=total,
                    page=self.page,
                    page_size=self.page_size,
                    on_change=self._on_group_page_change,
                )

    def _on_group_page_change(self, page: int) -> None:
        target = max(1, int(page))
        pages = max(1, (self.total_groups + self.page_size - 1) // self.page_size)
        target = min(target, pages)
        if target == self.page:
            return
        self.page = target
        self._refresh_group_page()

    @staticmethod
    def _render_summary_metrics(summary: dict[str, int]) -> None:
        """使用数据库聚合结果渲染分组摘要，不读取全部 group payload。"""

        total_count = int(summary.get("total") or 0)
        active_count = int(summary.get("active") or 0)
        disabled_count = int(summary.get("disabled") or 0)
        user_count = int(summary.get("users") or 0)

        with ui.element("div").classes("vpn-v2-overview-grid"):
            metric_card(
                "用户组总数",
                total_count,
                helper_tooltip="包含根组和所有业务子组",
                icon="folder_shared",
                tone="info",
                compact=True,
            )
            metric_card(
                "启用分组",
                active_count,
                helper_tooltip="当前处于启用状态、可继续参与管理操作的用户组",
                icon="check_circle",
                tone="success" if active_count else "default",
                compact=True,
            )
            metric_card(
                "停用分组",
                disabled_count,
                helper_tooltip="当前已停用、暂停参与后续管理操作的用户组",
                icon="block",
                tone="warning" if disabled_count else "default",
                compact=True,
            )
            metric_card(
                "组内用户",
                user_count,
                helper_tooltip="按当前用户组列表快照汇总的组内用户数量",
                icon="people",
                tone="default",
                compact=True,
            )

    def _render_group_list(
        self,
        groups: list[dict],
        *,
        root_id: str = "",
    ):
        """只渲染当前数据库页的用户组卡片。"""

        self.visible_group_ids = [group["id"] for group in groups]
        self.group_checkboxes.clear()
        if not groups:
            self._sync_select_all()
            empty_state(
                "暂无用户组",
                description="完成初始化后会创建根组，也可以在这里新建子组。",
                action_label="新建用户组",
                on_action=self._show_create_dialog,
                icon="group_off",
            )
            return

        root_id = str(root_id or groups[0]["id"])
        with ui.element("div").classes("mgmt-record-list"):
            for group in groups:
                view = self._build_group_view_model(group, root_id=root_id)
                self._render_group_card(view, is_root=view["is_root"])
        self._sync_select_all()

    @staticmethod
    def _build_group_view_model(group: dict, *, root_id: str) -> dict:
        """标准化分组展示数据，容忍缺失字段并明确根组/状态语义。"""

        group_id = str(group.get("id") or "")
        status = str(group.get("status") or "active")
        return {
            **group,
            "id": group_id,
            "name": str(group.get("name") or "未命名用户组"),
            "subnet": str(group.get("subnet") or "未配置子网"),
            "status": status if status in {"active", "disabled"} else "disabled",
            "user_count": int(group.get("user_count") or 0),
            "is_root": bool(group_id and group_id == root_id),
        }

    def _render_group_card(self, group: dict, is_root: bool = False):
        """渲染单个用户组卡片。"""

        group_id = str(group["id"])
        status = str(group.get("status") or "active")
        is_active = status == "active"
        user_count = int(group.get("user_count") or 0)

        card_classes = "mgmt-record-card groups-record-card"
        if is_root:
            card_classes += " groups-record-card--root"
        if not is_active:
            card_classes += " groups-record-card--disabled"

        with ui.element("div").classes(card_classes):
            with ui.element("div").classes("mgmt-record-main"):
                self.group_checkboxes[group_id] = ui.checkbox(
                    value=group_id in self.selected_ids,
                    on_change=lambda e, gid=group_id: self._toggle_select(gid, bool(e.value)),
                ).classes("mgmt-checkbox")

                with ui.element("div").classes("mgmt-record-copy"):
                    with ui.row().classes("items-center gap-2 flex-wrap"):
                        ui.label(str(group["name"])).classes("mgmt-record-title")
                        if is_root:
                            ui.badge("根组").props("outline").classes("groups-root-badge")
                    with ui.element("div").classes("mgmt-record-meta"):
                        with ui.element("span").classes("mgmt-meta-item"):
                            ui.icon("lan", size="14px")
                            ui.label(str(group["subnet"]))
                        with ui.element("span").classes("mgmt-meta-item"):
                            ui.icon("person", size="14px")
                            ui.label(f"用户数 {user_count}")

            with ui.column().classes("group-record-side hidden md:flex gap-2"):
                status_badge(
                    "running" if is_active else "disabled",
                    label="启用" if is_active else "停用",
                )
                structured_value(
                    group_id,
                    kind="serial",
                    copyable=True,
                    copy_message="组 ID 已复制",
                    tooltip="完整用户组 ID",
                )

            with ui.element("div").classes("mgmt-actions"):
                secondary_action(
                    "编辑",
                    icon="edit",
                    on_click=lambda current=group: self._show_edit_dialog(current),
                ).props("dense")

                if is_active:
                    secondary_action(
                        "停用",
                        icon="block",
                        on_click=lambda current=group: self._confirm_disable(current),
                    ).props("dense")
                else:
                    secondary_action(
                        "启用",
                        icon="check_circle",
                        on_click=lambda gid=group_id: self._do_enable(gid),
                    ).props("dense")

                if is_root:
                    danger_action(
                        "删除",
                        icon="delete",
                        disabled=True,
                        tooltip="根组定义全局 VPN 子网，不允许删除",
                    ).props("dense")
                else:
                    danger_action(
                        "删除",
                        icon="delete",
                        on_click=lambda current=group: self._confirm_delete(current),
                    ).props("dense")

    def _toggle_select(self, group_id: str, selected: bool):
        """切换用户组选中状态。"""

        if selected:
            self.selected_ids.add(group_id)
        else:
            self.selected_ids.discard(group_id)
        self._sync_select_all()
        self._sync_bulk_state()

    def _toggle_select_all(self, selected: bool):
        """选择或取消选择全部可见用户组。"""

        update_visible_selection(self.selected_ids, self.visible_group_ids, selected)
        apply_row_checkboxes(self.group_checkboxes, self.selected_ids)
        self._sync_select_all()
        self._sync_bulk_state()

    def _sync_select_all(self):
        """同步列表全选复选框。"""

        if self.select_all_checkbox is not None:
            sync_select_all_checkbox(
                self.select_all_checkbox,
                self.visible_group_ids,
                self.selected_ids,
            )

    def _sync_bulk_state(self) -> None:
        """同步批量操作数量与可用状态。"""

        if self.bulk_bar is not None:
            self.bulk_bar.set_selected_count(len(self.selected_ids))
        enabled = bool(self.selected_ids)
        for button in (
            self.bulk_enable_button,
            self.bulk_disable_button,
            self.bulk_delete_button,
        ):
            if button is None:
                continue
            if enabled:
                button.enable()
            else:
                button.disable()

    async def _copy_group_id(self, group_id: str):
        """兼容旧入口：复制完整用户组 ID。"""

        if await copy_text_to_clipboard(group_id):
            ui.notify("组 ID 已复制", type="positive")

    def _show_create_dialog(self):
        """显示新建用户组表单。"""

        existing_groups = self.group_service.list_all()
        root_group = existing_groups[0] if existing_groups else None
        fields: dict[str, Any] = {}

        def render_body() -> None:
            if root_group:
                ui.label(
                    f"子网须位于根组「{root_group['name']}」"
                    f"（{root_group.get('subnet', '')}）内，例如 10.224.1.0/24。"
                ).classes("text-caption text-grey")
            else:
                ui.label("请先完成初始化（创建根组），再建子组。").classes(
                    "text-caption text-warning"
                )

            fields["name"] = ui.input(
                "组名称",
                placeholder="例如：开发组",
            ).classes("w-full")
            fields["subnet"] = ui.input(
                "子网 CIDR",
                placeholder="例如：10.224.1.0/24",
            ).classes("w-full")
            fields["conflict"] = ui.label("").classes("text-caption text-negative")
            fields["conflict"].set_visibility(False)

            def on_subnet_change() -> None:
                subnet_val = str(fields["subnet"].value or "").strip()
                if not subnet_val:
                    fields["conflict"].set_visibility(False)
                    return
                config = load_config()
                conflicts = check_subnet_conflict(
                    subnet_val,
                    config.global_subnet or "",
                    self.group_service.list_all(),
                )
                fields["conflict"].text = "; ".join(conflicts)
                fields["conflict"].set_visibility(bool(conflicts))

            fields["subnet"].on("blur", on_subnet_change)

        form = create_form_dialog(
            "新建用户组",
            body=render_body,
            on_submit=lambda: self._create_group(
                str(fields["name"].value or "").strip(),
                str(fields["subnet"].value or "").strip(),
            ),
            submit_label="创建",
        )
        form.open()

    def _create_group(self, name: str, subnet: str) -> bool:
        """执行用户组创建并返回表单是否可关闭。"""

        if not name or not subnet:
            ui.notify("请填写完整信息", type="negative")
            return False
        try:
            self.group_service.create(name, subnet)
            ui.notify(f"用户组 {name} 已创建", type="positive")
            ui.navigate.to("/groups")
            return True
        except ValueError as exc:
            ui.notify(f"创建失败: {exc}", type="negative")
            return False

    def _do_create(self, dialog, name: str, subnet: str):
        """兼容旧调用：执行用户组创建。"""

        success = self._create_group(name, subnet)
        if success:
            dialog.close()
        return success

    def _show_edit_dialog(self, group: dict):
        """显示可同时修改名称与子网的编辑表单。"""

        group_id = str(group["id"])
        has_users = int(group.get("user_count") or 0) > 0
        all_groups = self.group_service.list_all()
        is_root = bool(all_groups and all_groups[0]["id"] == group_id)
        has_sub_groups = is_root and len(all_groups) > 1
        can_edit_subnet = not has_users and not has_sub_groups
        fields: dict[str, Any] = {}

        def render_body() -> None:
            fields["name"] = ui.input(
                "组名称",
                value=str(group.get("name") or ""),
            ).classes("w-full")
            fields["subnet"] = ui.input(
                "子网 CIDR",
                value=str(group.get("subnet") or ""),
            ).classes("w-full")

            if not can_edit_subnet:
                fields["subnet"].props("disable")
            if has_users:
                ui.label("组内有用户：可修改名称，但不能修改子网。").classes(
                    "text-caption text-warning"
                )
            if has_sub_groups:
                ui.label("根组存在子组：可修改名称，但需先删除子组才能修改根组子网。").classes(
                    "text-caption text-warning"
                )
            if is_root and can_edit_subnet:
                ui.label("修改根组子网会同步全局配置、实例配置与默认防火墙规则。").classes(
                    "text-caption text-grey"
                )

            fields["conflict"] = ui.label("").classes("text-caption text-negative")
            fields["conflict"].set_visibility(False)

            def on_subnet_change() -> None:
                subnet_val = str(fields["subnet"].value or "").strip()
                if not subnet_val or subnet_val == str(group.get("subnet") or ""):
                    fields["conflict"].set_visibility(False)
                    return
                if is_root:
                    from app.utils.cidr import validate_cidr

                    valid = validate_cidr(subnet_val)
                    fields["conflict"].text = "" if valid else f"子网格式不合法: {subnet_val}"
                    fields["conflict"].set_visibility(not valid)
                    return
                config = load_config()
                conflicts = check_subnet_conflict(
                    subnet_val,
                    config.global_subnet or "",
                    self.group_service.list_all(),
                    exclude_group_id=group_id,
                )
                fields["conflict"].text = "; ".join(conflicts)
                fields["conflict"].set_visibility(bool(conflicts))

            if can_edit_subnet:
                fields["subnet"].on("blur", on_subnet_change)

        form = create_form_dialog(
            "编辑用户组",
            body=render_body,
            on_submit=lambda: self._update_group_details(
                group_id,
                str(fields["name"].value or "").strip(),
                (
                    str(fields["subnet"].value or "").strip()
                    if can_edit_subnet
                    else str(group.get("subnet") or "")
                ),
            ),
        )
        form.open()

    def _update_group_details(self, group_id: str, name: str, subnet: str) -> bool:
        """保存用户组名称和子网。"""

        if not name:
            ui.notify("组名称不能为空", type="negative")
            return False
        if not subnet:
            ui.notify("子网不能为空", type="negative")
            return False
        try:
            self.group_service.update_details(group_id, name=name, subnet=subnet)
            ui.notify("用户组已更新", type="positive")
            ui.navigate.to("/groups")
            return True
        except ValueError as exc:
            ui.notify(f"修改失败: {exc}", type="negative")
            return False

    def _do_update_subnet(self, dialog, group_id: str, new_subnet: str, old_subnet: str):
        """兼容旧调用：仅修改子网。"""

        current = self.group_service.get(group_id)
        if not current:
            ui.notify("用户组不存在", type="negative")
            return False
        if new_subnet == old_subnet:
            dialog.close()
            return True
        success = self._update_group_details(
            group_id,
            str(current.get("name") or ""),
            new_subnet,
        )
        if success:
            dialog.close()
        return success

    def _confirm_disable(self, group: dict) -> None:
        """停用单个用户组前明确影响范围。"""

        name = str(group.get("name") or group.get("id") or "")
        confirm_dialog.show(
            f"确定停用用户组「{name}」？",
            impact="停用后该组不再作为可用分组提供给后续管理操作。",
            object_count=1,
            danger_level="warning",
            on_confirm=lambda: self._do_disable(str(group["id"])),
        )

    def _confirm_delete(self, group: dict):
        """删除用户组前的二次确认。"""

        name = str(group.get("name") or group["id"])
        user_count = int(group.get("user_count") or 0)
        if user_count > 0:
            ui.notify(f"「{name}」内有 {user_count} 个用户，无法删除", type="warning")
            return
        confirm_dialog.show(
            f"确定删除用户组「{name}」？",
            impact="删除后不可恢复；仅允许删除没有用户的非根组。",
            object_count=1,
            danger_level="danger",
            on_confirm=lambda: self._do_delete(str(group["id"])),
        )

    def _do_delete(self, group_id: str):
        """执行用户组删除。"""

        try:
            self.group_service.delete(group_id)
            ui.notify("用户组已删除", type="positive")
            ui.navigate.to("/groups")
        except ValueError as exc:
            ui.notify(f"删除失败: {exc}", type="negative")

    def _batch_delete_confirm(self):
        """批量删除前显示数量和影响范围。"""

        if not self.selected_ids:
            ui.notify("请先选择用户组", type="warning")
            return
        confirm_dialog.show(
            "确定删除所选用户组？",
            impact="根组或仍有用户的分组会被安全跳过并在结果中报告失败原因。",
            object_count=len(self.selected_ids),
            danger_level="danger",
            on_confirm=self._do_batch_delete,
        )

    def _do_batch_delete(self):
        """执行批量删除并汇总结果。"""

        results = self.group_service.bulk_delete(list(self.selected_ids))
        success = sum(1 for item in results if item["success"])
        failed = len(results) - success
        if failed:
            ui.notify(f"批量删除完成：成功 {success}，失败 {failed}", type="warning")
        else:
            ui.notify(f"成功删除 {success} 个用户组", type="positive")
        self.selected_ids.clear()
        ui.navigate.to("/groups")

    def _do_enable(self, group_id: str):
        """启用单个用户组。"""

        try:
            self.group_service.enable(group_id)
            ui.notify("用户组已启用", type="positive")
            ui.navigate.to("/groups")
        except ValueError as exc:
            ui.notify(f"操作失败: {exc}", type="negative")

    def _do_disable(self, group_id: str):
        """停用单个用户组。"""

        try:
            self.group_service.disable(group_id)
            ui.notify("用户组已停用", type="positive")
            ui.navigate.to("/groups")
        except ValueError as exc:
            ui.notify(f"操作失败: {exc}", type="negative")

    def _batch_enable(self):
        """批量启用所选用户组。"""

        if not self.selected_ids:
            ui.notify("请先选择用户组", type="warning")
            return
        results = self.group_service.bulk_enable(list(self.selected_ids))
        success = sum(1 for item in results if item["success"])
        failed = len(results) - success
        ui.notify(
            f"批量启用完成：成功 {success}，失败 {failed}",
            type="positive" if failed == 0 else "warning",
        )
        self.selected_ids.clear()
        ui.navigate.to("/groups")

    def _batch_disable_confirm(self):
        """批量停用前确认影响范围。"""

        if not self.selected_ids:
            ui.notify("请先选择用户组", type="warning")
            return
        confirm_dialog.show(
            "确定停用所选用户组？",
            impact="停用后这些分组将不再作为可用分组提供给后续管理操作。",
            object_count=len(self.selected_ids),
            danger_level="warning",
            on_confirm=self._batch_disable,
        )

    def _batch_disable(self):
        """批量停用所选用户组。"""

        if not self.selected_ids:
            ui.notify("请先选择用户组", type="warning")
            return
        results = self.group_service.bulk_disable(list(self.selected_ids))
        success = sum(1 for item in results if item["success"])
        failed = len(results) - success
        ui.notify(
            f"批量停用完成：成功 {success}，失败 {failed}",
            type="positive" if failed == 0 else "warning",
        )
        self.selected_ids.clear()
        ui.navigate.to("/groups")
