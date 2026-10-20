# -*- coding: utf-8 -*-
"""用户管理页面。"""

import logging
import threading
import re
from datetime import datetime

from nicegui import ui
from nicegui.elements.checkbox import Checkbox

logger = logging.getLogger(__name__)

from app.core.config import load_config
from app.services.openvpn.instance import get_status
from app.services.user.bulk_import import BulkImportService
from app.services.user.device_bind import DeviceBindingService
from app.ui.components import confirm_dialog
from app.ui.components.actions import (
    MenuAction,
    danger_action,
    more_actions,
    primary_action,
    secondary_action,
)
from app.ui.components.data_grid import render_data_grid
from app.ui.components.form_dialog import create_form_dialog
from app.ui.components.page_header import render_page_header
from app.ui.components.states import empty_state, error_state, no_match_state
from app.ui.components.status import status_badge, structured_value
from app.ui.components.toolbars import render_bulk_action_bar, render_page_toolbar
from app.ui.components.list_selection import (
    apply_row_checkboxes,
    sync_select_all_checkbox,
    update_visible_selection,
)
from app.ui.copy_clipboard import copy_text_to_clipboard
from app.services.user.crud import UserService


class UsersPage:
    """用户管理页面。"""

    def __init__(self):
        self.user_service = UserService()
        self.selected_users: set[str] = set()
        self.search_input = None
        self.data_container = None
        self.search_state_container = None
        self.grid_view = None
        self.all_grid_rows: list[dict] = []
        self.bulk_bar = None
        self.bulk_download_button = None
        self.bulk_enable_button = None
        self.bulk_more_menu = None

        # 兼容旧列表选择辅助与既有测试；AG Grid 迁移后不再作为主视图。
        self.list_container = None
        self.pagination_container = None
        self.select_all_checkbox = None
        self.visible_usernames: list[str] = []
        self.user_checkboxes: dict[str, Checkbox] = {}

        self._create_running = False
        self._create_result: dict | None = None
        self._create_timer = None
        self.page = 1
        self.page_size = 50

    def render(self):
        """渲染用户管理页面；数据密集集合统一使用 AG Grid。"""

        def search_region() -> None:
            self.search_input = (
                ui.input(placeholder="搜索用户名、虚拟 IP、组、证书或设备")
                .props("clearable")
                .classes("w-full")
            )
            self.search_input.on("keydown.enter", self._handle_search)
            ui.button("搜索", icon="search", on_click=self._handle_search).props(
                "outline no-caps color=primary"
            )
            ui.button("清除", icon="filter_alt_off", on_click=self._clear_search).props(
                "flat no-caps color=primary"
            )

        def primary_region() -> None:
            primary_action(
                "新建用户",
                icon="person_add",
                on_click=self._show_create_dialog,
            )

        def secondary_region() -> None:
            secondary_action(
                "批量导入",
                icon="upload_file",
                on_click=self._show_import_dialog,
            )
            secondary_action(
                "刷新",
                icon="refresh",
                on_click=self._refresh_user_list,
            )

        def bulk_actions() -> None:
            disabled = not self.selected_users
            self.bulk_download_button = secondary_action(
                "批量下载",
                icon="download",
                on_click=self._batch_download,
                disabled=disabled,
                tooltip="请先选择用户" if disabled else None,
            )
            self.bulk_enable_button = secondary_action(
                "批量启用",
                icon="toggle_on",
                on_click=lambda: self._batch_set_status(True),
                disabled=disabled,
                tooltip="请先选择用户" if disabled else None,
            )
            self.bulk_more_menu = more_actions(
                (
                    MenuAction("批量停用", icon="toggle_off", on_click=self._batch_disable_confirm),
                    MenuAction("批量踢下线", icon="power_settings_new", on_click=self._batch_kick_offline),
                    MenuAction("批量切换组", icon="drive_file_move", on_click=self._show_batch_change_group_dialog),
                    MenuAction("批量重置绑定", icon="phonelink_erase", on_click=self._batch_reset_binding_confirm),
                    MenuAction("批量删除", icon="delete", danger=True, on_click=self._batch_delete_confirm),
                ),
                disabled=disabled,
                tooltip="请先选择用户" if disabled else "更多批量操作",
            )

        with ui.column().classes("page-shell mgmt-page"):
            render_page_header(
                "用户管理",
                description="统一查看账号状态、在线会话、虚拟 IP、证书、流量与设备绑定，并执行配置和链接操作。",
            )
            render_page_toolbar(
                search=search_region,
                primary=primary_region,
                secondary=secondary_region,
            )
            self.bulk_bar = render_bulk_action_bar(
                len(self.selected_users),
                actions=bulk_actions,
            )
            self.data_container = ui.column().classes("w-full")

        self._refresh_user_list()

    def _load_user_grid_rows(self) -> tuple[list[dict], list[str]]:
        """聚合用户基础数据与可选运行数据，单个辅助数据源失败不阻断整页。"""

        users = self.user_service.list_all()
        warnings: list[str] = []

        try:
            live_sessions = self._load_live_sessions()
            live_available = True
        except Exception as exc:
            logger.exception("加载 OpenVPN 在线会话失败")
            live_sessions = {}
            live_available = False
            warnings.append(f"在线会话不可用: {exc}")

        try:
            binding_aux = DeviceBindingService().build_user_binding_aux()
        except Exception as exc:
            logger.exception("加载设备绑定辅助数据失败")
            binding_aux = {}
            warnings.append(f"设备绑定数据不可用: {exc}")

        try:
            ccd_vip_map = self.user_service.list_ccd_virtual_ipv4_by_username()
        except Exception as exc:
            logger.exception("加载 CCD 虚拟 IP 失败")
            ccd_vip_map = {}
            warnings.append(f"虚拟 IP 数据不可用: {exc}")

        try:
            from app.services.group.crud import GroupService

            group_names = {
                str(group.get("id") or ""): str(group.get("name") or "")
                for group in GroupService().list_all()
            }
        except Exception as exc:
            logger.exception("加载用户组名称失败")
            group_names = {}
            warnings.append(f"用户组名称不可用: {exc}")

        rows = [
            self._build_user_grid_row(
                user,
                live_sessions.get(str(user.get("username") or "")),
                binding_aux.get(str(user.get("username") or "")) or {},
                str(ccd_vip_map.get(str(user.get("username") or "")) or "").strip(),
                group_names,
                live_available=live_available,
            )
            for user in users
        ]
        return rows, warnings

    def _build_user_grid_row(
        self,
        user: dict,
        session: dict | None,
        binding_aux: dict[str, str],
        ccd_virtual_ip: str,
        group_names: dict[str, str],
        *,
        live_available: bool,
    ) -> dict:
        """把既有用户、会话和绑定信息标准化为 AG Grid 行。"""

        username = str(user.get("username") or "")
        group_id = str(user.get("group_id") or "")
        status = str(user.get("status") or "active")
        is_online = session is not None if live_available else False
        cert_serial = str(user.get("cert_serial") or "—")
        created_at = str(user.get("created_at") or "")[:10] or "未知"

        if ccd_virtual_ip:
            virtual_ip = ccd_virtual_ip
        elif session:
            virtual_ip = str(session.get("virtual_address") or "").strip() or "—"
        else:
            virtual_ip = "—"

        bytes_received = int(session.get("bytes_received", 0) or 0) if session else 0
        bytes_sent = int(session.get("bytes_sent", 0) or 0) if session else 0
        traffic = (
            f"↓{self._format_bytes(bytes_received)} ↑{self._format_bytes(bytes_sent)}"
            if session
            else "—"
        )
        connected_raw = str(session.get("connected_since") or "") if session else ""
        connection = (
            f"{self._format_connected_since(connected_raw)} · "
            f"{self._format_connection_duration(connected_raw)}"
            if session
            else (
                f"上次 {self._format_connected_since(str(binding_aux.get('last_connected_since') or ''))}"
                if binding_aux.get("last_connected_since")
                else "—"
            )
        )
        real_address = str(session.get("real_address") or "—") if session else "—"
        device = str(binding_aux.get("device_label") or "").strip() or "—"
        group_name = group_names.get(group_id) or (group_id[:8] if group_id else "—")

        return {
            "id": username,
            "username": username,
            "account_status": "启用" if status == "active" else "停用",
            "session_status": (
                "在线" if is_online else ("离线" if live_available else "未知")
            ),
            "virtual_ip": virtual_ip,
            "group": group_name,
            "group_id": group_id,
            "cert_serial": cert_serial,
            "cert_serial_display": self._short_serial(cert_serial),
            "traffic": traffic,
            "connection": connection,
            "source": real_address,
            "device": device,
            "created_at": created_at,
            "actions": "操作",
            "_status": status,
            "_online": is_online,
            "_live_available": live_available,
        }

    @staticmethod
    def _user_grid_columns() -> tuple[dict, ...]:
        """用户表格列定义；原始敏感/长值保留在行对象但不强制全部展示。"""

        return (
            {"field": "username", "headerName": "用户名", "pinned": "left", "minWidth": 150},
            {"field": "account_status", "headerName": "账号", "width": 92},
            {"field": "session_status", "headerName": "会话", "width": 92},
            {"field": "virtual_ip", "headerName": "虚拟 IP", "minWidth": 140, "cellClass": "vpn-grid-mono"},
            {"field": "group", "headerName": "用户组", "minWidth": 120},
            {"field": "cert_serial_display", "headerName": "证书序列号", "minWidth": 160, "cellClass": "vpn-grid-mono"},
            {"field": "traffic", "headerName": "流量", "minWidth": 160, "cellClass": "vpn-grid-mono"},
            {"field": "connection", "headerName": "连接时间", "minWidth": 190},
            {"field": "source", "headerName": "来源地址", "minWidth": 170, "cellClass": "vpn-grid-mono"},
            {"field": "device", "headerName": "设备", "minWidth": 140},
            {"field": "created_at", "headerName": "创建日期", "width": 120},
            {
                "field": "actions",
                "headerName": "操作",
                "pinned": "right",
                "width": 100,
                "sortable": False,
                "filter": False,
                "cellClass": "vpn-grid-action-cell",
            },
        )

    def _refresh_user_list(self):
        """刷新用户数据，同时保留仍存在的选择和搜索上下文。"""

        if self.data_container is None:
            return
        try:
            rows, warnings = self._load_user_grid_rows()
        except Exception as exc:
            logger.exception("加载用户列表失败")
            self.data_container.clear()
            with self.data_container:
                error_state(
                    str(exc),
                    title="用户列表加载失败",
                    on_retry=self._refresh_user_list,
                )
            return

        existing = {str(row.get("username") or "") for row in rows}
        self.selected_users.intersection_update(existing)
        self.all_grid_rows = rows

        self.data_container.clear()
        with self.data_container:
            self._render_user_grid(rows, warnings)
        self._sync_bulk_state()
        self._handle_search()

    def _render_user_grid(self, rows: list[dict], warnings: list[str]) -> None:
        """构建用户数据区及局部状态。"""

        if warnings:
            with ui.row().classes("w-full items-start gap-2 vpn-data-warning"):
                ui.icon("warning_amber", color="warning")
                ui.label("；".join(warnings)).classes("section-caption")

        if not rows:
            self.grid_view = None
            self.search_state_container = None
            empty_state(
                "暂无用户",
                description="创建用户或批量导入后会显示在这里。",
                action_label="新建用户",
                on_action=self._show_create_dialog,
                icon="person_off",
            )
            return

        self.search_state_container = ui.column().classes("w-full")
        self.grid_view = render_data_grid(
            self._user_grid_columns(),
            rows,
            page_size=self.page_size,
            selected_ids=self.selected_users,
            on_cell_click=self._on_grid_cell_clicked,
        )
        self.grid_view.summary.text = f"共 {len(rows)} 个用户"
        self.grid_view.grid.on("selectionChanged", self._on_grid_selection_changed)

    def _handle_search(self):
        """客户端快速筛选，同时在服务端镜像计算结果数与无匹配状态。"""

        if self.grid_view is None:
            return
        query = str(self.search_input.value or "").strip() if self.search_input else ""
        normalized = query.casefold()
        matched = [
            row
            for row in self.all_grid_rows
            if not normalized or self._row_matches_query(row, normalized)
        ]

        self.grid_view.grid.options["quickFilterText"] = query
        self.grid_view.grid.update()
        self.grid_view.summary.text = (
            f"匹配 {len(matched)} / 共 {len(self.all_grid_rows)} 个用户"
            if query
            else f"共 {len(self.all_grid_rows)} 个用户"
        )

        if self.search_state_container is not None:
            self.search_state_container.clear()
            if query and not matched:
                self.grid_view.container.set_visibility(False)
                with self.search_state_container:
                    no_match_state(
                        description=f"没有用户匹配“{query}”。",
                        on_clear=self._clear_search,
                    )
            else:
                self.grid_view.container.set_visibility(True)

    @staticmethod
    def _row_matches_query(row: dict, query: str) -> bool:
        fields = (
            "username",
            "virtual_ip",
            "group",
            "group_id",
            "cert_serial",
            "source",
            "device",
            "account_status",
            "session_status",
        )
        return any(query in str(row.get(field) or "").casefold() for field in fields)

    def _clear_search(self) -> None:
        if self.search_input is not None:
            self.search_input.set_value("")
        self._handle_search()

    async def _on_grid_selection_changed(self, _event=None) -> None:
        """把 AG Grid 当前选择同步到批量操作状态。"""

        if self.grid_view is None:
            return
        rows = await self.grid_view.get_selected_rows()
        self.selected_users = {
            str(row.get("username") or "")
            for row in rows
            if row.get("username")
        }
        self._sync_bulk_state()

    def _sync_bulk_state(self) -> None:
        if self.bulk_bar is not None:
            self.bulk_bar.set_selected_count(len(self.selected_users))
        enabled = bool(self.selected_users)
        for button in (self.bulk_download_button, self.bulk_enable_button):
            if button is None:
                continue
            if enabled:
                button.enable()
            else:
                button.disable()
        if self.bulk_more_menu is not None:
            if enabled:
                self.bulk_more_menu.button.enable()
            else:
                self.bulk_more_menu.button.disable()

    def _on_grid_cell_clicked(self, event) -> None:
        """点击固定操作列时打开当前用户操作面板。"""

        args = getattr(event, "args", None) or {}
        if not isinstance(args, dict):
            return
        column = args.get("colId")
        if column is None and isinstance(args.get("column"), dict):
            column = args["column"].get("colId")
        if column != "actions":
            return
        row = args.get("data") or {}
        if isinstance(row, dict) and row.get("username"):
            self._show_user_actions(row)

    def _show_user_actions(self, row: dict) -> None:
        """集中承载单用户高频与低频操作，避免表格被按钮挤占。"""

        username = str(row.get("username") or "")
        is_active = str(row.get("_status") or "") == "active"
        is_online = bool(row.get("_online"))

        with ui.dialog() as dialog, ui.card().classes("vpn-form-dialog"):
            with ui.row().classes("w-full items-start justify-between no-wrap"):
                with ui.column().classes("gap-1 min-w-0"):
                    ui.label(username).classes("vpn-form-dialog__title")
                    with ui.row().classes("items-center gap-2 flex-wrap"):
                        status_badge(
                            "running" if is_active else "disabled",
                            label="账号启用" if is_active else "账号停用",
                        )
                        status_badge(
                            "success" if is_online else "unknown",
                            label="在线" if is_online else "离线",
                            icon="wifi" if is_online else "wifi_off",
                        )
                ui.button(icon="close", on_click=dialog.close).props("flat round dense")

            with ui.column().classes("w-full gap-2"):
                structured_value(
                    row.get("virtual_ip") or "—",
                    kind="ip",
                    copyable=row.get("virtual_ip") not in {None, "", "—"},
                    copy_message="虚拟 IP 已复制",
                )
                structured_value(
                    row.get("cert_serial") or "—",
                    kind="serial",
                    copyable=row.get("cert_serial") not in {None, "", "—"},
                    copy_message="证书序列号已复制",
                )
                structured_value(
                    row.get("group_id") or "—",
                    kind="serial",
                    copyable=bool(row.get("group_id")),
                    copy_message="组 ID 已复制",
                )

            def invoke(callback):
                dialog.close()
                return callback()

            with ui.row().classes("w-full gap-2 flex-wrap"):
                if is_active:
                    secondary_action(
                        "停用",
                        icon="toggle_off",
                        on_click=lambda: invoke(lambda: self._confirm_user_disable(username)),
                    )
                else:
                    secondary_action(
                        "启用",
                        icon="toggle_on",
                        on_click=lambda: invoke(lambda: self._toggle_user_status(username)),
                    )
                secondary_action(
                    "踢下线",
                    icon="power_settings_new",
                    on_click=lambda: invoke(lambda: self._kick_offline(username)),
                    disabled=not is_online,
                    tooltip=None if is_online else "当前用户不在线",
                )
                secondary_action(
                    "切换组",
                    icon="drive_file_move",
                    on_click=lambda: invoke(lambda: self._show_change_group_dialog(username)),
                )
                secondary_action(
                    "下载配置",
                    icon="download",
                    on_click=lambda: invoke(lambda: self._download_ovpn(username)),
                )
                secondary_action(
                    "编辑配置",
                    icon="edit_note",
                    on_click=lambda: invoke(lambda: self._edit_ovpn(username)),
                )
                secondary_action(
                    "复制下载链接",
                    icon="link",
                    on_click=lambda: invoke(lambda: self._copy_download_link(username)),
                )
                secondary_action(
                    "推送下载链接",
                    icon="send",
                    on_click=lambda: invoke(lambda: self._push_download_link_notify(username)),
                )
                secondary_action(
                    "重置绑定",
                    icon="phonelink_erase",
                    on_click=lambda: invoke(lambda: self._confirm_reset_binding(username)),
                )
                danger_action(
                    "删除",
                    icon="delete",
                    on_click=lambda: invoke(lambda: self._confirm_delete(username)),
                )
        dialog.open()

    def _confirm_user_disable(self, username: str) -> None:
        confirm_dialog.show(
            f"确定停用用户 {username}？",
            impact="停用会更新 CCD 禁用状态，并终止该用户当前连接。",
            object_count=1,
            danger_level="warning",
            on_confirm=lambda: self._toggle_user_status(username),
        )

    def _batch_set_status(self, enabled: bool) -> None:
        """批量设置账号启用状态并汇总成功、跳过和失败。"""

        if not self.selected_users:
            ui.notify("请先选择用户", type="warning")
            return
        target = "active" if enabled else "disabled"
        success = skipped = failed = 0
        for username in list(self.selected_users):
            try:
                user = self.user_service.get(username)
                if not user:
                    failed += 1
                    continue
                if str(user.get("status") or "active") == target:
                    skipped += 1
                    continue
                self.user_service.toggle_status(username)
                success += 1
            except Exception:
                logger.exception("批量修改用户状态失败 user=%s target=%s", username, target)
                failed += 1
        ui.notify(
            f"批量{'启用' if enabled else '停用'}完成：成功 {success}，跳过 {skipped}，失败 {failed}",
            type="positive" if failed == 0 else "warning",
        )
        self._refresh_user_list()

    def _batch_disable_confirm(self) -> None:
        if not self.selected_users:
            ui.notify("请先选择用户", type="warning")
            return
        confirm_dialog.show(
            "确定停用所选用户？",
            impact="停用会更新所选用户的 CCD 禁用状态，并终止其当前连接。",
            object_count=len(self.selected_users),
            danger_level="warning",
            on_confirm=lambda: self._batch_set_status(False),
        )

    def _batch_kick_offline(self) -> None:
        if not self.selected_users:
            ui.notify("请先选择用户", type="warning")
            return
        success = skipped = failed = 0
        for username in list(self.selected_users):
            try:
                if self.user_service.kick_offline(username):
                    success += 1
                else:
                    skipped += 1
            except Exception:
                logger.exception("批量踢下线失败 user=%s", username)
                failed += 1
        ui.notify(
            f"批量下线完成：成功 {success}，跳过 {skipped}，失败 {failed}",
            type="positive" if failed == 0 else "warning",
        )
        self._refresh_user_list()

    def _show_batch_change_group_dialog(self) -> None:
        if not self.selected_users:
            ui.notify("请先选择用户", type="warning")
            return

        from app.services.group.crud import GroupService

        groups = [g for g in GroupService().list_all() if g.get("status") == "active"]
        if not groups:
            ui.notify("没有可用的启用用户组", type="warning")
            return
        options = {
            str(group["id"]): f"{group['name']} ({group['subnet']})"
            for group in groups
        }
        fields: dict[str, object] = {}

        def body() -> None:
            fields["group"] = ui.select(options, label="目标用户组").classes("w-full")
            ui.label(
                f"将对已选 {len(self.selected_users)} 个用户逐一切换；已在目标组的用户会跳过。"
            ).classes("section-caption")

        form = create_form_dialog(
            "批量切换用户组",
            body=body,
            submit_label="切换",
            on_submit=lambda: self._batch_change_group(
                str(getattr(fields["group"], "value", "") or "")
            ),
        )
        form.open()

    def _batch_change_group(self, group_id: str) -> bool:
        if not group_id:
            ui.notify("请选择目标用户组", type="warning")
            return False
        success = skipped = failed = 0
        for username in list(self.selected_users):
            try:
                user = self.user_service.get(username)
                if not user:
                    failed += 1
                    continue
                if str(user.get("group_id") or "") == group_id:
                    skipped += 1
                    continue
                self.user_service.change_group(username, group_id)
                success += 1
            except Exception:
                logger.exception("批量切换用户组失败 user=%s group=%s", username, group_id)
                failed += 1
        ui.notify(
            f"批量切换完成：成功 {success}，跳过 {skipped}，失败 {failed}",
            type="positive" if failed == 0 else "warning",
        )
        self._refresh_user_list()
        return True

    def _batch_delete_confirm(self) -> None:
        if not self.selected_users:
            ui.notify("请先选择用户", type="warning")
            return
        confirm_dialog.show(
            "确定删除所选用户？",
            impact="删除会吊销证书并清理用户配置，此操作不可恢复。",
            object_count=len(self.selected_users),
            danger_level="danger",
            on_confirm=self._batch_delete,
        )

    def _batch_delete(self) -> None:
        success = failed = 0
        for username in list(self.selected_users):
            try:
                self.user_service.delete(username)
                success += 1
            except Exception:
                logger.exception("批量删除用户失败 user=%s", username)
                failed += 1
        ui.notify(
            f"批量删除完成：成功 {success}，失败 {failed}",
            type="positive" if failed == 0 else "warning",
        )
        self.selected_users.clear()
        self._refresh_user_list()

    def _short_serial(self, cert_serial: str) -> str:
        """生成适合列表展示的短序列号。"""
        if not cert_serial or cert_serial == "—":
            return "—"
        if len(cert_serial) <= 16:
            return cert_serial
        return f"{cert_serial[:8]}...{cert_serial[-8:]}"

    def _load_live_sessions(self) -> dict[str, dict]:
        """聚合所有实例中的在线用户会话。"""
        config = load_config()
        sessions: dict[str, dict] = {}
        for instance_name in list(config.get("instances", {}).keys()):
            status = get_status(instance_name)
            for client in status.get("clients", []):
                username = client.get("common_name")
                if not username:
                    continue
                sessions[username] = dict(client)
        return sessions

    def _format_bytes(self, size: int) -> str:
        """将字节数转换为易读格式。"""
        value = float(size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if abs(value) < 1024:
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} PB"

    def _format_connected_since(self, value: str) -> str:
        """格式化 OpenVPN 的连接开始时间（status / time_ascii），展示为 年-月-日 时分。"""
        if not value:
            return "—"
        normalized = " ".join(value.strip().split())
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%a %b %d %H:%M:%S %Y",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
        ):
            try:
                return datetime.strptime(normalized, fmt).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                continue
        return normalized

    def _format_connection_duration(self, value: str) -> str:
        """计算连接时长（从 connected_since 到现在）。"""
        if not value:
            return "—"
        normalized = " ".join(value.strip().split())
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%a %b %d %H:%M:%S %Y",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
        ):
            try:
                start = datetime.strptime(normalized, fmt)
                delta = datetime.now() - start
                total_seconds = int(delta.total_seconds())
                if total_seconds < 0:
                    return "—"
                hours, remainder = divmod(total_seconds, 3600)
                minutes, _ = divmod(remainder, 60)
                if hours >= 24:
                    days = hours // 24
                    hours = hours % 24
                    return f"{days}天{hours}时{minutes}分"
                return f"{hours}时{minutes}分"
            except ValueError:
                continue
        return "—"

    def _toggle_user_status(self, username: str):
        """切换用户启用/停用状态。"""
        try:
            new_status = self.user_service.toggle_status(username)
            label = "已启用" if new_status == "active" else "已停用"
            ui.notify(f"用户 {username} {label}", type="positive")
            self._refresh_user_list()
        except Exception as exc:
            ui.notify(f"操作失败: {exc}", type="negative")

    def _kick_offline(self, username: str):
        """踢掉用户当前在线会话。"""
        try:
            self.user_service.kick_offline(username)
            ui.notify(f"已踢下线: {username}", type="positive")
            self._refresh_user_list()
        except Exception as exc:
            ui.notify(f"踢下线失败: {exc}", type="negative")

    def _show_change_group_dialog(self, username: str):
        """显示用户切换组弹窗。"""
        from app.services.group.crud import GroupService

        user = self.user_service.get(username)
        if not user:
            ui.notify(f"用户不存在: {username}", type="negative")
            return
        current_group_id = str(user.get("group_id") or "").strip()
        groups = [
            g for g in GroupService().list_all()
            if g.get("status") == "active" and str(g.get("id") or "") != current_group_id
        ]
        if not groups:
            ui.notify("没有可切换的启用用户组", type="warning")
            return

        group_options = {group["id"]: f"{group['name']} ({group['subnet']})" for group in groups}
        with ui.dialog() as dialog, ui.card().classes("w-96"):
            ui.label(f"切换用户组 — {username}").classes("text-h6")
            ui.label("切换后会重新分配虚拟 IP，并立即踢下线，用户重连后生效。").classes(
                "text-caption text-grey"
            )
            group_select = ui.select(group_options, label="目标组").classes("w-full").props(
                'behavior=menu popup-content-class="vpn-group-select-menu"'
            )

            with ui.row().classes("justify-end q-mt-md"):
                ui.button("取消", on_click=dialog.close).props("flat")
                ui.button(
                    "切换",
                    on_click=lambda: self._do_change_group(dialog, username, group_select.value),
                ).props("color=primary")
        dialog.open()

    def _do_change_group(self, dialog, username: str, new_group_id: str):
        """执行用户切换组。"""
        if not new_group_id:
            ui.notify("请选择目标组", type="negative")
            return
        try:
            result = self.user_service.change_group(username, new_group_id)
            dialog.close()
            if result.get("changed"):
                old_ip = result.get("old_virtual_ip") or "无"
                new_ip = result.get("new_virtual_ip") or "无"
                ui.notify(f"已切换 {username} 的用户组，虚拟 IP: {old_ip} -> {new_ip}", type="positive")
            else:
                ui.notify(f"{username} 已在目标组，无需切换", type="warning")
            self._refresh_user_list()
        except Exception as exc:
            ui.notify(f"切换组失败: {exc}", type="negative")

    def _download_ovpn(self, username: str):
        """直接下载用户的 .ovpn 配置文件。"""
        from app.services.user.bulk_download import BulkDownloadService
        ovpn_path = BulkDownloadService._find_ovpn(username)
        if not ovpn_path:
            ui.notify(f"用户 {username} 的 .ovpn 文件不存在", type="negative")
            return
        try:
            content = ovpn_path.read_bytes()
            ui.download(content, f"{username}.ovpn")
        except Exception as exc:
            ui.notify(f"下载失败: {exc}", type="negative")

    def _edit_ovpn(self, username: str):
        """打开编辑器修改用户 .ovpn 配置文件。"""
        from app.services.user.bulk_download import BulkDownloadService
        ovpn_path = BulkDownloadService._find_ovpn(username)
        if not ovpn_path:
            ui.notify(f"用户 {username} 的 .ovpn 文件不存在，请先确认已生成", type="negative")
            return
        try:
            content = ovpn_path.read_text(encoding="utf-8")
        except Exception as exc:
            ui.notify(f"读取文件失败: {exc}", type="negative")
            return

        with ui.dialog().props("position=right maximized") as dialog, ui.card().classes(
            "w-[min(100vw,48rem)] h-full no-wrap overflow-y-auto"
        ):
            ui.label(f"编辑 .ovpn 配置 — {username}").classes("text-h6")
            ui.label(f"文件路径: {ovpn_path}").classes("text-caption text-grey q-mb-sm")
            ui.label(
                "保存后对用户下次下载/使用该 .ovpn 生效；内嵌证书与密钥勿随意改动。"
            ).classes("text-caption text-grey")

            editor = ui.textarea(value=content).classes("w-full").props(
                'outlined rows=30 input-style="font-family: Consolas, monospace; font-size: 12px; line-height: 1.5;"'
            )

            with ui.row().classes("justify-end q-mt-md"):
                ui.button("取消", on_click=dialog.close).props("flat")
                ui.button(
                    "保存",
                    on_click=lambda: self._save_ovpn(dialog, username, ovpn_path, editor.value),
                ).props("color=primary")
        dialog.open()

    def _save_ovpn(self, dialog, username: str, ovpn_path, new_content: str):
        """保存编辑后的 .ovpn 配置文件。"""
        if not new_content or not new_content.strip():
            ui.notify("内容不能为空", type="negative")
            return
        try:
            ovpn_path.write_text(new_content, encoding="utf-8")
            dialog.close()
            ui.notify(f"用户 {username} 的 .ovpn 配置已保存", type="positive")
        except Exception as exc:
            ui.notify(f"保存失败: {exc}", type="negative")

    async def _copy_text(self, text: str, message: str):
        """复制文本并提示。"""
        if not text or text == "—":
            ui.notify("没有可复制的内容", type="warning")
            return
        if await copy_text_to_clipboard(text):
            ui.notify(message, type="positive")

    def _toggle_select(self, username: str, selected: bool):
        """切换用户选中状态。"""
        if selected:
            self.selected_users.add(username)
        else:
            self.selected_users.discard(username)
        self._sync_select_all()

    def _toggle_select_all(self, selected: bool):
        """选择或取消选择当前页全部用户。"""
        update_visible_selection(self.selected_users, self.visible_usernames, selected)
        apply_row_checkboxes(self.user_checkboxes, self.selected_users)
        self._sync_select_all()

    def _sync_select_all(self):
        """同步用户列表全选复选框。"""
        if self.select_all_checkbox is not None:
            sync_select_all_checkbox(
                self.select_all_checkbox,
                self.visible_usernames,
                self.selected_users,
            )

    def _confirm_reset_binding(self, username: str):
        """单用户重置设备绑定确认。"""
        confirm_dialog.show(
            f"重置后 {username} 下次接入视为新设备。确定重置绑定？",
            on_confirm=lambda: self._do_reset_binding(username),
            title="重置设备绑定",
        )

    def _do_reset_binding(self, username: str):
        """执行单用户设备绑定重置。"""
        svc = DeviceBindingService()
        if svc.reset_binding(username):
            ui.notify(f"已重置 {username} 的设备绑定", type="positive")
        else:
            ui.notify(f"{username} 无绑定记录", type="warning")

    def _batch_reset_binding_confirm(self):
        """批量重置选中用户的设备绑定。"""
        if not self.selected_users:
            ui.notify("请先勾选用户", type="warning")
            return
        confirm_dialog.show(
            f"确定重置已选 {len(self.selected_users)} 个用户的设备绑定？",
            on_confirm=self._batch_reset_binding,
            title="批量重置绑定",
        )

    def _batch_reset_binding(self):
        """批量重置设备绑定。"""
        svc = DeviceBindingService()
        ok = 0
        for name in list(self.selected_users):
            if svc.reset_binding(name):
                ok += 1
        ui.notify(f"已处理 {ok} 个绑定记录", type="positive")
        self.selected_users.clear()
        self._refresh_user_list()

    def _show_create_dialog(self):
        """显示新建用户弹窗。"""
        from app.services.group.crud import GroupService

        groups = GroupService().list_all()

        with ui.dialog() as dialog, ui.card().classes("w-96"):
            ui.label("新建用户").classes("text-h6")
            username_input = ui.input("用户名", placeholder="例如: lisi").classes("w-full")
            count_input = ui.number("创建数量", value=1, min=1, max=500, step=1, format="%.0f").classes(
                "w-full"
            )
            ui.label("数量>1 时自动生成后缀：lisi、lisi_1、lisi_2 …").classes("text-caption text-grey")

            group_options = {group["id"]: f"{group['name']} ({group['subnet']})" for group in groups}
            group_select = ui.select(group_options, label="所属组").classes("w-full").props(
                'behavior=menu popup-content-class="vpn-group-select-menu"'
            )

            password_enabled = ui.checkbox("启用账号密码")
            password_input = ui.input("密码", password=True).classes("w-full")
            password_input.bind_visibility_from(password_enabled, "value")

            with ui.row().classes("justify-end q-mt-md"):
                ui.button("取消", on_click=dialog.close).props("flat")
                ui.button(
                    "创建",
                    on_click=lambda: self._do_create(
                        dialog,
                        username_input.value,
                        int(count_input.value or 1),
                        group_select.value,
                        password_enabled.value,
                        password_input.value,
                    ),
                ).props("color=primary")

        dialog.open()

    def _do_create(self, dialog, username: str, count: int, group_id: str, password_enabled: bool, password: str):
        """执行用户创建（支持批量命名）。"""
        if self._create_running:
            ui.notify("用户正在后台创建，请稍候", type="warning")
            return
        if not username or not group_id:
            ui.notify("请填写完整信息", type="negative")
            return
        if count < 1:
            ui.notify("创建数量无效", type="negative")
            return
        if count == 1:
            names = [username.strip()]
        else:
            base = username.strip()
            names = [base] + [f"{base}_{i}" for i in range(1, count)]

        self._create_running = True
        self._create_result = None
        dialog.close()
        ui.notify(f"已开始后台创建 {len(names)} 个用户，请稍候", type="info")
        logger.info("后台用户创建任务已启动 count=%s first_user=%s", len(names), names[0])

        def worker():
            created = []
            current_name = ""
            try:
                for name in names:
                    current_name = name
                    logger.info("后台创建用户开始 user=%s", name)
                    self.user_service.create(
                        username=name,
                        group_id=group_id,
                        password_enabled=password_enabled,
                        password=password if password_enabled else None,
                    )
                    created.append(name)
                    logger.info("后台创建用户完成 user=%s", name)
                self._create_result = {"ok": True, "created": created}
            except Exception as exc:
                logger.exception("后台创建用户失败 user=%s", current_name)
                self._create_result = {
                    "ok": False,
                    "created": created,
                    "error": f"创建 {current_name} 失败: {exc}",
                }

        threading.Thread(target=worker, daemon=True, name="user-create-worker").start()
        self._create_timer = ui.timer(0.3, self._poll_create_progress)

    def _poll_create_progress(self):
        """轮询后台用户创建结果，并在 UI 线程刷新页面。"""
        if self._create_result is None:
            return

        if self._create_timer is not None:
            if hasattr(self._create_timer, "deactivate"):
                self._create_timer.deactivate()
            elif hasattr(self._create_timer, "active"):
                self._create_timer.active = False
            self._create_timer = None

        self._create_running = False
        result = self._create_result
        self._create_result = None
        created = result.get("created") or []

        if result.get("ok"):
            ui.notify(f"已创建 {len(created)} 个用户", type="positive")
            if created:
                self._copy_download_link(created[0])
                # 延后跳转，避免页面卸载导致剪贴板脚本未执行
                ui.timer(0.25, lambda: ui.navigate.to("/users"), once=True)
            else:
                ui.navigate.to("/users")
            return

        if created:
            ui.notify(f"已创建 {len(created)} 个用户，后续创建中断", type="warning")
        ui.notify(str(result.get("error") or "创建失败"), type="negative")
        self._refresh_user_list()

    def _ensure_one_time_download_url(self, username: str) -> str | None:
        """
        生成一次性下载 URL；失败时已向用户 notify，并打日志。

        Returns:
            完整下载 URL，不可生成时 None
        """
        config = load_config()
        user_data = self.user_service.get(username)
        if not user_data or not user_data.get("ovpn_file_path"):
            ui.notify("用户配置文件不存在", type="negative")
            logger.warning("一次性下载链接：用户 %s 无 ovpn 路径", username)
            return None

        from app.services.download.link_mgr import create_link
        from app.utils.public_base_url import get_ui_request, resolve_download_base_url

        req = get_ui_request()
        base_url = resolve_download_base_url(req, config.download_base_url)
        if not base_url:
            ui.notify(
                "无法生成下载链接：未解析到可达地址或仅监听本机。请到「系统设置」→「下载配置」填写基础 URL，或用局域网 IP 打开控制台。",
                type="warning",
            )
            logger.warning("一次性下载链接：用户 %s 无法解析 base_url", username)
            return None

        try:
            url = create_link(
                username=username,
                ovpn_path=user_data["ovpn_file_path"],
                base_url=base_url,
            )
        except Exception as exc:
            logger.exception("一次性下载链接生成失败 user=%s", username)
            ui.notify(f"生成下载链接失败: {exc}", type="negative")
            return None

        logger.info("已生成一次性下载链接 user=%s", username)
        return url

    def _copy_download_link(self, username: str):
        """生成一次性链接并写入剪贴板，不弹窗。"""
        url = self._ensure_one_time_download_url(username)
        if not url:
            return
        self._copy_text(
            url,
            "下载链接已复制（约 1 小时内有效，仅可下载一次）",
        )

    def _push_download_link_notify(self, username: str):
        """按系统配置的通知通道推送下载链接；无现成链接则现场生成。"""
        url = self._ensure_one_time_download_url(username)
        if not url:
            return

        from app.services.notify import send_download_link

        success = send_download_link(username, url)
        if success:
            ui.notify(f"已推送下载链接: {username}", type="positive")
            logger.info("notify 下载链接推送成功 user=%s", username)
        else:
            ui.notify(
                "推送失败：请确认「系统设置 → 通知」已启用并选对通道（钉钉 / 企业微信 / Rocket.Chat 需配置 Webhook）",
                type="negative",
            )
            logger.warning("notify 下载链接推送失败 user=%s（详情见审计日志）", username)

    def _confirm_delete(self, username: str):
        """删除用户前的二次确认。"""
        confirm_dialog.show(
            f"删除将吊销证书并清空配置，不可恢复。确定删除 {username}？",
            on_confirm=lambda: self._do_delete(username),
        )

    def _do_delete(self, username: str):
        """执行用户删除。"""
        try:
            self.user_service.delete(username)
            ui.notify(f"用户 {username} 已删除", type="positive")
            ui.navigate.to("/users")
        except Exception as exc:
            ui.notify(f"删除失败: {exc}", type="negative")

    def _batch_download(self):
        """批量下载选中用户的 VPN 文件（ZIP 按时间戳命名）。"""
        if not self.selected_users:
            ui.notify("请先勾选要下载的用户", type="warning")
            return

        from app.services.user.bulk_download import BulkDownloadService

        service = BulkDownloadService()
        zip_bytes, warnings, zip_filename = service.create_zip(list(self.selected_users))

        for warning in warnings:
            ui.notify(f"跳过: {warning}", type="warning")

        if zip_bytes:
            ui.download(zip_bytes, zip_filename)
        else:
            ui.notify("没有可下载的文件", type="negative")

    def _show_import_dialog(self):
        """显示批量导入弹窗：支持 CSV/TXT 文件及可选创建数量。"""
        from app.services.group.crud import GroupService

        with ui.dialog() as dialog, ui.card().classes("min-w-[480px]"):
            ui.label("批量导入用户").classes("text-h6")
            ui.label(
                "每行：用户名 + 组名 + 可选数量，空格或逗号分隔。数量省略时为 1，组不存在则整批不导入。"
            ).classes("section-caption")

            textarea = ui.textarea(
                label="粘贴内容（或上传文件后自动填入）",
                placeholder="zhangsan default_group\nlisi dev_group 3",
            ).classes("w-full").props('rows="8"')

            async def handle_upload(e):
                # NiceGUI 3.x：上传内容为 e.file（FileUpload），read/text 为异步
                try:
                    data = await e.file.read()
                    content = data.decode("utf-8")
                    textarea.set_value(content)
                    ui.notify(f"已载入文件: {e.file.name}", type="positive")
                except Exception as exc:
                    fname = getattr(e.file, "name", "")
                    logger.exception("批量导入：读取上传文件失败 file=%s", fname)
                    ui.notify(f"读取上传文件失败 ({fname}): {exc}", type="negative")
                    raise RuntimeError(f"读取上传文件失败: {exc}") from exc

            ui.upload(
                label="或上传 CSV/TXT 文件",
                on_upload=handle_upload,
                auto_upload=True,
                max_files=1,
            ).props('accept=".csv,.txt,.text"').classes("w-full q-mt-sm")

            with ui.row().classes("justify-end q-mt-md"):
                ui.button("取消", on_click=dialog.close).props("flat")
                ui.button(
                    "预检查并导入",
                    icon="playlist_add_check",
                    on_click=lambda: self._do_import(dialog, textarea.value, GroupService()),
                ).props("color=primary")
        dialog.open()

    def _do_import(self, dialog, raw_text: str, group_service):
        """展开并预检查全部导入用户，通过后执行创建。"""
        if not raw_text or not raw_text.strip():
            ui.notify("导入内容为空", type="warning")
            return

        service = BulkImportService()
        try:
            rows = service.parse_text(raw_text)
        except ValueError as exc:
            logger.error("批量导入内容解析失败: %s", exc)
            ui.notify(str(exc), type="negative")
            return

        all_groups = group_service.list_all()
        group_ref_to_id = {group["name"]: group["id"] for group in all_groups}
        group_ref_to_id.update({group["id"]: group["id"] for group in all_groups})
        existing_users = {
            user["username"]
            for user in self.user_service.list_all()
        }
        validation = service.validate_all(rows, group_ref_to_id, existing_users)
        if not validation.valid:
            logger.error("批量导入预检查失败 errors=%s", validation.errors)
            for error in validation.errors[:5]:
                ui.notify(error, type="negative")
            if len(validation.errors) > 5:
                ui.notify(
                    f"还有 {len(validation.errors) - 5} 条错误未显示",
                    type="warning",
                )
            return

        created: list[str] = []
        for row in rows:
            username = row["username"]
            try:
                self.user_service.create(
                    username=username,
                    group_id=group_ref_to_id[row["group"]],
                )
                created.append(username)
            except Exception as exc:
                logger.exception(
                    "批量导入用户创建失败 user=%s created_count=%s",
                    username,
                    len(created),
                )
                ui.notify(
                    f"创建 {username} 失败，已创建 {len(created)} 个用户: {exc}",
                    type="negative",
                )
                raise RuntimeError(f"批量导入在创建 {username} 时失败") from exc

        dialog.close()
        logger.info(
            "批量导入用户完成 created_count=%s first_user=%s",
            len(created),
            created[0],
        )
        ui.notify(
            f"成功导入 {len(created)} 个用户",
            type="positive",
        )
        ui.navigate.to("/users")
