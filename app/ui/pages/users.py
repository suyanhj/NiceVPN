# -*- coding: utf-8 -*-
"""用户管理页面。"""

import logging
import re
from datetime import datetime

from nicegui import ui

logger = logging.getLogger(__name__)

from app.core.config import load_config
from app.services.openvpn.instance import get_status
from app.services.user.device_bind import DeviceBindingService
from app.ui.components import confirm_dialog
from app.ui.copy_clipboard import copy_text_to_clipboard
from app.services.user.crud import UserService


class UsersPage:
    """用户管理页面。"""

    def __init__(self):
        self.user_service = UserService()
        self.selected_users: set[str] = set()
        self.list_container = None
        self.search_input = None

    def render(self):
        """渲染用户管理页面。"""
        users = self.user_service.list_all()
        live_sessions = self._load_live_sessions()
        binding_aux = DeviceBindingService().build_user_binding_aux()
        ccd_vip_map = self.user_service.list_ccd_virtual_ipv4_by_username()

        with ui.column().classes("page-shell mgmt-page"):
            with ui.element("section").classes("mgmt-panel"):
                with ui.element("div").classes("mgmt-header-row"):
                    with ui.element("div").classes("mgmt-header-copy"):
                        ui.label("用户管理").classes("mgmt-title")
                        ui.label("查看在线与证书、流量与虚拟 IP；可下发配置与链接。").classes("mgmt-desc")

                    with ui.element("div").classes("mgmt-toolbar"):
                        self.search_input = ui.input(
                            placeholder="搜索用户名",
                        ).classes("mgmt-search-input").on("keydown.enter", self._refresh_user_list)
                        ui.button("搜索", icon="search", on_click=self._refresh_user_list).props(
                            "outline no-caps no-ripple"
                        ).classes("mgmt-toolbar-btn is-outline mgmt-search-btn")
                        ui.button("新建用户", icon="person_add", on_click=self._show_create_dialog).props(
                            "unelevated no-caps no-ripple"
                        ).classes("mgmt-toolbar-btn is-primary")
                        ui.button("批量导入", icon="upload_file", on_click=self._show_import_dialog).props(
                            "outline no-caps no-ripple"
                        ).classes("mgmt-toolbar-btn is-outline")
                        ui.button("批量下载", icon="download", on_click=self._batch_download).props(
                            "outline no-caps no-ripple"
                        ).classes("mgmt-toolbar-btn is-outline")
                        ui.button(
                            "批量重置绑定",
                            icon="phonelink_erase",
                            on_click=self._batch_reset_binding_confirm,
                        ).props("outline no-caps no-ripple").classes("mgmt-toolbar-btn is-outline")

            with ui.element("section").classes("mgmt-panel mgmt-panel-list"):
                with ui.element("div").classes("mgmt-list-head"):
                    ui.label("用户列表").classes("mgmt-kicker")

                self.list_container = ui.column().classes("w-full")
                with self.list_container:
                    self._render_user_list(users, live_sessions, binding_aux, ccd_vip_map)

    def _refresh_user_list(self):
        """按搜索条件刷新列表。"""
        users = self.user_service.list_all()
        q = (self.search_input.value or "").strip() if self.search_input else ""
        if q:
            pat = re.compile(".*" + re.escape(q) + ".*", re.IGNORECASE)
            users = [u for u in users if pat.search(u.get("username", ""))]
        live_sessions = self._load_live_sessions()
        binding_aux = DeviceBindingService().build_user_binding_aux()
        ccd_vip_map = self.user_service.list_ccd_virtual_ipv4_by_username()
        self.list_container.clear()
        with self.list_container:
            self._render_user_list(users, live_sessions, binding_aux, ccd_vip_map)

    def _render_user_list(
        self,
        users: list[dict],
        live_sessions: dict[str, dict],
        binding_aux: dict[str, dict[str, str]],
        ccd_vip_map: dict[str, str],
    ):
        """渲染用户列表容器。"""
        if not users:
            ui.label("暂无用户，点「新建用户」添加。").classes("empty-state")
            return

        with ui.element("div").classes("mgmt-record-list"):
            for item in users:
                uname = item["username"]
                self._render_user_card(
                    item,
                    live_sessions.get(uname),
                    binding_aux.get(uname) or {},
                    (ccd_vip_map.get(uname) or "").strip(),
                )

    def _render_user_card(
        self,
        user: dict,
        session: dict | None,
        binding_aux: dict[str, str],
        ccd_virtual_ip: str,
    ):
        """渲染单个用户卡片：虚拟 IP 以 CCD 固定分配为准（创建用户时写入），无 CCD 时在线才用 status。"""
        username = user["username"]
        group_id = user.get("group_id", "")
        status = user.get("status", "active")
        is_active = status == "active"
        created_at = (user.get("created_at", "") or "")[:10] or "未知"
        cert_serial = user.get("cert_serial", "—")
        serial_short = self._short_serial(cert_serial)
        group_short = group_id[:8] if group_id else ""
        is_online = session is not None
        bytes_received = session.get("bytes_received", 0) if session else 0
        bytes_sent = session.get("bytes_sent", 0) if session else 0
        br_label = self._format_bytes(bytes_received) if session else "—"
        bs_label = self._format_bytes(bytes_sent) if session else "—"
        connected_since_raw = session.get("connected_since", "") if session else ""
        connected_since = self._format_connected_since(connected_since_raw) if session else "—"
        connection_duration = self._format_connection_duration(connected_since_raw) if session else "—"
        real_address = session.get("real_address", "—") if session else "—"
        if ccd_virtual_ip:
            virtual_label = ccd_virtual_ip
        elif session:
            virtual_label = (session.get("virtual_address") or "").strip() or "—"
        else:
            virtual_label = "—"
        client_device = (binding_aux.get("device_label") or "").strip()
        last_conn_stored = (binding_aux.get("last_connected_since") or "").strip()

        with ui.element("div").classes("mgmt-record-card"):
            with ui.element("div").classes("mgmt-record-main"):
                ui.checkbox(
                    value=username in self.selected_users,
                    on_change=lambda e, current=username: self._toggle_select(current, e.value),
                ).classes("mgmt-checkbox")

                with ui.element("div").classes("mgmt-record-copy" + (" is-muted" if not is_active else "")):
                    with ui.row().classes("items-center gap-2"):
                        ui.label(username).classes("mgmt-record-title")
                        ui.label("在线" if is_online else "离线").classes(
                            "user-session-badge" if is_online else "user-session-badge is-offline"
                        )
                        if not is_active:
                            ui.label("已停用").classes("user-session-badge is-offline")
                    with ui.element("div").classes("mgmt-record-meta user-card-meta"):
                        with ui.row().classes("items-center gap-3 flex-wrap w-full"):
                            with ui.element("span").classes("mgmt-meta-item"):
                                ui.icon("event", size="12px")
                                ui.label(created_at)
                            ui.button(
                                f"SN {serial_short}",
                                icon="content_copy",
                                on_click=lambda value=cert_serial: self._copy_text(
                                    value, f"证书序列号已复制: {value}"
                                ),
                            ).props("flat dense no-caps no-ripple").classes("user-copy-chip")
                            if group_id:
                                ui.button(
                                    f"GID {group_short}",
                                    icon="content_copy",
                                    on_click=lambda value=group_id: self._copy_text(
                                        value, f"组 ID 已复制: {value}"
                                    ),
                                ).props("flat dense no-caps no-ripple").classes("user-copy-chip")
                        with ui.row().classes(
                            "items-center gap-3 flex-wrap w-full user-meta-session-line"
                        ):
                            with ui.element("span").classes("mgmt-meta-item"):
                                ui.label(f"↓{br_label}  ↑{bs_label}")
                            if is_online:
                                with ui.element("span").classes("mgmt-meta-item"):
                                    ui.label(f"连接于 {connected_since}  时长 {connection_duration}")
                                with ui.element("span").classes("mgmt-meta-item"):
                                    ui.label(f"来源 {real_address}")
                            elif last_conn_stored:
                                lc_label = self._format_connected_since(last_conn_stored)
                                with ui.element("span").classes("mgmt-meta-item"):
                                    ui.label(f"上次连接于 {lc_label}")
                            if client_device:
                                with ui.element("span").classes("mgmt-meta-item"):
                                    ui.label(f"设备 {client_device}")
                            with ui.element("span").classes("mgmt-meta-item"):
                                ui.label(f"虚拟 {virtual_label}")

            with ui.element("div").classes("mgmt-actions"):
                ui.button(
                    icon="toggle_on" if is_active else "toggle_off",
                    on_click=lambda current=username: self._toggle_user_status(current),
                ).props("flat round dense no-caps no-ripple").classes(
                    "mgmt-icon-btn is-link" if is_active else "mgmt-icon-btn"
                ).tooltip("停用" if is_active else "启用")
                if is_online:
                    ui.button(
                        icon="power_settings_new",
                        on_click=lambda current=username: self._kick_offline(current),
                    ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-delete").tooltip("踢下线")
                ui.button(
                    icon="download",
                    on_click=lambda current=username: self._download_ovpn(current),
                ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-link").tooltip("下载 .ovpn")
                ui.button(
                    icon="edit_note",
                    on_click=lambda current=username: self._edit_ovpn(current),
                ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn").tooltip("编辑配置")
                ui.button(
                    icon="phonelink_lock",
                    on_click=lambda current=username: self._confirm_reset_binding(current),
                ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn").tooltip("重置设备绑定")
                ui.button(
                    icon="link",
                    on_click=lambda current=username: self._copy_download_link(current),
                ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-link").tooltip("复制下载链接")
                ui.button(
                    icon="send",
                    on_click=lambda current=username: self._push_download_link_notify(current),
                ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-send").tooltip("推送下载链接")
                ui.button(
                    icon="delete",
                    on_click=lambda current=username: self._confirm_delete(current),
                ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-delete").tooltip("删除")

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

    def _copy_text(self, text: str, message: str):
        """复制文本并提示。"""
        if not text or text == "—":
            ui.notify("没有可复制的内容", type="warning")
            return
        copy_text_to_clipboard(text)
        ui.notify(message, type="positive")

    def _toggle_select(self, username: str, selected: bool):
        """切换用户选中状态。"""
        if selected:
            self.selected_users.add(username)
        else:
            self.selected_users.discard(username)

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
            group_select = ui.select(group_options, label="所属组").classes("w-full")

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

        created = []
        for name in names:
            try:
                self.user_service.create(
                    username=name,
                    group_id=group_id,
                    password_enabled=password_enabled,
                    password=password if password_enabled else None,
                )
                created.append(name)
            except Exception as exc:
                ui.notify(f"创建 {name} 失败: {exc}", type="negative")
                return

        dialog.close()
        ui.notify(f"已创建 {len(created)} 个用户", type="positive")
        if created:
            self._copy_download_link(created[0])
            # 延后跳转，避免页面卸载导致剪贴板脚本未执行
            ui.timer(0.25, lambda: ui.navigate.to("/users"), once=True)
        else:
            ui.navigate.to("/users")

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
                "推送失败：请确认「系统设置 → 通知」已启用并选对通道（钉钉需配置 Webhook）",
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
        """显示批量导入弹窗：支持 CSV/TXT 文件，格式为 用户名 组名 或 用户名,组名。"""
        from app.services.group.crud import GroupService

        with ui.dialog() as dialog, ui.card().classes("min-w-[480px]"):
            ui.label("批量导入用户").classes("text-h6")
            ui.label(
                "每行：用户名 + 空格或逗号 + 组名（CSV/TXT 均可）。组须事先存在，否则整批不导入。"
            ).classes("section-caption")

            textarea = ui.textarea(
                label="粘贴内容（或上传文件后自动填入）",
                placeholder="zhangsan default_group\nlisi dev_group",
            ).classes("w-full").props('rows="8"')

            async def handle_upload(e):
                # NiceGUI 3.x：上传内容为 e.file（FileUpload），read/text 为异步
                try:
                    data = await e.file.read()
                    content = data.decode("utf-8", errors="replace")
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
        """解析并批量导入用户，预检查所有组是否存在。"""
        if not raw_text or not raw_text.strip():
            ui.notify("导入内容为空", type="warning")
            return

        # 解析每一行
        entries: list[tuple[str, str]] = []
        for line_num, line in enumerate(raw_text.strip().splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # 逗号或空格分隔
            if "," in line:
                parts = [p.strip() for p in line.split(",", 1)]
            else:
                parts = line.split(None, 1)
            if len(parts) < 2:
                ui.notify(f"第 {line_num} 行格式错误（需要 用户名 组名）: {line}", type="negative")
                return
            entries.append((parts[0], parts[1]))

        if not entries:
            ui.notify("没有解析到有效记录", type="warning")
            return

        # 预检查：收集所有引用的组名，验证是否存在
        all_groups = group_service.list_all()
        group_name_to_id = {g["name"]: g["id"] for g in all_groups}
        group_id_map = {g["id"]: g["id"] for g in all_groups}
        missing_groups = set()
        resolved: list[tuple[str, str]] = []
        for username, group_ref in entries:
            gid = group_name_to_id.get(group_ref) or group_id_map.get(group_ref)
            if not gid:
                missing_groups.add(group_ref)
            else:
                resolved.append((username, gid))

        if missing_groups:
            ui.notify(
                f"以下组不存在，整批拒绝导入: {', '.join(sorted(missing_groups))}",
                type="negative",
            )
            return

        # 检查用户名重复
        usernames = [r[0] for r in resolved]
        dup = set(u for u in usernames if usernames.count(u) > 1)
        if dup:
            ui.notify(f"导入数据中存在重复用户名: {', '.join(sorted(dup))}", type="negative")
            return

        # 逐一创建
        created, errors = [], []
        for username, group_id in resolved:
            try:
                self.user_service.create(username=username, group_id=group_id)
                created.append(username)
            except Exception as exc:
                errors.append(f"{username}: {exc}")

        dialog.close()
        if created:
            ui.notify(f"成功导入 {len(created)} 个用户", type="positive")
        if errors:
            for err in errors[:5]:
                ui.notify(f"导入失败 - {err}", type="negative")
            if len(errors) > 5:
                ui.notify(f"还有 {len(errors) - 5} 条错误未显示", type="warning")
        ui.navigate.to("/users")
