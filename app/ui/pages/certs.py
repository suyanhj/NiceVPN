# -*- coding: utf-8 -*-
"""证书管理页面。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from nicegui import ui

from app.core.constants import CERT_EXPIRY_WARN_DAYS
from app.services.cert.cert_service import CertService
from app.ui.adapters.certs import adapt_cert_row, short_serial
from app.ui.components import alert_card, confirm_dialog
from app.ui.components.actions import (
    MenuAction,
    danger_action,
    more_actions,
    secondary_action,
)
from app.ui.components.cards import metric_card
from app.ui.components.data_grid import render_data_grid
from app.ui.components.feedback import AsyncOperationGuard
from app.ui.components.states import empty_state, error_state, no_match_state
from app.ui.components.status import status_badge, structured_value
from app.ui.components.toolbars import render_bulk_action_bar, render_page_toolbar

logger = logging.getLogger(__name__)

_CERT_IDENTITY_RENDERER = r"""(params) => {
  const root = document.createElement('div');
  root.className = 'cert-grid-identity';

  const name = document.createElement('div');
  name.className = 'cert-grid-identity__name';
  name.textContent = String(params.data?.common_name ?? '—');

  const meta = document.createElement('div');
  meta.className = 'cert-grid-identity__meta';

  const sn = document.createElement('span');
  sn.className = 'cert-grid-identity__sn';
  sn.textContent = 'SN';

  const serial = document.createElement('span');
  serial.className = 'cert-grid-identity__serial';
  serial.textContent = String(params.data?.serial_display ?? '—');

  meta.append(sn, serial);
  root.append(name, meta);
  return root;
}"""

_CERT_STATUS_RENDERER = r"""(params) => {
  const rawTone = String(params.data?.status_tone ?? 'unknown');
  const tone = ['success', 'warning', 'failed'].includes(rawTone) ? rawTone : 'unknown';
  const pill = document.createElement('span');
  pill.className = 'cert-grid-status cert-grid-status--' + tone;
  pill.textContent = String(params.data?.status_label ?? '未知');
  return pill;
}"""

_CERT_EXPIRY_RENDERER = r"""(params) => {
  const root = document.createElement('div');
  root.className = 'cert-grid-expiry';

  const date = document.createElement('div');
  date.className = 'cert-grid-expiry__date';
  date.textContent = String(params.data?.expires_display ?? '—');

  const days = params.data?.days_left;
  const detail = document.createElement('div');
  detail.className = 'cert-grid-expiry__detail';

  if (days === null || days === undefined) {
    detail.textContent = '有效期未知';
  } else if (Number(days) < 0) {
    detail.classList.add('cert-grid-expiry__detail--danger');
    detail.textContent = '已过期 ' + Math.abs(Number(days)) + ' 天';
  } else {
    if (String(params.data?.status) === 'expiring') {
      detail.classList.add('cert-grid-expiry__detail--warning');
    }
    detail.textContent = '剩余 ' + Number(days) + ' 天';
  }

  root.append(date, detail);
  return root;
}"""

_CERT_ACTION_RENDERER = r"""(_params) => {
  const action = document.createElement('span');
  action.className = 'cert-grid-action';
  action.textContent = '•••';
  action.title = '证书操作';
  return action;
}"""


class CertsPage:
    """证书管理页面。"""

    _STATUS_LABELS = {
        "valid": "有效",
        "expiring": "即将到期",
        "revoked": "已吊销",
        "expired": "已过期",
        "unknown": "未知",
    }

    def __init__(self):
        self.cert_service = CertService()
        self.search_input = None
        self.data_container = None
        self.summary_container = None
        self.search_state_container = None
        self.grid_view = None
        self.all_rows: list[dict[str, Any]] = []
        self.selected_certs: set[str] = set()
        self.page_size = 50
        self.bulk_bar = None
        self.bulk_renew_button = None
        self.bulk_revoke_button = None
        self._operation_guard = AsyncOperationGuard()

        # 兼容旧页面属性，避免外部测试/调用依赖失效。
        self.advanced_open = False
        self.advanced_icon = None
        self.advanced_body = None
        self.list_container = None
        self.pagination_container = None
        self.select_all_checkbox = None
        self.visible_cert_names: list[str] = []
        self.cert_checkboxes: dict[str, Any] = {}
        self.page = 1

    def render(self):
        """使用共享页面结构和 AG Grid 渲染证书库。"""

        def search_region() -> None:
            self.search_input = (
                ui.input(placeholder="搜索用户名、序列号或状态")
                .props("clearable")
                .classes("cert-search-input")
            )
            self.search_input.on("keydown.enter", self._handle_search)
            secondary_action("搜索", icon="search", on_click=self._handle_search)
            secondary_action(
                "清除",
                icon="filter_alt_off",
                on_click=self._clear_search,
            )

        def secondary_region() -> None:
            secondary_action("刷新", icon="refresh", on_click=self._refresh_cert_list)
            more_actions(
                (
                    MenuAction("更新 CRL", icon="sync", on_click=self._handle_regen_crl),
                    MenuAction(
                        "重建 CA",
                        icon="warning",
                        danger=True,
                        on_click=self._show_rebuild_ca_confirm,
                    ),
                ),
                label="高级操作",
                icon="settings_suggest",
            )

        def bulk_actions() -> None:
            disabled = not self.selected_certs
            self.bulk_renew_button = secondary_action(
                "批量续签",
                icon="autorenew",
                on_click=self._batch_renew,
                disabled=disabled,
                tooltip="请先选择证书" if disabled else None,
            )
            self.bulk_revoke_button = danger_action(
                "批量吊销",
                icon="block",
                on_click=self._batch_revoke,
                disabled=disabled,
                tooltip="请先选择证书" if disabled else None,
            )

        with ui.column().classes("page-shell mgmt-page certs-page--v2"):
            self.summary_container = ui.element("div").classes("vpn-v2-overview-grid")
            toolbar = render_page_toolbar(
                search=search_region,
                secondary=secondary_region,
            )
            toolbar.container.classes(add="certs-toolbar")
            self._render_expiry_alerts()
            self.bulk_bar = render_bulk_action_bar(
                len(self.selected_certs),
                actions=bulk_actions,
            )
            self.data_container = ui.column().classes(
                "w-full flex-1 min-h-0 certs-data-region"
            )

        self._refresh_cert_list()

    @classmethod
    def _adapt_cert_row(
        cls,
        cert: dict,
        *,
        now: datetime | None = None,
        warn_days: int = CERT_EXPIRY_WARN_DAYS,
    ) -> dict[str, Any]:
        """兼容页面调用；证书展示语义由纯适配层统一维护。"""

        return adapt_cert_row(cert, now=now, warn_days=warn_days)

    @staticmethod
    def _grid_columns() -> tuple[dict, ...]:
        """高密度数据能力保留，视觉按高级列表组织为四个信息区。"""

        return (
            {
                "field": "common_name",
                "headerName": "用户 / CN",
                "pinned": "left",
                "lockPinned": True,
                "suppressMovable": True,
                "width": 230,
                "minWidth": 190,
                "maxWidth": 260,
                ":valueGetter": (
                    "(params) => String(params.data?.common_name ?? '') + ' ' + "
                    "String(params.data?.serial ?? '')"
                ),
                ":cellRenderer": _CERT_IDENTITY_RENDERER,
                "cellClass": "cert-grid-cell cert-grid-cell--identity",
            },
            {
                "field": "status_label",
                "headerName": "状态",
                "flex": 0.55,
                "minWidth": 112,
                "maxWidth": 150,
                ":cellRenderer": _CERT_STATUS_RENDERER,
                "cellClass": "cert-grid-cell cert-grid-cell--status",
            },
            {
                "field": "expires_display",
                "headerName": "有效期",
                "flex": 1,
                "minWidth": 180,
                ":cellRenderer": _CERT_EXPIRY_RENDERER,
                "cellClass": "cert-grid-cell cert-grid-cell--expiry",
            },
            {
                "field": "actions",
                "headerName": "",
                "pinned": "right",
                "lockPinned": True,
                "suppressMovable": True,
                "width": 64,
                "minWidth": 64,
                "maxWidth": 64,
                "sortable": False,
                "filter": False,
                "resizable": False,
                ":cellRenderer": _CERT_ACTION_RENDERER,
                "cellClass": "cert-grid-cell cert-grid-cell--actions",
            },
        )

    def _load_rows(self) -> list[dict[str, Any]]:
        return [self._adapt_cert_row(cert) for cert in self.cert_service.list_all()]

    def _refresh_cert_list(self):
        """刷新证书数据，并保留仍存在的选择和搜索条件。"""

        if self.data_container is None:
            return
        try:
            rows = self._load_rows()
        except Exception as exc:
            logger.exception("加载证书列表失败")
            if self.summary_container is not None:
                self.summary_container.clear()
            self.data_container.clear()
            with self.data_container:
                error_state(
                    str(exc),
                    title="证书列表加载失败",
                    on_retry=self._refresh_cert_list,
                )
            return

        self._render_summary_metrics(rows)

        existing = {str(row["id"]) for row in rows if row.get("id")}
        self.selected_certs.intersection_update(existing)
        self.all_rows = rows
        self.data_container.clear()

        with self.data_container:
            if not rows:
                self.grid_view = None
                self.search_state_container = None
                empty_state(
                    "暂无证书",
                    description="用户证书签发后会显示在这里。",
                    icon="verified_user",
                )
            else:
                self.search_state_container = ui.column().classes("w-full")
                self.grid_view = render_data_grid(
                    self._grid_columns(),
                    rows,
                    page_size=self.page_size,
                    selected_ids=self.selected_certs,
                    on_cell_click=self._on_grid_cell_clicked,
                    suppress_horizontal_scroll=True,
                )
                self.grid_view.summary.text = f"共 {len(rows)} 个证书"
                self.grid_view.grid.on("selectionChanged", self._on_selection_changed)

        self._sync_bulk_state()
        self._handle_search()

    def _render_summary_metrics(self, rows: list[dict[str, Any]]) -> None:
        """渲染证书库状态摘要；只消费已适配行，不增加业务请求。"""

        if self.summary_container is None:
            return

        valid_count = sum(row.get("status") == "valid" for row in rows)
        expiring_count = sum(row.get("status") == "expiring" for row in rows)
        expired_count = sum(row.get("status") == "expired" for row in rows)
        revoked_count = sum(row.get("status") == "revoked" for row in rows)
        invalid_count = expired_count + revoked_count

        self.summary_container.clear()
        with self.summary_container:
            metric_card(
                "证书总数",
                len(rows),
                helper="当前证书库",
                icon="verified_user",
                tone="info",
                compact=True,
            )
            metric_card(
                "有效证书",
                valid_count,
                helper="状态正常，可继续使用",
                icon="task_alt",
                tone="success" if valid_count else "default",
                compact=True,
            )
            metric_card(
                "临期证书",
                expiring_count,
                helper=f"{CERT_EXPIRY_WARN_DAYS} 天阈值内",
                icon="schedule",
                tone="warning" if expiring_count else "default",
                compact=True,
            )
            metric_card(
                "失效 / 吊销",
                invalid_count,
                helper=f"{expired_count} 过期 · {revoked_count} 吊销",
                icon="gpp_bad",
                tone="danger" if invalid_count else "default",
                compact=True,
            )

    def _render_expiry_alerts(self):
        """渲染即将到期告警；告警源失败不影响主证书库。"""

        try:
            expiring = self.cert_service.get_expiring()
        except Exception as exc:
            logger.exception("加载临期证书告警失败")
            alert_card.show(
                level="warning",
                title="临期证书检查不可用",
                message=str(exc),
            )
            return
        if expiring:
            names = "、".join(str(cert.get("common_name") or "—") for cert in expiring[:5])
            alert_card.show(
                level="warning",
                title=f"{len(expiring)} 个证书将到期",
                message=names + ("…" if len(expiring) > 5 else ""),
            )

    def _handle_search(self):
        if self.grid_view is None:
            return
        query = str(self.search_input.value or "").strip() if self.search_input else ""
        normalized = query.casefold()
        matched = [
            row
            for row in self.all_rows
            if not normalized or self._row_matches_query(row, normalized)
        ]
        self.grid_view.grid.options["quickFilterText"] = query
        self.grid_view.grid.update()
        self.grid_view.summary.text = (
            f"匹配 {len(matched)} / 共 {len(self.all_rows)} 个证书"
            if query
            else f"共 {len(self.all_rows)} 个证书"
        )
        if self.search_state_container is not None:
            self.search_state_container.clear()
            if query and not matched:
                self.grid_view.container.set_visibility(False)
                with self.search_state_container:
                    no_match_state(
                        description=f"没有证书匹配“{query}”。",
                        on_clear=self._clear_search,
                    )
            else:
                self.grid_view.container.set_visibility(True)

    @staticmethod
    def _row_matches_query(row: dict, query: str) -> bool:
        return any(
            query in str(row.get(field) or "").casefold()
            for field in ("common_name", "serial", "status_label", "expires_display")
        )

    def _clear_search(self) -> None:
        if self.search_input is not None:
            self.search_input.set_value("")
        self._handle_search()

    async def _on_selection_changed(self, _event=None) -> None:
        if self.grid_view is None:
            return
        rows = await self.grid_view.get_selected_rows()
        self.selected_certs = {
            str(row.get("common_name") or "")
            for row in rows
            if row.get("common_name") not in {None, "", "—"}
        }
        self._sync_bulk_state()

    def _sync_bulk_state(self) -> None:
        if self.bulk_bar is not None:
            self.bulk_bar.set_selected_count(len(self.selected_certs))
        enabled = bool(self.selected_certs)
        for button in (self.bulk_renew_button, self.bulk_revoke_button):
            if button is None:
                continue
            if enabled:
                button.enable()
            else:
                button.disable()

    def _on_grid_cell_clicked(self, event) -> None:
        args = getattr(event, "args", None) or {}
        if not isinstance(args, dict):
            return
        col_id = args.get("colId")
        if col_id is None and isinstance(args.get("column"), dict):
            col_id = args["column"].get("colId")
        if col_id != "actions":
            return
        row = args.get("data") or {}
        if isinstance(row, dict) and row.get("common_name"):
            self._show_cert_actions(row)

    def _show_cert_actions(self, row: dict) -> None:
        common_name = str(row.get("common_name") or "")
        status = str(row.get("status") or "unknown")
        actionable = status not in {"revoked", "expired"}

        with ui.dialog() as dialog, ui.card().classes("vpn-form-dialog"):
            with ui.row().classes("w-full items-start justify-between no-wrap"):
                with ui.column().classes("gap-1 min-w-0"):
                    ui.label(common_name).classes("vpn-form-dialog__title")
                    status_badge(
                        row.get("status_tone", "unknown"),
                        label=str(row.get("status_label") or "未知"),
                    )
                ui.button(icon="close", on_click=dialog.close).props("flat round dense")

            structured_value(
                row.get("serial") or "—",
                kind="serial",
                copyable=bool(row.get("serial")),
                copy_message="证书序列号已复制",
            )
            ui.label(f"到期日期：{row.get('expires_display') or '—'}").classes(
                "section-caption"
            )

            def invoke(callback):
                dialog.close()
                return callback()

            with ui.row().classes("w-full gap-2 flex-wrap"):
                secondary_action(
                    "续签",
                    icon="autorenew",
                    on_click=lambda: invoke(lambda: self._handle_renew(common_name)),
                    disabled=not actionable,
                    tooltip=None if actionable else "已吊销或已过期证书不可从此入口续签",
                )
                danger_action(
                    "吊销",
                    icon="block",
                    on_click=lambda: invoke(lambda: self._handle_revoke(common_name)),
                    disabled=not actionable,
                    tooltip=None if actionable else "该证书当前状态不可吊销",
                )
        dialog.open()

    def _begin_operation(self) -> bool:
        if self._operation_guard.try_start():
            return True
        ui.notify("证书操作正在执行，请稍候", type="info")
        return False

    def _finish_operation(self) -> None:
        self._operation_guard.finish()

    def _handle_renew(self, common_name: str):
        confirm_dialog.show(
            message=f"续签 {common_name} 的证书？",
            impact="续签完成后会刷新证书库；原客户端配置可能需要重新下发。",
            object_count=1,
            on_confirm=lambda: self._do_renew(common_name),
            title="续签证书",
            danger_level="normal",
        )

    def _do_renew(self, common_name: str):
        if not self._begin_operation():
            return
        try:
            success = self.cert_service.renew(common_name)
            ui.notify(
                f"{'已续签' if success else '续签失败'}: {common_name}",
                type="positive" if success else "negative",
            )
        except Exception as exc:
            logger.exception("续签证书失败 cn=%s", common_name)
            ui.notify(f"续签失败: {common_name}: {exc}", type="negative")
        finally:
            self._finish_operation()
        self._refresh_cert_list()

    def _handle_revoke(self, common_name: str):
        confirm_dialog.show(
            message=f"确定吊销 {common_name}？",
            impact="吊销后该证书将无法继续连接 VPN，并会重新生成 CRL。",
            object_count=1,
            on_confirm=lambda: self._do_revoke(common_name),
            title="吊销证书",
            danger_level="danger",
        )

    def _do_revoke(self, common_name: str):
        if not self._begin_operation():
            return
        try:
            success = self.cert_service.revoke(common_name)
            ui.notify(
                f"{'已吊销' if success else '吊销失败'}: {common_name}",
                type="positive" if success else "negative",
            )
        except Exception as exc:
            logger.exception("吊销证书失败 cn=%s", common_name)
            ui.notify(f"吊销失败: {common_name}: {exc}", type="negative")
        finally:
            self._finish_operation()
        self._refresh_cert_list()

    def _batch_renew(self):
        if not self.selected_certs:
            ui.notify("请先选择证书", type="warning")
            return
        confirm_dialog.show(
            message="确定续签所选证书？",
            impact="会逐一处理所选证书并在结束后汇总成功与失败数量。",
            object_count=len(self.selected_certs),
            on_confirm=self._do_batch_renew,
            title="批量续签",
            danger_level="normal",
        )

    def _do_batch_renew(self):
        if not self._begin_operation():
            return
        success_count = 0
        failed: list[str] = []
        try:
            for cn in list(self.selected_certs):
                try:
                    if self.cert_service.renew(cn):
                        success_count += 1
                    else:
                        failed.append(cn)
                except Exception as exc:
                    logger.exception("批量续签失败 cn=%s", cn)
                    failed.append(f"{cn}: {exc}")
        finally:
            self._finish_operation()
        self.selected_certs.clear()
        ui.notify(
            f"批量续签完成：成功 {success_count}，失败 {len(failed)}",
            type="positive" if not failed else "warning",
        )
        self._refresh_cert_list()

    def _batch_revoke(self):
        if not self.selected_certs:
            ui.notify("请先选择证书", type="warning")
            return
        confirm_dialog.show(
            message="确定吊销所选证书？",
            impact="吊销不可恢复，相关用户将不能再使用这些证书连接 VPN。",
            object_count=len(self.selected_certs),
            on_confirm=self._do_batch_revoke,
            title="批量吊销",
            danger_level="danger",
        )

    def _do_batch_revoke(self):
        if not self._begin_operation():
            return
        success_count = 0
        failed: list[str] = []
        try:
            for cn in list(self.selected_certs):
                try:
                    if self.cert_service.revoke(cn):
                        success_count += 1
                    else:
                        failed.append(cn)
                except Exception as exc:
                    logger.exception("批量吊销失败 cn=%s", cn)
                    failed.append(f"{cn}: {exc}")
        finally:
            self._finish_operation()
        self.selected_certs.clear()
        ui.notify(
            f"批量吊销完成：成功 {success_count}，失败 {len(failed)}",
            type="positive" if not failed else "warning",
        )
        self._refresh_cert_list()

    def _handle_regen_crl(self):
        confirm_dialog.show(
            message="确定重新生成 CRL？",
            impact="新的 CRL 会用于后续证书吊销检查。",
            object_count=1,
            on_confirm=self._do_regen_crl,
            title="更新 CRL",
            danger_level="warning",
        )

    def _do_regen_crl(self):
        if not self._begin_operation():
            return
        try:
            self.cert_service._get_wrapper().gen_crl()
            ui.notify("CRL 已更新", type="positive")
        except Exception as exc:
            logger.exception("CRL 生成失败")
            ui.notify(f"CRL 生成失败: {exc}", type="negative")
        finally:
            self._finish_operation()
        self._refresh_cert_list()

    def _show_rebuild_ca_confirm(self):
        confirm_dialog.show(
            message="确定重建 CA 根证书？",
            impact="现有客户端证书将全部失效，所有用户都需要重新签发并下发配置。",
            confirm_text="REBUILD",
            danger_level="danger",
            on_confirm=self._do_rebuild_ca_confirmed,
            title="重建 CA 根",
        )

    def _do_rebuild_ca_confirmed(self):
        if not self._begin_operation():
            return
        try:
            wrapper = self.cert_service._get_wrapper()
            wrapper.build_ca()
            wrapper.gen_crl()
            ui.notify("CA 已重建；请让用户重新拉取配置", type="positive")
        except Exception as exc:
            logger.exception("CA 重建失败")
            ui.notify(f"CA 重建失败: {exc}", type="negative")
        finally:
            self._finish_operation()
        self._refresh_cert_list()

    # ---- 旧接口兼容 ----

    @staticmethod
    def _short_serial(serial: str) -> str:
        """兼容旧调用入口。"""

        return short_serial(serial)

    def _refresh_table(self):
        self._refresh_cert_list()

    def _toggle_advanced_panel(self):
        self.advanced_open = not self.advanced_open
        if self.advanced_body is not None:
            self.advanced_body.set_visibility(self.advanced_open)

    def _do_rebuild_ca(self, dialog, confirm_text: str):
        if confirm_text != "REBUILD":
            ui.notify("请输入 REBUILD 确认", type="warning")
            return
        dialog.close()
        self._do_rebuild_ca_confirmed()

    def _toggle_cert_selection(self, common_name: str, checked: bool):
        if checked:
            self.selected_certs.add(common_name)
        else:
            self.selected_certs.discard(common_name)
        self._sync_bulk_state()

    def _toggle_select_all(self, checked: bool):
        if checked:
            self.selected_certs.update(
                row["common_name"]
                for row in self.all_rows
                if row.get("common_name") not in {None, "", "—"}
            )
        else:
            self.selected_certs.clear()
        self._sync_bulk_state()

    def _sync_select_all(self):
        self._sync_bulk_state()
