# -*- coding: utf-8 -*-
"""证书管理页面。"""

import logging
import re
from datetime import datetime, timezone

from nicegui import ui

from app.ui.copy_clipboard import copy_text_to_clipboard

from app.services.cert.cert_service import CertService
from app.ui.components import alert_card, confirm_dialog

logger = logging.getLogger(__name__)


class CertsPage:
    """证书管理页面。"""

    _STATUS_STYLES = {
        "valid": {"label": "有效", "class": "success"},
        "expiring": {"label": "即将到期", "class": "warn"},
        "revoked": {"label": "已吊销", "class": "danger"},
        "expired": {"label": "已过期", "class": "danger"},
    }

    def __init__(self):
        self.cert_service = CertService()
        self.advanced_open = False
        self.advanced_icon = None
        self.advanced_body = None
        self.search_input = None
        self.list_container = None
        self.selected_certs: set = set()

    def render(self):
        """渲染证书管理页面。"""
        with ui.column().classes("page-shell mgmt-page"):
            with ui.element("section").classes("mgmt-panel"):
                with ui.element("div").classes("mgmt-header-row"):
                    with ui.element("div").classes("mgmt-header-copy"):
                        ui.label("证书管理").classes("mgmt-title")
                        ui.label("用户证书列表；续签、吊销，底部可维护 CRL / CA。").classes("mgmt-desc")
                    with ui.element("div").classes("mgmt-toolbar"):
                        self.search_input = ui.input(
                            placeholder="搜索用户名",
                        ).classes("mgmt-search-input").on("keydown.enter", self._refresh_cert_list)
                        ui.button("搜索", icon="search", on_click=self._refresh_cert_list).props(
                            "outline no-caps no-ripple"
                        ).classes("mgmt-toolbar-btn is-outline mgmt-search-btn")
                        ui.button("批量续签", icon="autorenew", on_click=self._batch_renew).props(
                            "outline no-caps no-ripple"
                        ).classes("mgmt-toolbar-btn is-outline")
                        ui.button("批量吊销", icon="block", on_click=self._batch_revoke).props(
                            "outline no-caps no-ripple"
                        ).classes("mgmt-toolbar-btn is-outline")
                        ui.button(icon="refresh", on_click=self._refresh_table).props(
                            "flat round dense no-caps no-ripple"
                        ).classes("mgmt-icon-btn").tooltip("刷新")

            self._render_expiry_alerts()

            with ui.element("section").classes("mgmt-panel mgmt-panel-list"):
                with ui.element("div").classes("mgmt-list-head"):
                    ui.label("证书库").classes("mgmt-kicker")
                self.list_container = ui.column().classes("w-full")
                with self.list_container:
                    self._render_cert_list()

            with ui.element("section").classes("cert-advanced-panel"):
                with ui.element("div").classes("cert-advanced-toggle").on(
                    "click", self._toggle_advanced_panel
                ):
                    with ui.element("div").classes("cert-advanced-copy"):
                        ui.icon("settings_suggest", color="#fb7185")
                        ui.label("高级：CRL / 重建 CA").classes("cert-advanced-title")
                    self.advanced_icon = ui.icon("expand_more").classes("cert-advanced-icon")

                self.advanced_body = ui.column().classes("cert-advanced-body")
                self.advanced_body.set_visibility(False)
                with self.advanced_body:
                    ui.label("危险：重建 CA 会使现有证书全部失效。").classes("cert-advanced-note")
                    with ui.element("div").classes("cert-advanced-actions"):
                        ui.button("更新 CRL", on_click=self._handle_regen_crl).props(
                            "outline no-caps no-ripple"
                        ).classes("cert-outline-btn")
                        ui.button("重建 CA 根", on_click=self._show_rebuild_ca_confirm).props(
                            "outline no-caps no-ripple"
                        ).classes("cert-danger-btn")

    def _refresh_cert_list(self):
        """按搜索条件过滤并刷新证书列表。"""
        if self.list_container is None:
            return
        self.list_container.clear()
        with self.list_container:
            self._render_cert_list()

    def _render_expiry_alerts(self):
        """渲染即将到期告警。"""
        expiring = self.cert_service.get_expiring()
        if expiring:
            alert_card.show(
                level="warning",
                title=f"{len(expiring)} 个证书将到期",
                message="、".join(cert["common_name"] for cert in expiring[:5]) + ("..." if len(expiring) > 5 else ""),
            )

    def _render_cert_list(self):
        """渲染证书列表（支持搜索过滤）。"""
        certs = self.cert_service.list_all()
        q = (self.search_input.value or "").strip() if self.search_input else ""
        if q:
            pat = re.compile(".*" + re.escape(q) + ".*", re.IGNORECASE)
            certs = [c for c in certs if pat.search(c.get("common_name", ""))]
        now = datetime.now(timezone.utc)

        if not certs:
            ui.label("暂无证书。").classes("empty-state")
            return

        with ui.element("div").classes("mgmt-record-list"):
            for cert in certs:
                display_status = cert["status"]
                if cert["status"] == "valid":
                    try:
                        expires_at = datetime.fromisoformat(cert["expires_at"])
                        from app.core.constants import CERT_EXPIRY_WARN_DAYS

                        if (expires_at - now).days <= CERT_EXPIRY_WARN_DAYS:
                            display_status = "expiring"
                    except (ValueError, TypeError):
                        pass

                style = self._STATUS_STYLES.get(display_status, self._STATUS_STYLES["valid"])
                self._render_cert_row(cert, style)

    def _render_cert_row(self, cert: dict, style: dict):
        """渲染单个证书条目。"""
        common_name = cert["common_name"]
        serial = cert["serial"]
        expires = cert["expires_at"][:10] if cert.get("expires_at") else "—"
        row_classes = "mgmt-record-card"
        name_classes = "mgmt-record-title"

        if cert["status"] == "revoked":
            row_classes += " is-muted"
            name_classes += " is-muted"

        with ui.element("div").classes(row_classes):
            ui.checkbox(
                value=common_name in self.selected_certs,
                on_change=lambda e, cn=common_name: self._toggle_cert_selection(cn, e.value),
            ).classes("mgmt-checkbox")
            with ui.element("div").classes("mgmt-record-main"):
                with ui.element("div").classes("mgmt-record-copy"):
                    ui.label(common_name).classes(name_classes)
                    with ui.element("div").classes("mgmt-record-meta"):
                        ui.button(
                            f"SN: {self._short_serial(serial)}",
                            icon="content_copy",
                            on_click=lambda value=serial: self._copy_text(value, "序列号已复制"),
                        ).props("flat dense no-caps no-ripple").classes("cert-copy-chip")
                        with ui.element("span").classes("mgmt-meta-item"):
                            ui.icon("event", size="12px")
                            ui.label(expires)

            with ui.element("div").classes("mgmt-record-side"):
                ui.label(style["label"]).classes(f"cert-status {style['class']}".strip())
                with ui.element("div").classes("mgmt-actions"):
                    renew_button = ui.button(
                        icon="autorenew",
                        on_click=lambda cn=common_name: self._handle_renew(cn),
                    ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-renew").tooltip("续签")
                    revoke_button = ui.button(
                        icon="block",
                        on_click=lambda cn=common_name: self._handle_revoke(cn),
                    ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-revoke").tooltip("吊销")
                    if cert["status"] == "revoked":
                        renew_button.disable()
                        revoke_button.disable()

    def _short_serial(self, serial: str) -> str:
        """生成短序列号。"""
        if not serial:
            return "—"
        if len(serial) <= 8:
            return serial
        return f"{serial[:8]}..."

    def _copy_text(self, text: str, message: str):
        """复制文本并提示。"""
        if not text:
            ui.notify("没有可复制的内容", type="warning")
            return
        copy_text_to_clipboard(text)
        ui.notify(message, type="positive")

    def _toggle_advanced_panel(self):
        """切换高级面板展开状态。"""
        self.advanced_open = not self.advanced_open
        if self.advanced_body is not None:
            self.advanced_body.set_visibility(self.advanced_open)
        if self.advanced_icon is not None:
            self.advanced_icon.classes(remove="is-open")
            if self.advanced_open:
                self.advanced_icon.classes(add="is-open")

    def _refresh_table(self):
        """刷新页面。"""
        ui.navigate.to("/certs")

    def _handle_renew(self, common_name: str):
        """续签确认。"""
        confirm_dialog.show(
            message=f"续签 {common_name} 的证书？",
            on_confirm=lambda: self._do_renew(common_name),
            title="续签",
        )

    def _do_renew(self, common_name: str):
        """执行续签。"""
        success = self.cert_service.renew(common_name)
        if success:
            ui.notify(f"已续签: {common_name}", type="positive")
            self._refresh_table()
        else:
            ui.notify(f"续签失败: {common_name}，见日志", type="negative")

    def _handle_revoke(self, common_name: str):
        """吊销确认。"""
        confirm_dialog.show(
            message=f"吊销 {common_name}？吊销后无法连 VPN。",
            on_confirm=lambda: self._do_revoke(common_name),
            title="吊销",
        )

    def _do_revoke(self, common_name: str):
        """执行吊销。"""
        success = self.cert_service.revoke(common_name)
        if success:
            ui.notify(f"已吊销: {common_name}", type="positive")
            self._refresh_table()
        else:
            ui.notify(f"吊销失败: {common_name}，见日志", type="negative")

    def _handle_regen_crl(self):
        """重新生成 CRL。"""
        confirm_dialog.show(
            message="重新生成 CRL？",
            on_confirm=self._do_regen_crl,
            title="更新 CRL",
        )

    def _do_regen_crl(self):
        """执行 CRL 生成。"""
        try:
            wrapper = self.cert_service._get_wrapper()
            wrapper.gen_crl()
            ui.notify("CRL 已更新", type="positive")
        except Exception as exc:
            ui.notify(f"CRL 生成失败: {exc}", type="negative")

    def _show_rebuild_ca_confirm(self):
        """显示重建 CA 确认弹窗。"""
        with ui.dialog() as dialog, ui.card().classes("min-w-[400px]"):
            ui.label("重建 CA 证书").classes("text-h6 text-weight-bold")
            ui.separator()
            ui.label("将重建 CA：现有客户端证书全部作废，用户需重新下发配置。").classes("q-my-md text-negative")
            ui.label("当前为无密码 CA，不会提示输入 CA 密码。").classes("section-caption")

            confirm_text = ui.input('请输入 "REBUILD" 以确认').classes("w-full q-mt-sm")

            with ui.row().classes("q-mt-md justify-end"):
                ui.button("取消", on_click=dialog.close).props("flat")
                ui.button(
                    "确认重建",
                    on_click=lambda: self._do_rebuild_ca(dialog, confirm_text.value),
                ).props("color=negative")
        dialog.open()

    def _do_rebuild_ca(self, dialog, confirm_text: str):
        """执行 CA 重建。"""
        if confirm_text != "REBUILD":
            ui.notify('请输入 REBUILD 确认', type="warning")
            return

        dialog.close()
        try:
            wrapper = self.cert_service._get_wrapper()
            wrapper.build_ca()
            wrapper.gen_crl()
            ui.notify("CA 已重建；请让用户重新拉取配置", type="positive")
            self._refresh_table()
        except Exception as exc:
            ui.notify(f"CA 重建失败: {exc}", type="negative")

    def _toggle_cert_selection(self, common_name: str, checked: bool):
        """切换证书选中状态。"""
        if checked:
            self.selected_certs.add(common_name)
        else:
            self.selected_certs.discard(common_name)

    def _batch_renew(self):
        """批量续签选中的证书。"""
        if not self.selected_certs:
            ui.notify("请先勾选证书", type="warning")
            return
        count = len(self.selected_certs)
        confirm_dialog.show(
            message=f"批量续签已选 {count} 个？",
            on_confirm=self._do_batch_renew,
            title="批量续签",
        )

    def _do_batch_renew(self):
        """执行批量续签。"""
        success_count = 0
        failed = []
        for cn in list(self.selected_certs):
            try:
                if self.cert_service.renew(cn):
                    success_count += 1
                else:
                    failed.append(cn)
            except Exception as exc:
                failed.append(f"{cn}: {exc}")
        self.selected_certs.clear()
        msg = f"已续签 {success_count} 个"
        if failed:
            msg += f"，失败 {len(failed)} 个: {', '.join(failed)}"
            ui.notify(msg, type="warning")
        else:
            ui.notify(msg, type="positive")
        self._refresh_table()

    def _batch_revoke(self):
        """批量吊销选中的证书。"""
        if not self.selected_certs:
            ui.notify("请先勾选证书", type="warning")
            return
        count = len(self.selected_certs)
        confirm_dialog.show(
            message=f"批量吊销 {count} 个？相关用户将不能连 VPN，不可恢复。",
            on_confirm=self._do_batch_revoke,
            title="批量吊销",
        )

    def _do_batch_revoke(self):
        """执行批量吊销。"""
        success_count = 0
        failed = []
        for cn in list(self.selected_certs):
            try:
                if self.cert_service.revoke(cn):
                    success_count += 1
                else:
                    failed.append(cn)
            except Exception as exc:
                failed.append(f"{cn}: {exc}")
        self.selected_certs.clear()
        msg = f"已吊销 {success_count} 个"
        if failed:
            msg += f"，失败 {len(failed)} 个: {', '.join(failed)}"
            ui.notify(msg, type="warning")
        else:
            ui.notify(msg, type="positive")
        self._refresh_table()
