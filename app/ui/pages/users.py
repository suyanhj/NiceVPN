# -*- coding: utf-8 -*-
"""用户管理页面。"""

import logging
import threading
import re
from datetime import datetime

from nicegui import background_tasks, ui
from nicegui.elements.checkbox import Checkbox

logger = logging.getLogger(__name__)

from app.core.config import load_config
from app.services.openvpn.instance import get_status
from app.services.user.bulk_import import BulkImportService
from app.services.user.device_bind import DeviceBindingService
from app.ui.components import confirm_dialog
from app.ui.components.data_grid import render_data_grid
from app.ui.components.actions import create_action
from app.ui.components.data_grid_renderers import (
    ACTION_BUTTON_RENDERER,
    STATUS_PILL_RENDERER,
)
from app.ui.components.form_dialog import create_form_dialog
from app.ui.components.states import empty_state, error_state
from app.ui.components.status import status_badge, structured_value
from app.ui.components.toolbars import render_bulk_action_bar
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
        self.data_container = None
        self.grid_view = None
        self.all_grid_rows: list[dict] = []
        self.bulk_bar = None
        self.bulk_download_button = None
        self.bulk_enable_button = None
        self.bulk_icon_buttons: list[object] = []

        # 兼容旧列表选择辅助与既有测试；AG Grid 迁移后不再作为主视图。
        self.list_container = None
        self.pagination_container = None
        self.select_all_checkbox = None
        self.visible_usernames: list[str] = []
        self.user_checkboxes: dict[str, Checkbox] = {}

        self._create_running = False
        self._create_result: dict | None = None
        self._create_timer = None
        self._create_progress: dict[str, object] = {}
        self._create_progress_ui: dict[str, object] | None = None

        self._import_running = False
        self._import_result: dict | None = None
        self._import_timer = None
        self._import_progress: dict[str, object] = {}
        self._import_progress_ui: dict[str, object] | None = None
        self.page = 1
        self.page_size = 50

    def render(self):
        """渲染用户管理页面；页级动作与批量动作统一收敛到单条操作栏。"""

        def bulk_actions() -> None:
            disabled = not self.selected_users

            def icon_action(
                icon: str,
                tooltip: str,
                callback,
                *,
                disabled_action: bool = False,
                danger: bool = False,
                track_bulk: bool = False,
            ):
                classes = "users-bar-icon-action"
                if danger:
                    classes += " users-bar-icon-action--danger"
                button = ui.button(
                    icon=icon,
                    on_click=callback,
                ).props(
                    f"flat round dense aria-label='{tooltip}'"
                ).classes(classes)
                button.tooltip(
                    "请先选择用户"
                    if disabled_action and disabled
                    else tooltip
                )
                if disabled_action and disabled:
                    button.disable()
                if track_bulk:
                    self.bulk_icon_buttons.append(button)
                return button

            create_button = create_action(
                "新建用户",
                icon="person_add",
                on_click=self._show_create_dialog,
                tooltip="新建用户",
                icon_only=True,
            )
            create_button.props("round dense")
            create_button.classes(
                add="users-bar-icon-action users-bar-icon-action--create"
            )
            icon_action(
                "upload_file",
                "批量导入",
                self._show_import_dialog,
            )
            icon_action(
                "refresh",
                "刷新用户列表",
                self._refresh_user_list,
            )
            ui.element("span").classes("users-action-separator")

            self.bulk_icon_buttons = []
            self.bulk_download_button = icon_action(
                "download",
                "批量下载",
                self._batch_download,
                disabled_action=True,
            )
            self.bulk_enable_button = icon_action(
                "toggle_on",
                "批量启用",
                lambda: self._batch_set_status(True),
                disabled_action=True,
            )
            icon_action(
                "toggle_off",
                "批量停用",
                self._batch_disable_confirm,
                disabled_action=True,
                track_bulk=True,
            )
            icon_action(
                "power_settings_new",
                "批量踢下线",
                self._batch_kick_offline,
                disabled_action=True,
                track_bulk=True,
            )
            icon_action(
                "drive_file_move",
                "批量切换组",
                self._show_batch_change_group_dialog,
                disabled_action=True,
                track_bulk=True,
            )
            icon_action(
                "phonelink_erase",
                "批量重置绑定",
                self._batch_reset_binding_confirm,
                disabled_action=True,
                track_bulk=True,
            )
            icon_action(
                "delete",
                "批量删除",
                self._batch_delete_confirm,
                disabled_action=True,
                danger=True,
                track_bulk=True,
            )

        with ui.column().classes("page-shell mgmt-page users-page--v2"):
            self.bulk_bar = render_bulk_action_bar(
                len(self.selected_users),
                actions=bulk_actions,
            )
            self.bulk_bar.container.classes(add="users-bulk-bar")
            self.data_container = ui.column().classes(
                "w-full users-data-region"
            )

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
            "account_status_tone": "success" if status == "active" else "warning",
            "session_status": (
                "在线" if is_online else ("离线" if live_available else "未知")
            ),
            "session_status_tone": (
                "success" if is_online else ("unknown" if live_available else "warning")
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
            "device_filter": device if device != "—" else "未知",
            "device_source": (
                f"{device} · {real_address}"
                if device != "—" and real_address != "—"
                else (device if device != "—" else real_address)
            ),
            "created_at": created_at,
            "actions": "操作",
            "_status": status,
            "_online": is_online,
            "_live_available": live_available,
        }

    @staticmethod
    def _user_grid_columns() -> tuple[dict, ...]:
        """用户主表保持紧凑；长值和低频字段放入操作弹窗。"""

        return (
            {
                "field": "username",
                "headerName": "用户名",
                "pinned": "left",
                "lockPinned": True,
                "suppressMovable": True,
                "width": 142,
                "minWidth": 120,
                "maxWidth": 172,
                "cellClass": "vpn-grid-primary-cell",
            },
            {
                "field": "account_status",
                "headerName": "账号",
                "width": 94,
                "minWidth": 90,
                "maxWidth": 108,
                ":cellRenderer": STATUS_PILL_RENDERER,
                ":filter": "window.NiceVpnSetFilter",
                "filterParams": {
                    "searchPlaceholder": "搜索账号状态...",
                    "selectAllLabel": "(全选)",
                    "noMatchesLabel": "没有匹配状态",
                    "valueOrder": ["启用", "停用"],
                },
                "cellClass": "vpn-grid-renderer-cell",
            },
            {
                "field": "session_status",
                "headerName": "会话",
                "width": 94,
                "minWidth": 90,
                "maxWidth": 108,
                ":cellRenderer": STATUS_PILL_RENDERER,
                ":filter": "window.NiceVpnSetFilter",
                "filterParams": {
                    "searchPlaceholder": "搜索会话状态...",
                    "selectAllLabel": "(全选)",
                    "noMatchesLabel": "没有匹配状态",
                    "valueOrder": ["在线", "离线", "未知"],
                },
                "cellClass": "vpn-grid-renderer-cell",
            },
            {
                "field": "virtual_ip",
                "headerName": "虚拟 IP",
                "flex": 0.82,
                "minWidth": 122,
                "cellClass": "vpn-grid-mono",
            },
            {
                "field": "group",
                "headerName": "用户组",
                "flex": 0.78,
                "minWidth": 110,
                ":filter": "window.NiceVpnSetFilter",
                "filterParams": {
                    "searchPlaceholder": "搜索用户组...",
                    "selectAllLabel": "(全选)",
                    "noMatchesLabel": "没有匹配用户组",
                },
            },
            {
                "field": "traffic",
                "headerName": "流量",
                "flex": 0.88,
                "minWidth": 132,
                "cellClass": "vpn-grid-mono",
            },
            {
                "field": "connection",
                "headerName": "连接时间",
                "flex": 1.04,
                "minWidth": 168,
            },
            {
                "field": "device_source",
                "headerName": "设备 / 来源",
                "flex": 1.15,
                "minWidth": 188,
                ":filter": "window.NiceVpnSetFilter",
                "filterParams": {
                    "filterValueField": "device_filter",
                    "searchPlaceholder": "搜索设备类型...",
                    "selectAllLabel": "(全选)",
                    "noMatchesLabel": "没有匹配设备",
                    "valueOrder": ["安卓", "iOS", "Mac", "Windows", "Linux", "未知"],
                },
            },
            {
                "field": "actions",
                "headerName": "操作",
                "pinned": "right",
                "lockPinned": True,
                "suppressMovable": True,
                "width": 92,
                "minWidth": 92,
                "maxWidth": 92,
                "sortable": False,
                "filter": False,
                "resizable": False,
                ":cellRenderer": ACTION_BUTTON_RENDERER,
                "cellClass": "vpn-grid-renderer-cell vpn-grid-action-cell",
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

    def _render_user_grid(self, rows: list[dict], warnings: list[str]) -> None:
        """构建用户数据区及局部状态。"""

        if warnings:
            with ui.row().classes("w-full items-start gap-2 vpn-data-warning"):
                ui.icon("warning_amber", color="warning")
                ui.label("；".join(warnings)).classes("section-caption")

        if not rows:
            self.grid_view = None
            empty_state(
                "暂无用户",
                description="创建用户或批量导入后会显示在这里。",
                action_label="新建用户",
                on_action=self._show_create_dialog,
                icon="person_off",
            )
            return

        self.grid_view = render_data_grid(
            self._user_grid_columns(),
            rows,
            page_size=self.page_size,
            selected_ids=self.selected_users,
            on_cell_click=self._on_grid_cell_clicked,
            suppress_horizontal_scroll=True,
        )
        self.grid_view.summary.set_visibility(False)
        self.grid_view.grid.on("selectionChanged", self._on_grid_selection_changed)


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
        for button in self.bulk_icon_buttons:
            if enabled:
                button.enable()
            else:
                button.disable()

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
        """集中承载单用户操作；主表只保留一个稳定操作入口。"""

        username = str(row.get("username") or "")
        is_active = str(row.get("_status") or "") == "active"
        is_online = bool(row.get("_online"))

        with ui.dialog() as dialog, ui.card().classes(
            "vpn-form-dialog users-action-dialog"
        ):
            with ui.row().classes("w-full items-start justify-between no-wrap"):
                with ui.column().classes("gap-1 min-w-0"):
                    ui.label(username).classes(
                        "vpn-form-dialog__title users-action-dialog__title"
                    )
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
                ui.button(
                    icon="close",
                    on_click=dialog.close,
                ).props("flat round dense").classes("users-action-dialog__close")

            with ui.column().classes("w-full gap-2 users-action-dialog__details"):
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
                ui.label(
                    f"设备：{row.get('device') or '—'} · "
                    f"来源：{row.get('source') or '—'} · "
                    f"创建：{row.get('created_at') or '未知'}"
                ).classes("section-caption users-action-dialog__meta")

            def invoke(callback):
                dialog.close()
                return callback()

            def icon_action(
                icon: str,
                tooltip: str,
                callback,
                *,
                disabled: bool = False,
                danger: bool = False,
            ):
                classes = "users-dialog-icon-action"
                if danger:
                    classes += " users-dialog-icon-action--danger"
                button = ui.button(
                    icon=icon,
                    on_click=lambda: invoke(callback),
                ).props(
                    f"flat round dense aria-label='{tooltip}'"
                ).classes(classes)
                button.tooltip(tooltip)
                if disabled:
                    button.disable()
                return button

            with ui.row().classes(
                "w-full items-center gap-1 flex-wrap users-action-dialog__actions"
            ):
                if is_active:
                    icon_action(
                        "toggle_off",
                        "停用",
                        lambda: self._confirm_user_disable(username),
                    )
                else:
                    icon_action(
                        "toggle_on",
                        "启用",
                        lambda: self._toggle_user_status(username),
                    )
                icon_action(
                    "power_settings_new",
                    "踢下线",
                    lambda: self._kick_offline(username),
                    disabled=not is_online,
                )
                icon_action(
                    "drive_file_move",
                    "切换组",
                    lambda: self._show_change_group_dialog(username),
                )
                icon_action(
                    "download",
                    "下载配置",
                    lambda: self._download_ovpn(username),
                )
                icon_action(
                    "edit_note",
                    "编辑配置",
                    lambda: self._edit_ovpn(username),
                )
                icon_action(
                    "link",
                    "复制下载链接",
                    lambda: self._copy_download_link(username),
                )
                icon_action(
                    "send",
                    "推送下载链接",
                    lambda: self._push_download_link_notify(username),
                )
                icon_action(
                    "phonelink_erase",
                    "重置绑定",
                    lambda: self._confirm_reset_binding(username),
                )
                icon_action(
                    "delete",
                    "删除",
                    lambda: self._confirm_delete(username),
                    danger=True,
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
        with ui.dialog() as dialog, ui.card().classes(
            "vpn-form-dialog w-96 users-change-group-dialog"
        ):
            ui.label(f"切换用户组 — {username}").classes(
                "vpn-form-dialog__title"
            )
            ui.label("切换后会重新分配虚拟 IP，并立即踢下线，用户重连后生效。").classes(
                "text-caption text-grey"
            )
            group_select = ui.select(group_options, label="目标组").classes("w-full").props(
                'behavior=menu popup-content-class="vpn-group-select-menu"'
            )

            with ui.row().classes(
                "w-full justify-end q-mt-md vpn-form-dialog__actions"
            ):
                ui.button("取消", on_click=dialog.close).props("flat no-caps")
                ui.button(
                    "切换",
                    on_click=lambda: self._do_change_group(dialog, username, group_select.value),
                ).props("unelevated no-caps color=primary")
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
            "w-[min(100vw,48rem)] h-full no-wrap overflow-y-auto users-config-drawer"
        ):
            ui.label(f"编辑 .ovpn 配置 — {username}").classes(
                "text-h6 users-config-drawer__title"
            )
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
            f"确定重置 {username} 的设备绑定？",
            impact="重置后该用户下次接入会被视为新设备，需要重新建立绑定。",
            object_count=1,
            danger_level="warning",
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
            "确定重置所选用户的设备绑定？",
            impact="重置后这些用户下次接入会被视为新设备。",
            object_count=len(self.selected_users),
            danger_level="warning",
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
        """显示统一的新建用户表单。"""

        from app.services.group.crud import GroupService

        groups = [g for g in GroupService().list_all() if g.get("status") == "active"]
        group_options = {
            str(group["id"]): f"{group['name']} ({group['subnet']})"
            for group in groups
        }
        fields: dict[str, object] = {}

        def body() -> None:
            fields["username"] = ui.input(
                "用户名",
                placeholder="例如：lisi",
            ).classes("w-full")
            fields["count"] = ui.number(
                "创建数量",
                value=1,
                min=1,
                max=500,
                step=1,
                format="%.0f",
            ).classes("w-full")
            ui.label("数量 > 1 时自动生成后缀：lisi、lisi_1、lisi_2 …").classes(
                "section-caption"
            )
            fields["group"] = ui.select(
                group_options,
                label="所属组",
            ).classes("w-full").props(
                'behavior=menu popup-content-class="vpn-group-select-menu"'
            )
            fields["password_enabled"] = ui.checkbox("启用账号密码")
            fields["password"] = ui.input("密码", password=True).classes("w-full")
            fields["password"].bind_visibility_from(fields["password_enabled"], "value")

        form = create_form_dialog(
            "新建用户",
            body=body,
            submit_label="创建",
            on_submit=lambda: self._do_create(
                form.dialog,
                str(getattr(fields["username"], "value", "") or ""),
                int(getattr(fields["count"], "value", 1) or 1),
                str(getattr(fields["group"], "value", "") or ""),
                bool(getattr(fields["password_enabled"], "value", False)),
                str(getattr(fields["password"], "value", "") or ""),
            ),
        )
        form.open()

    def _open_progress_dialog(self, title: str, total: int) -> dict[str, object]:
        """创建一个持久进度对话框；任务完成后才显示关闭按钮。"""

        refs: dict[str, object] = {}
        with ui.dialog().props("persistent") as dialog, ui.card().classes("vpn-form-dialog"):
            ui.label(title).classes("vpn-form-dialog__title")
            refs["status"] = ui.label(f"准备处理 0 / {total}").classes("section-caption")
            refs["progress"] = ui.linear_progress(value=0.0, show_value=False).classes("w-full")
            refs["result"] = ui.label("").classes("section-caption")
            refs["result"].set_visibility(False)
            with ui.row().classes("w-full justify-end"):
                refs["close"] = ui.button("关闭", on_click=dialog.close).props("flat no-caps")
                refs["close"].set_visibility(False)
        refs["dialog"] = dialog
        dialog.open()
        return refs

    @staticmethod
    def _update_progress_dialog(
        refs: dict[str, object] | None,
        *,
        done: int,
        total: int,
        current: str = "",
        result: str | None = None,
    ) -> None:
        if not refs:
            return
        status = refs.get("status")
        progress = refs.get("progress")
        if status is not None:
            suffix = f" · {current}" if current else ""
            status.set_text(f"已处理 {done} / {total}{suffix}")
        if progress is not None:
            progress.set_value((done / total) if total else 1.0)
        if result is not None:
            result_label = refs.get("result")
            close_button = refs.get("close")
            if result_label is not None:
                result_label.set_text(result)
                result_label.set_visibility(True)
            if close_button is not None:
                close_button.set_visibility(True)

    def _do_create(self, dialog, username: str, count: int, group_id: str, password_enabled: bool, password: str):
        """执行用户创建（支持批量命名）并展示可见进度。"""

        if self._create_running:
            ui.notify("用户正在后台创建，请稍候", type="warning")
            return False
        if not username or not group_id:
            ui.notify("请填写完整信息", type="negative")
            return False
        if count < 1:
            ui.notify("创建数量无效", type="negative")
            return False

        base = username.strip()
        names = [base] if count == 1 else [base] + [f"{base}_{i}" for i in range(1, count)]
        self._create_running = True
        self._create_result = None
        self._create_progress = {
            "done": 0,
            "total": len(names),
            "current": names[0] if names else "",
        }
        dialog.close()
        self._create_progress_ui = self._open_progress_dialog("创建用户", len(names))
        logger.info("后台用户创建任务已启动 count=%s first_user=%s", len(names), names[0])

        def worker():
            created: list[str] = []
            current_name = ""
            try:
                for index, name in enumerate(names, start=1):
                    current_name = name
                    self._create_progress = {
                        "done": len(created),
                        "total": len(names),
                        "current": name,
                    }
                    logger.info("后台创建用户开始 user=%s", name)
                    self.user_service.create(
                        username=name,
                        group_id=group_id,
                        password_enabled=password_enabled,
                        password=password if password_enabled else None,
                    )
                    created.append(name)
                    self._create_progress = {
                        "done": index,
                        "total": len(names),
                        "current": name,
                    }
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
        return True

    def _poll_create_progress(self):
        """轮询后台用户创建进度与最终结果。"""

        progress = self._create_progress
        self._update_progress_dialog(
            self._create_progress_ui,
            done=int(progress.get("done") or 0),
            total=int(progress.get("total") or 0),
            current=str(progress.get("current") or ""),
        )
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
        created = list(result.get("created") or [])
        total = int(progress.get("total") or len(created))

        if result.get("ok"):
            summary = f"创建完成：成功 {len(created)}，失败 0"
            ui.notify(summary, type="positive")
        else:
            summary = (
                f"创建中断：成功 {len(created)}，失败 {max(1, total - len(created))}。"
                f"{result.get('error') or '创建失败'}"
            )
            ui.notify(summary, type="warning")

        self._update_progress_dialog(
            self._create_progress_ui,
            done=len(created),
            total=total,
            current="",
            result=summary,
        )
        if created:
            self._copy_download_link(created[0])
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
        """生成一次性链接并通过统一剪贴板辅助复制。"""
        url = self._ensure_one_time_download_url(username)
        if not url:
            return
        background_tasks.create(
            self._copy_text(
                url,
                "下载链接已复制（约 1 小时内有效，仅可下载一次）",
            ),
            name=f"copy download link {username}",
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
        """删除用户前明确证书和配置影响。"""
        confirm_dialog.show(
            f"确定删除用户 {username}？",
            impact="删除会吊销该用户证书、清理 CCD 与配置文件，操作不可恢复。",
            object_count=1,
            danger_level="danger",
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

        with ui.dialog() as dialog, ui.card().classes(
            "vpn-form-dialog min-w-[480px] users-import-dialog"
        ):
            ui.label("批量导入用户").classes("vpn-form-dialog__title")
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

            with ui.row().classes(
                "w-full justify-end q-mt-md vpn-form-dialog__actions"
            ):
                ui.button("取消", on_click=dialog.close).props("flat no-caps")
                ui.button(
                    "预检查并导入",
                    icon="playlist_add_check",
                    on_click=lambda: self._do_import(dialog, textarea.value, GroupService()),
                ).props("unelevated no-caps color=primary")
        dialog.open()

    def _do_import(self, dialog, raw_text: str, group_service):
        """预检查全部导入数据，通过后在后台逐条创建并展示进度。"""

        if self._import_running:
            ui.notify("批量导入正在执行，请稍候", type="warning")
            return
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
        existing_users = {user["username"] for user in self.user_service.list_all()}
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

        dialog.close()
        self._import_running = True
        self._import_result = None
        self._import_progress = {
            "done": 0,
            "total": len(rows),
            "current": str(rows[0].get("username") or "") if rows else "",
        }
        self._import_progress_ui = self._open_progress_dialog("批量导入用户", len(rows))

        def worker() -> None:
            created: list[str] = []
            current_name = ""
            try:
                for index, row in enumerate(rows, start=1):
                    current_name = str(row["username"])
                    self._import_progress = {
                        "done": len(created),
                        "total": len(rows),
                        "current": current_name,
                    }
                    self.user_service.create(
                        username=current_name,
                        group_id=group_ref_to_id[row["group"]],
                    )
                    created.append(current_name)
                    self._import_progress = {
                        "done": index,
                        "total": len(rows),
                        "current": current_name,
                    }
                self._import_result = {"ok": True, "created": created}
            except Exception as exc:
                logger.exception(
                    "批量导入用户创建失败 user=%s created_count=%s",
                    current_name,
                    len(created),
                )
                self._import_result = {
                    "ok": False,
                    "created": created,
                    "error": f"创建 {current_name} 失败: {exc}",
                }

        threading.Thread(target=worker, daemon=True, name="user-import-worker").start()
        self._import_timer = ui.timer(0.3, self._poll_import_progress)

    def _poll_import_progress(self) -> None:
        """轮询批量导入进度并汇总成功、部分成功与失败。"""

        progress = self._import_progress
        self._update_progress_dialog(
            self._import_progress_ui,
            done=int(progress.get("done") or 0),
            total=int(progress.get("total") or 0),
            current=str(progress.get("current") or ""),
        )
        if self._import_result is None:
            return

        if self._import_timer is not None:
            if hasattr(self._import_timer, "deactivate"):
                self._import_timer.deactivate()
            elif hasattr(self._import_timer, "active"):
                self._import_timer.active = False
            self._import_timer = None

        self._import_running = False
        result = self._import_result
        self._import_result = None
        created = list(result.get("created") or [])
        total = int(progress.get("total") or len(created))

        if result.get("ok"):
            summary = f"导入完成：成功 {len(created)}，失败 0"
            ui.notify(summary, type="positive")
        else:
            summary = (
                f"导入中断：成功 {len(created)}，失败 {max(1, total - len(created))}。"
                f"{result.get('error') or '导入失败'}"
            )
            ui.notify(summary, type="warning")

        self._update_progress_dialog(
            self._import_progress_ui,
            done=len(created),
            total=total,
            current="",
            result=summary,
        )
        self._refresh_user_list()

