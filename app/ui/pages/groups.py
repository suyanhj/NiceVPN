# -*- coding: utf-8 -*-
"""组管理页面。"""

from nicegui import ui

from app.ui.copy_clipboard import copy_text_to_clipboard

from app.core.config import load_config
from app.services.group.crud import GroupService
from app.services.group.subnet import check_subnet_conflict
from app.ui.components import confirm_dialog


class GroupsPage:
    """组管理页面。"""

    def __init__(self):
        self.group_service = GroupService()
        self.selected_ids: set[str] = set()

    def render(self):
        """渲染组管理页面。"""
        groups = self.group_service.list_all()

        with ui.column().classes("page-shell mgmt-page"):
            with ui.element("section").classes("mgmt-panel"):
                with ui.element("div").classes("mgmt-header-row"):
                    with ui.element("div").classes("mgmt-header-copy"):
                        ui.label("组管理").classes("mgmt-title")
                        ui.label("子网划分、成员归属与组启停。").classes("mgmt-desc")

                    with ui.element("div").classes("mgmt-toolbar"):
                        ui.button("新建组", icon="group_add", on_click=self._show_create_dialog).props(
                            "unelevated no-caps no-ripple"
                        ).classes("mgmt-toolbar-btn is-primary")
                        ui.button("批量启用", icon="check_circle", on_click=self._batch_enable).props(
                            "outline no-caps no-ripple"
                        ).classes("mgmt-toolbar-btn is-outline is-enable")
                        ui.button("批量禁用", icon="block", on_click=self._batch_disable).props(
                            "outline no-caps no-ripple"
                        ).classes("mgmt-toolbar-btn is-outline is-disable")
                        ui.button("批量删除", icon="delete_sweep", on_click=self._batch_delete_confirm).props(
                            "outline no-caps no-ripple"
                        ).classes("mgmt-toolbar-btn is-outline is-danger")

            with ui.element("section").classes("mgmt-panel mgmt-panel-list"):
                with ui.element("div").classes("mgmt-list-head"):
                    with ui.row().classes("items-center"):
                        ui.label("分组列表").classes("mgmt-kicker")
                    ui.label(f"共 {len(groups)} 个组").classes("group-list-count")

                self._render_group_list(groups)

    def _render_group_list(self, groups: list[dict]):
        """渲染分组列表。"""
        if not groups:
            ui.label("暂无分组，点「新建组」添加。").classes("empty-state")
            return

        root_id = groups[0]["id"] if groups else ""
        with ui.element("div").classes("mgmt-record-list"):
            for group in groups:
                self._render_group_card(group, is_root=(group["id"] == root_id))

    def _render_group_card(self, group: dict, is_root: bool = False):
        """渲染单个分组卡片。"""
        group_id = group["id"]
        status = group.get("status", "active")
        is_active = status == "active"
        user_count = group.get("user_count", 0)

        with ui.element("div").classes("mgmt-record-card"):
            with ui.element("div").classes("mgmt-record-main"):
                ui.checkbox(
                    value=group_id in self.selected_ids,
                    on_change=lambda e, gid=group_id: self._toggle_select(gid, e.value),
                ).classes("mgmt-checkbox")

                with ui.element("div").classes("mgmt-record-copy"):
                    ui.label(group["name"]).classes("mgmt-record-title")
                    with ui.element("div").classes("mgmt-record-meta"):
                        with ui.element("span").classes("mgmt-meta-item"):
                            ui.icon("lan", size="14px")
                            ui.label(group.get("subnet", "未配置子网"))
                        with ui.element("span").classes("mgmt-meta-item"):
                            ui.icon("person", size="14px")
                            ui.label(f"用户数 {user_count}")

            with ui.element("div").classes("group-record-side hidden md:flex"):
                ui.label("ACTIVE" if is_active else "DISABLED").classes(
                    "group-status-badge" if is_active else "group-status-badge is-disabled"
                )
                with ui.row().classes("items-center gap-1 flex-wrap"):
                    ui.label(f"组 ID: {group_id}").classes("group-uuid-badge group-uuid-full")
                    ui.button(
                        icon="content_copy",
                        on_click=lambda value=group_id: self._copy_group_id(value),
                    ).props("flat dense round").tooltip("复制组 ID")

            with ui.element("div").classes("mgmt-actions"):
                ui.button(
                    icon="edit",
                    on_click=lambda current=group: self._show_edit_dialog(current),
                ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-edit").tooltip("编辑")

                if is_active:
                    ui.button(
                        icon="block",
                        on_click=lambda gid=group_id: self._do_disable(gid),
                    ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-toggle").tooltip("禁用")
                else:
                    ui.button(
                        icon="check_circle",
                        on_click=lambda gid=group_id: self._do_enable(gid),
                    ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-toggle").tooltip("启用")

                if not is_root:
                    ui.button(
                        icon="delete",
                        on_click=lambda current=group: self._confirm_delete(current),
                    ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-delete").tooltip("删除")

    def _toggle_select(self, group_id: str, selected: bool):
        """切换分组选中状态。"""
        if selected:
            self.selected_ids.add(group_id)
        else:
            self.selected_ids.discard(group_id)

    def _copy_group_id(self, group_id: str):
        """复制完整组 ID。"""
        copy_text_to_clipboard(group_id)
        ui.notify("组 ID 已复制", type="positive")

    def _show_create_dialog(self):
        """显示新建分组弹窗。"""
        existing_groups = self.group_service.list_all()
        root_group = existing_groups[0] if existing_groups else None

        with ui.dialog() as dialog, ui.card().classes("w-96"):
            ui.label("新建组").classes("text-h6")

            if root_group:
                ui.label(
                    f"子网须在根组「{root_group['name']}」"
                    f"（{root_group.get('subnet', '')}）内，例如 10.224.1.0/24。"
                ).classes("text-caption text-grey")
            else:
                ui.label(
                    "请先完成初始化（创建根组），再建子组。"
                ).classes("text-caption text-warning")

            name_input = ui.input("组名称", placeholder="例如: 开发组").classes("w-full")
            subnet_input = ui.input("子网 CIDR", placeholder="例如: 10.224.1.0/24").classes("w-full")

            conflict_label = ui.label("").classes("text-caption text-negative")
            conflict_label.set_visibility(False)

            def on_subnet_change():
                subnet_val = subnet_input.value.strip()
                if not subnet_val:
                    conflict_label.set_visibility(False)
                    return
                config = load_config()
                conflicts = check_subnet_conflict(
                    subnet_val,
                    config.global_subnet or "",
                    self.group_service.list_all(),
                )
                if conflicts:
                    conflict_label.text = "; ".join(conflicts)
                    conflict_label.set_visibility(True)
                else:
                    conflict_label.text = ""
                    conflict_label.set_visibility(False)

            subnet_input.on("blur", on_subnet_change)

            with ui.row().classes("justify-end q-mt-md"):
                ui.button("取消", on_click=dialog.close).props("flat")
                ui.button(
                    "创建",
                    on_click=lambda: self._do_create(dialog, name_input.value.strip(), subnet_input.value.strip()),
                ).props("color=primary")

        dialog.open()

    def _do_create(self, dialog, name: str, subnet: str):
        """执行分组创建。"""
        if not name or not subnet:
            ui.notify("请填写完整信息", type="negative")
            return
        try:
            self.group_service.create(name, subnet)
            dialog.close()
            ui.notify(f"组 {name} 已创建", type="positive")
            ui.navigate.to("/groups")
        except ValueError as exc:
            ui.notify(f"创建失败: {exc}", type="negative")

    def _show_edit_dialog(self, group: dict):
        """显示编辑弹窗。"""
        group_id = group["id"]
        has_users = group.get("user_count", 0) > 0
        all_groups = self.group_service.list_all()
        is_root = len(all_groups) > 0 and all_groups[0]["id"] == group_id
        has_sub_groups = is_root and len(all_groups) > 1

        with ui.dialog() as dialog, ui.card().classes("w-96"):
            ui.label("编辑组").classes("text-h6")
            ui.input("组名称", value=group["name"]).classes("w-full").props("readonly")

            subnet_input = ui.input("子网 CIDR", value=group.get("subnet", "")).classes("w-full")
            can_edit = not has_users and not has_sub_groups
            if not can_edit:
                subnet_input.props("disable")
            if has_users:
                ui.label("组内有用户，不能改子网。").classes("text-caption text-warning")
            if has_sub_groups:
                ui.label("存在子组：请先删子组，再改根组子网。").classes("text-caption text-warning")
            if is_root and can_edit:
                ui.label(
                    "改根组子网会同步全局配置与默认防火墙规则。"
                ).classes("text-caption text-grey")

            conflict_label = ui.label("").classes("text-caption text-negative")
            conflict_label.set_visibility(False)

            def on_subnet_change():
                subnet_val = subnet_input.value.strip()
                if not subnet_val or subnet_val == group.get("subnet", ""):
                    conflict_label.set_visibility(False)
                    return
                # 根组修改时不检测"是否在全局子网范围内"，因为根组本身就定义全局子网
                if is_root:
                    from app.utils.cidr import validate_cidr
                    if not validate_cidr(subnet_val):
                        conflict_label.text = f"子网格式不合法: {subnet_val}"
                        conflict_label.set_visibility(True)
                    else:
                        conflict_label.text = ""
                        conflict_label.set_visibility(False)
                    return
                config = load_config()
                conflicts = check_subnet_conflict(
                    subnet_val,
                    config.global_subnet or "",
                    self.group_service.list_all(),
                    exclude_group_id=group_id,
                )
                if conflicts:
                    conflict_label.text = "; ".join(conflicts)
                    conflict_label.set_visibility(True)
                else:
                    conflict_label.text = ""
                    conflict_label.set_visibility(False)

            subnet_input.on("blur", on_subnet_change)

            with ui.row().classes("justify-end q-mt-md"):
                ui.button("取消", on_click=dialog.close).props("flat")
                ui.button(
                    "保存",
                    on_click=lambda: self._do_update_subnet(
                        dialog,
                        group_id,
                        subnet_input.value.strip(),
                        group.get("subnet", ""),
                    ),
                ).props("color=primary")

        dialog.open()

    def _do_update_subnet(self, dialog, group_id: str, new_subnet: str, old_subnet: str):
        """执行子网修改。"""
        if new_subnet == old_subnet:
            dialog.close()
            return
        if not new_subnet:
            ui.notify("子网不能为空", type="negative")
            return
        try:
            self.group_service.update_subnet(group_id, new_subnet)
            dialog.close()
            ui.notify("子网修改成功", type="positive")
            ui.navigate.to("/groups")
        except ValueError as exc:
            ui.notify(f"修改失败: {exc}", type="negative")

    def _confirm_delete(self, group: dict):
        """删除分组前的二次确认。"""
        name = group.get("name", group["id"])
        user_count = group.get("user_count", 0)
        if user_count > 0:
            ui.notify(f"「{name}」内有 {user_count} 个用户，无法删除", type="warning")
            return
        confirm_dialog.show(
            f"删除「{name}」不可恢复，确定？",
            on_confirm=lambda: self._do_delete(group["id"]),
        )

    def _do_delete(self, group_id: str):
        """执行分组删除。"""
        try:
            self.group_service.delete(group_id)
            ui.notify("组已删除", type="positive")
            ui.navigate.to("/groups")
        except ValueError as exc:
            ui.notify(f"删除失败: {exc}", type="negative")

    def _batch_delete_confirm(self):
        """批量删除前的确认。"""
        if not self.selected_ids:
            ui.notify("请先勾选组", type="warning")
            return
        confirm_dialog.show(
            f"删除已选 {len(self.selected_ids)} 个组，不可恢复。确定？",
            on_confirm=self._do_batch_delete,
        )

    def _do_batch_delete(self):
        """执行批量删除。"""
        results = self.group_service.bulk_delete(list(self.selected_ids))
        success = sum(1 for item in results if item["success"])
        for item in results:
            if not item["success"]:
                ui.notify(f"组 {item['id'][:8]}... 删除失败: {item['error']}", type="warning")
        if success:
            ui.notify(f"成功删除 {success} 个组", type="positive")
        self.selected_ids.clear()
        ui.navigate.to("/groups")

    def _do_enable(self, group_id: str):
        """启用单个分组。"""
        try:
            self.group_service.enable(group_id)
            ui.notify("组已启用", type="positive")
            ui.navigate.to("/groups")
        except ValueError as exc:
            ui.notify(f"操作失败: {exc}", type="negative")

    def _do_disable(self, group_id: str):
        """禁用单个分组。"""
        try:
            self.group_service.disable(group_id)
            ui.notify("组已禁用", type="positive")
            ui.navigate.to("/groups")
        except ValueError as exc:
            ui.notify(f"操作失败: {exc}", type="negative")

    def _batch_enable(self):
        """批量启用选中的分组。"""
        if not self.selected_ids:
            ui.notify("请先勾选组", type="warning")
            return
        results = self.group_service.bulk_enable(list(self.selected_ids))
        success = sum(1 for item in results if item["success"])
        ui.notify(f"成功启用 {success} 个组", type="positive")
        self.selected_ids.clear()
        ui.navigate.to("/groups")

    def _batch_disable(self):
        """批量禁用选中的分组。"""
        if not self.selected_ids:
            ui.notify("请先勾选组", type="warning")
            return
        results = self.group_service.bulk_disable(list(self.selected_ids))
        success = sum(1 for item in results if item["success"])
        ui.notify(f"成功禁用 {success} 个组", type="positive")
        self.selected_ids.clear()
        ui.navigate.to("/groups")
