# -*- coding: utf-8 -*-
"""Firewall 对端远端页面行为 mixin。

仅承载 `FirewallPage` 的对端链 UI/交互方法；状态仍由主页面初始化，
SSH、iptables 与服务层调用路径保持不变。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from nicegui import background_tasks, run, ui
from pydantic import ValidationError

from app.models.firewall import FirewallRule
from app.services.firewall.iptables_mgr import IptablesManager
from app.services.firewall.simple_rule_import import remote_rests_from_create_fields
from app.services.group.crud import GroupService
from app.services.peer_instance import remote_chain_cache
from app.services.peer_instance.peer_ssh_connect import peer_row_has_usable_ssh_auth
from app.services.peer_instance.service import PeerService
from app.services.user.crud import UserService
from app.ui.adapters.firewall import (
    adapt_remote_cache_status,
    adapt_remote_peer_capability,
    adapt_remote_rule_rows,
    parse_remote_rule_hints,
)
from app.ui.components import confirm_dialog
from app.ui.components.actions import danger_action, primary_action, secondary_action
from app.ui.components.data_grid import render_data_grid
from app.ui.components.form_dialog import create_form_dialog
from app.ui.components.firewall_rule_form import render_firewall_rule_create_fields
from app.ui.components.states import empty_state, error_state, loading_state
from app.ui.components.status import status_badge
from app.ui.firewall_helpers import (
    move_order,
    parse_source_text_for_remote_save as _parse_source_text_for_remote_save,
    parse_user_source_for_create as _parse_user_source_for_create,
    read_ccd_vpn_ips as _read_ccd_vpn_ips,
    remote_grid_columns,
    remote_row_to_edit_form_dict as _remote_row_to_edit_form_dict,
    rows_from_iptables_paste,
    rows_from_remote_import_payload,
    rows_from_remote_import_text_mixed,
    short_peer_id,
)

logger = logging.getLogger(__name__)


class FirewallRemoteMixin:
    """`FirewallPage` 的对端远端链交互实现。"""

    def _on_enter_remote_tab(self) -> None:
        """进入「对端远端」：同步下拉后必须先 SSH 取对端真值落库，再谈编辑（与换节点时一致）。"""
        self._ensure_remote_tab_synced_with_peers()
        self._refresh_remote_view()
        self._schedule_remote_fetch_via_ssh()

    def _ensure_remote_tab_synced_with_peers(self) -> None:
        """根据 :meth:`PeerService.list_all` 重建或更新对端下拉的选项（避免首屏/新建对端后 UI 不刷新）。"""
        if not self._remote_tab_shell:
            return
        opts = self._peer_options_for_select()
        if opts and self._remote_peer_select is None:
            self._remote_tab_shell.clear()
            with self._remote_tab_shell:
                self._build_remote_tab_static()
            return
        if not opts and self._remote_peer_select is not None:
            self._remote_tab_shell.clear()
            with self._remote_tab_shell:
                self._build_remote_tab_static()
            return
        if self._remote_peer_select is not None and opts:
            self._remote_peer_select.options = opts
            cur = str(self._remote_peer_select.value or "").strip()
            if cur not in opts:
                self._remote_peer_select.value = next(iter(opts))
            self._remote_peer_select.update()

    def _on_remote_peer_select_change(self) -> None:
        """对端节点变化：以新机为准，清除上一个对端的列筛选后重新拉取。"""
        self._remote_fetch_generation += 1
        self._remote_fetch_running_peer = None
        self._remote_filter_model = {}
        self._remote_grid_sort_active = False
        self._refresh_remote_view()
        self._schedule_remote_fetch_via_ssh()

    def _schedule_remote_fetch_via_ssh(self) -> None:
        """在「对端远端」对当前选中的对端：SSH 拉取 → ``record_from_fetch`` 落库；失败 ``notify`` + ``logger``。"""
        if self._center_tab_active:
            return
        target = self._active_remote_peer_id()
        if not target:
            return
        if self._remote_fetch_running_peer == target:
            logger.info("对端链正在拉取，跳过重复请求 peer=%s", target)
            return
        self._remote_fetch_generation += 1
        generation = self._remote_fetch_generation
        self._remote_fetch_running_peer = target
        self._remote_fetch_error_by_peer.pop(target, None)
        self._refresh_remote_toolbar_state()
        try:
            background_tasks.create(
                self._remote_fetch_chain_via_ssh(target, generation),
                name="firewall-remote-fetch",
            )
        except RuntimeError:
            if self._remote_fetch_running_peer == target:
                self._remote_fetch_running_peer = None
            self._refresh_remote_toolbar_state()
            return

    async def _remote_fetch_chain_via_ssh(self, target: str, generation: int) -> None:
        """用对端上当前链覆盖本地工作副本（``record_from_fetch``）；不判断此前是否已有缓存，保证与对端一致。"""
        if self._center_tab_active or generation != self._remote_fetch_generation:
            return
        try:
            snap = await run.io_bound(
                self._peer_service.fetch_remote_peer_filter_chain_snapshot, target
            )
        except Exception as exc:
            if self._center_tab_active or generation != self._remote_fetch_generation:
                return
            logger.error("SSH 拉取对端链失败 peer=%s: %s", target, exc)
            self._remote_fetch_error_by_peer[target] = str(exc)
            self._notify_for_page(f"从对端拉取失败: {exc}", type="negative")
            self._refresh_remote_toolbar_state()
            return
        finally:
            if self._remote_fetch_running_peer == target:
                self._remote_fetch_running_peer = None
            self._refresh_remote_toolbar_state()
        if self._center_tab_active or generation != self._remote_fetch_generation:
            return
        remote_chain_cache.record_from_fetch(snap, target)
        self._remote_fetch_error_by_peer.pop(target, None)
        show = target[:8] if len(target) > 8 else target
        logger.debug("对端链已拉取并落库 peer=%s", show)
        if self._active_remote_peer_id() != target:
            return
        self._refresh_remote_view()

    def _build_remote_tab_static(self) -> None:
        """构建对端选择、SSH/缓存状态、拉取推送工具栏和规则视图容器。"""

        opts = self._peer_options_for_select()

        with ui.element("section").classes(
            "mgmt-panel firewall-remote-control w-full"
        ):
            with ui.row().classes(
                "firewall-remote-control-row "
                "w-full items-center gap-3 flex-wrap"
            ):
                if opts:
                    first_id = next(iter(opts))
                    self._remote_peer_select = (
                        ui.select(
                            opts,
                            label="对端节点",
                            value=first_id,
                            with_input=True,
                        )
                        .classes(
                            "firewall-remote-peer-select "
                            "min-w-[18rem] flex-1"
                        )
                        .props("outlined dense")
                    )
                    self._remote_peer_select.on_value_change(
                        lambda _event: self._on_remote_peer_select_change()
                    )
                else:
                    self._remote_peer_select = None
                    ui.label("暂无对端站点").classes("mgmt-section-title")

                self._remote_status_container = ui.row().classes(
                    "items-center gap-2"
                )
                self._remote_cache_status_container = ui.row().classes(
                    "items-center gap-2"
                )

                self._remote_pull_button = secondary_action(
                    "从对端拉取",
                    icon="cloud_download",
                    on_click=self._schedule_remote_fetch_via_ssh,
                    disabled=not bool(opts),
                )
                self._remote_push_button = primary_action(
                    "推送到对端",
                    icon="cloud_upload",
                    on_click=self._confirm_remote_push,
                    disabled=True,
                )

            self._remote_cache_meta_label = ui.label(
                "拉取：— · 推送：—"
            ).classes(
                "section-caption firewall-remote-cache-meta q-mt-sm"
            )

        self._remote_list_container = ui.column().classes(
            "firewall-remote-list-stretch firewall-remote-data-region "
            "w-full min-h-0 gap-0 h-full"
        )

        self._refresh_remote_toolbar_state()

    def _selected_remote_peer_row(self) -> dict[str, Any] | None:
        """返回当前对端记录。"""

        peer_id = self._active_remote_peer_id()
        if not peer_id:
            return None
        try:
            row = self._peer_service.get(peer_id)
        except Exception:
            logger.exception("读取当前对端失败 peer=%s", peer_id)
            return None
        return dict(row) if row else None

    def _refresh_remote_toolbar_state(self) -> None:
        """按 SSH 条件、拉取忙碌和缓存状态刷新远端工具栏。"""

        peer_id = self._active_remote_peer_id()
        peer = self._selected_remote_peer_row()
        fetch_running = bool(
            peer_id and self._remote_fetch_running_peer == peer_id
        )
        try:
            has_auth = bool(
                peer and peer_row_has_usable_ssh_auth(peer)
            )
        except Exception:
            logger.exception("判断对端 SSH 凭据失败 peer=%s", peer_id)
            has_auth = False

        capability = adapt_remote_peer_capability(
            peer,
            has_ssh_auth=has_auth,
            fetch_running=fetch_running,
        )
        cache = (
            remote_chain_cache.read_remote_chain_cache(peer_id)
            if peer_id
            else None
        )
        cache_view = adapt_remote_cache_status(
            cache,
            fetch_running=fetch_running,
        )
        fetch_error = str(
            self._remote_fetch_error_by_peer.get(peer_id, "")
        ).strip()

        if self._remote_status_container is not None:
            self._remote_status_container.clear()
            with self._remote_status_container:
                status_badge(
                    (
                        "failed"
                        if fetch_error
                        else str(capability["status_tone"])
                    ),
                    label=(
                        "拉取失败"
                        if fetch_error
                        else str(capability["status_label"])
                    ),
                    tooltip=fetch_error or str(capability["reason"] or ""),
                )

        if self._remote_cache_status_container is not None:
            self._remote_cache_status_container.clear()
            with self._remote_cache_status_container:
                status_badge(
                    str(cache_view["status_tone"]),
                    label=str(cache_view["status_label"]),
                    tooltip=str(cache_view["last_sync_error"] or ""),
                )

        if self._remote_cache_meta_label is not None:
            self._remote_cache_meta_label.text = (
                f"拉取：{cache_view['last_pulled_at']} · "
                f"推送：{cache_view['last_pushed_at']} · "
                f"本地 {cache_view['row_count']} 条"
            )

        if self._remote_pull_button is not None:
            if capability["can_fetch"]:
                self._remote_pull_button.enable()
            else:
                self._remote_pull_button.disable()
            if fetch_running:
                self._remote_pull_button.props(add="loading")
            else:
                self._remote_pull_button.props(remove="loading")

        if self._remote_push_button is not None:
            any_push_running = self._remote_push_running_peer is not None
            push_running = bool(
                peer_id and self._remote_push_running_peer == peer_id
            )
            can_push = (
                bool(capability["can_push"])
                and bool(cache_view["can_push"])
                and not any_push_running
            )
            if can_push:
                self._remote_push_button.enable()
            else:
                self._remote_push_button.disable()
            if push_running:
                self._remote_push_button.props(add="loading")
            else:
                self._remote_push_button.props(remove="loading")

    def _active_remote_peer_id(self) -> str:
        if not self._remote_peer_select:
            return ""
        return str(self._remote_peer_select.value or "").strip()

    async def _on_remote_pull(self) -> None:
        if not self._require_remote_strategy_tab():
            return
        pid = self._active_remote_peer_id()
        if not pid:
            self._notify_for_page("请选择对端", type="warning")
            return
        try:
            snap = await run.io_bound(self._peer_service.fetch_remote_peer_filter_chain_snapshot, pid)
        except Exception as exc:
            logger.error("拉取对端链失败 peer=%s: %s", pid, exc)
            self._notify_for_page(f"拉取失败: {exc}", type="negative")
            return
        remote_chain_cache.record_from_fetch(snap, pid)
        self._notify_for_page("已拉取", type="positive")
        self._refresh_remote_view()

    async def _apply_remote_cache_to_peer(self, peer_id: str) -> bool:
        """将当前本地工作副本（库内 ``rows``）经 SSH 写回对端机链；与拉取成对，保「真值→改库→推对端」。

        本方法不调用 :func:`mark_pending_apply`（避免覆盖「改前」备份）。除
        :meth:`_on_remote_push` 在发起 SSH 前会自行 ``mark_pending`` 外，其余调用方须在
        改库后、调用本方法前已对该 peer 执行过 ``mark_pending_apply`` 且 ``rows`` 与库内一致。
        成功返回 True；失败时落库错误并 :meth:`_notify_for_page`。"""
        cache = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        chain = str(cache.get("chain") or IptablesManager.peer_chain_name_for_id(peer_id))
        ce = bool(cache.get("chain_exists"))
        rows = list(cache.get("rows") or [])
        rests = remote_chain_cache.rests_to_push_list(rows)  # type: ignore[arg-type]
        try:
            await run.io_bound(self._peer_service.apply_remote_peer_filter_chain_rests, peer_id, rests)
            remote_chain_cache.record_after_apply(peer_id, chain, bool(ce), rows, True, None)
        except Exception as exc:
            logger.error("写回对端链失败 peer=%s: %s", peer_id, exc)
            remote_chain_cache.record_after_apply(peer_id, chain, bool(ce), rows, False, str(exc))
            self._notify_for_page(f"写回失败: {exc}", type="negative")
            return False
        return True

    def _confirm_remote_push(self) -> None:
        """确认后推送当前工作副本，避免误覆盖远端链。"""

        if not self._require_remote_strategy_tab():
            return
        peer_id = self._active_remote_peer_id()
        if not peer_id:
            self._notify_for_page("请选择对端", type="warning")
            return
        if self._remote_push_running_peer is not None:
            self._notify_for_page(
                "已有对端写回正在执行，请勿重复提交",
                type="warning",
            )
            return
        cache = remote_chain_cache.read_remote_chain_cache(peer_id)
        if not cache:
            self._notify_for_page(
                "尚无本地工作副本，请先从对端拉取或导入",
                type="warning",
            )
            return
        rows = list(cache.get("rows") or [])
        confirm_dialog.show(
            f"推送当前 {len(rows)} 条规则到对端？",
            impact=(
                "将通过 SSH 重写当前对端用户链；"
                "失败时本地工作副本保留错误状态并按 prepush 备份恢复。"
            ),
            object_count=len(rows),
            danger_level="warning",
            on_confirm=lambda: self._schedule_remote_coro(
                self._on_remote_push()
            ),
        )

    async def _on_remote_push(self) -> bool:
        """推送当前工作副本，带页面级重复提交保护。"""

        if not self._require_remote_strategy_tab():
            return False
        peer_id = self._active_remote_peer_id()
        if not peer_id:
            self._notify_for_page("请选择对端", type="warning")
            return False
        if self._remote_push_running_peer is not None:
            self._notify_for_page(
                "已有对端写回正在执行，请勿重复提交",
                type="warning",
            )
            return False

        cache = remote_chain_cache.read_remote_chain_cache(peer_id)
        if not cache:
            self._notify_for_page(
                "尚无本地工作副本，请先从对端拉取或导入",
                type="warning",
            )
            return False

        self._remote_push_running_peer = peer_id
        self._refresh_remote_toolbar_state()
        chain = str(
            cache.get("chain")
            or IptablesManager.peer_chain_name_for_id(peer_id)
        )
        chain_exists = bool(cache.get("chain_exists"))
        rows = list(cache.get("rows") or [])

        try:
            remote_chain_cache.mark_pending_apply(
                peer_id,
                chain,
                chain_exists,
                rows,
            )
            ok = await self._apply_remote_cache_to_peer(peer_id)
            if ok:
                self._notify_for_page("已写回对端", type="positive")
            return ok
        finally:
            if self._remote_push_running_peer == peer_id:
                self._remote_push_running_peer = None
            self._refresh_remote_toolbar_state()
            self._refresh_remote_view()

    async def _on_remote_row_enabled_change(self, idx: int, e) -> None:
        """兼容旧开关入口。"""

        await self._set_remote_row_enabled(idx, bool(e.value))

    @staticmethod
    def _rows_from_remote_import_payload(raw: dict) -> list[dict]:
        return rows_from_remote_import_payload(raw)

    @staticmethod
    def _rows_from_iptables_paste(text: str, chain: str) -> list[dict]:
        return rows_from_iptables_paste(text, chain)

    @staticmethod
    def _rows_from_remote_import_text_mixed(
        text: str, peer_id: str, cname: str
    ) -> tuple[list[dict], str, bool]:
        return rows_from_remote_import_text_mixed(text, peer_id, cname)

    def _schedule_remote_coro(self, coro) -> None:
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            pass

    @staticmethod
    def _remote_grid_columns(*, can_reorder: bool = False) -> list[dict[str, Any]]:
        return remote_grid_columns(can_reorder=can_reorder)

    def _refresh_remote_view(self) -> None:
        """按当前对端缓存刷新 AG Grid；拉取失败时保留可用旧缓存。"""

        if not self._remote_list_container:
            return

        self._refresh_remote_toolbar_state()
        self._remote_list_container.clear()
        self._remote_grid_view = None
        self._visible_remote_indices = []

        peer_id = self._active_remote_peer_id()
        if not peer_id:
            self._remote_grid_rows = []
            self._selected_remote_indices.clear()
            with self._remote_list_container:
                empty_state(
                    "请先选择对端",
                    description="选择对端后会自动尝试通过 SSH 拉取远端链真值。",
                    icon="hub",
                )
            return

        cache = remote_chain_cache.read_remote_chain_cache(peer_id)
        fetch_running = self._remote_fetch_running_peer == peer_id
        fetch_error = str(
            self._remote_fetch_error_by_peer.get(peer_id, "")
        ).strip()

        if cache is None:
            self._remote_grid_rows = []
            self._selected_remote_indices.clear()
            with self._remote_list_container:
                if fetch_running:
                    loading_state("正在从对端拉取规则…")
                elif fetch_error:
                    error_state(
                        fetch_error,
                        title="拉取对端规则失败",
                        on_retry=self._schedule_remote_fetch_via_ssh,
                    )
                else:
                    empty_state(
                        "尚无本地工作副本",
                        description="点击“从对端拉取”读取真实链；新建或导入后也可以建立工作副本。",
                        action_label="从对端拉取",
                        on_action=self._schedule_remote_fetch_via_ssh,
                        icon="cloud_download",
                    )
            return

        raw_rows = list(cache.get("rows") or [])
        rows = adapt_remote_rule_rows(raw_rows, peer_id=peer_id)
        for row in rows:
            row["actions"] = "操作"
        self._remote_grid_rows = rows
        self._visible_remote_indices = [int(row["index"]) for row in rows]

        valid_indices = set(self._visible_remote_indices)
        self._selected_remote_indices.intersection_update(valid_indices)
        self._sync_action_bar_count()

        with self._remote_list_container:
            if fetch_error:
                with ui.element("div").classes("w-full q-mb-sm"):
                    error_state(
                        fetch_error,
                        title="本次拉取失败，当前仍显示上次缓存",
                        on_retry=self._schedule_remote_fetch_via_ssh,
                    )

            if not rows:
                empty_state(
                    "远端链暂无规则",
                    description="当前工作副本为空；可以新建、导入或再次从对端拉取。",
                    action_label="新建规则",
                    on_action=self._show_create_remote_line_dialog,
                    icon="shield",
                )
                return

            selected_ids = {
                f"{peer_id}:{index}"
                for index in self._selected_remote_indices
            }
            peer = self._selected_remote_peer_row()
            try:
                has_auth = bool(peer and peer_row_has_usable_ssh_auth(peer))
            except Exception:
                logger.exception("判断对端排序 SSH 条件失败 peer=%s", peer_id)
                has_auth = False
            self._remote_reorder_allowed = bool(
                has_auth
                and not fetch_running
                and not fetch_error
                and self._remote_push_running_peer is None
            )

            self._remote_grid_view = render_data_grid(
                self._remote_grid_columns(
                    can_reorder=self._remote_reorder_allowed
                ),
                rows,
                row_id="id",
                page_size=50,
                pagination=not self._remote_reorder_allowed,
                selectable=True,
                selected_ids=selected_ids,
                on_cell_click=self._on_remote_grid_cell_click,
                row_drag_managed=self._remote_reorder_allowed,
                show_summary=False,
            )
            self._remote_grid_view.grid.on(
                "selectionChanged",
                self._on_remote_grid_selection_changed,
            )
            self._remote_grid_view.grid.on(
                "filterChanged",
                self._on_remote_grid_filter_changed,
            )
            self._remote_grid_view.grid.on(
                "sortChanged",
                self._on_remote_grid_filter_changed,
            )
            if self._remote_filter_model:
                self._schedule_grid_filter_model(
                    self._remote_grid_view.grid,
                    self._remote_filter_model,
                    "firewall-remote-filter-restore",
                )
            if self._remote_reorder_allowed:
                self._remote_grid_view.grid.on(
                    "rowDragEnd",
                    self._on_remote_grid_row_drag_end,
                )

    async def _on_remote_grid_selection_changed(self, *_args) -> None:
        """同步 AG Grid 选择到现有远端批量操作索引集合。"""

        if self._remote_grid_view is None:
            self._selected_remote_indices.clear()
            self._sync_action_bar_count()
            return
        selected = await self._remote_grid_view.get_selected_rows()
        self._selected_remote_indices = {
            int(row.get("index"))
            for row in selected
            if isinstance(row.get("index"), int)
        }
        self._sync_action_bar_count()

    async def _on_remote_grid_filter_changed(self, *_args) -> None:
        """保存对端列筛选/排序状态。"""

        view = self._remote_grid_view
        if view is None:
            return
        try:
            model = await view.grid.run_grid_method("getFilterModel")
            self._remote_filter_model = model if isinstance(model, dict) else {}
            column_state = await view.grid.run_grid_method("getColumnState")
            self._remote_grid_sort_active = any(
                isinstance(column, dict) and column.get("sort")
                for column in (column_state or [])
            )
            displayed = int(await view.grid.run_grid_method("getDisplayedRowCount"))
        except Exception as exc:
            logger.debug("同步对端规则筛选状态失败: %s", exc)
            return

    async def _on_remote_grid_cell_click(self, event) -> None:
        """复制原始/结构化字段，操作列暂复用现有编辑表单。"""

        args = getattr(event, "args", None) or {}
        if not isinstance(args, dict):
            return
        data = args.get("data") or {}
        if not isinstance(data, dict):
            return

        col_id = str(args.get("colId") or "")
        index = data.get("index")
        if col_id == "actions" and isinstance(index, int):
            self._show_remote_rule_actions(index)
            return

        value = ""
        if col_id == "raw_rest":
            value = str(data.get("raw_rest") or "")
        elif col_id in {"source", "dest", "dest_port"}:
            value = str(data.get(col_id) or "")
        if value and value not in {"—", "*"}:
            await self._copy_text_to_clipboard(value)

    def _show_remote_rule_actions(self, index: int) -> None:
        """远端单行操作面板；编辑路径继续使用现有写回逻辑。"""

        peer_id = self._active_remote_peer_id()
        cache = (
            remote_chain_cache.read_remote_chain_cache(peer_id)
            if peer_id
            else None
        )
        rows = list(cache.get("rows") or []) if cache else []
        if not (0 <= index < len(rows)):
            self._notify_for_page(
                "本地工作副本已变化，请刷新后重试",
                type="warning",
            )
            return
        row = dict(rows[index])
        enabled = bool(row.get("enabled", True))

        with ui.dialog() as dialog, ui.card().classes(
            "vpn-form-dialog firewall-action-dialog w-full max-w-md"
        ):
            ui.label(f"对端规则 #{index + 1}").classes("text-h6")
            ui.label(str(row.get("rest") or "")).classes(
                "font-mono text-caption break-all"
            )
            with ui.column().classes("w-full gap-2 q-mt-sm"):
                secondary_action(
                    "编辑",
                    icon="edit",
                    on_click=lambda: (
                        dialog.close(),
                        self._show_edit_remote_line_dialog(index, row),
                    ),
                )
                secondary_action(
                    "停用" if enabled else "启用",
                    icon="pause_circle" if enabled else "play_circle",
                    on_click=lambda: (
                        dialog.close(),
                        self._schedule_remote_coro(
                            self._set_remote_row_enabled(index, not enabled)
                        ),
                    ),
                )
                secondary_action(
                    "复制原始规则",
                    icon="content_copy",
                    on_click=lambda: self._schedule_remote_text_copy(
                        str(row.get("rest") or "")
                    ),
                )
                danger_action(
                    "删除",
                    icon="delete",
                    on_click=lambda: (
                        dialog.close(),
                        self._confirm_delete_remote_line(index),
                    ),
                )
            secondary_action("关闭", on_click=dialog.close)

        dialog.open()

    def _schedule_remote_text_copy(self, text: str) -> None:
        """从同步菜单安全调度原始规则复制。"""

        try:
            background_tasks.create(
                self._copy_text_to_clipboard(str(text or "")),
                name="firewall-copy-remote-rule",
            )
        except RuntimeError as exc:
            logger.error("调度对端规则复制失败: %s", exc)
            self._notify_for_page("复制任务未能启动", type="negative")

    async def _set_remote_row_enabled(
        self,
        index: int,
        enabled: bool,
    ) -> bool:
        """按显式目标状态切换单行并立即写回。"""

        if not self._require_remote_strategy_tab():
            return False
        peer_id = self._active_remote_peer_id()
        if not peer_id:
            self._notify_for_page("请选择对端", type="warning")
            return False

        cache = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        rows = list(cache.get("rows") or [])
        if not (0 <= index < len(rows)):
            self._notify_for_page(
                "本地数据已变化，请重拉或切换对端",
                type="warning",
            )
            self._refresh_remote_view()
            return False

        if bool(rows[index].get("enabled", True)) == bool(enabled):
            return True

        rows[index]["enabled"] = bool(enabled)
        chain = str(
            cache.get("chain")
            or IptablesManager.peer_chain_name_for_id(peer_id)
        )
        remote_chain_cache.mark_pending_apply(
            peer_id,
            chain,
            bool(cache.get("chain_exists", True)),
            rows,
        )
        ok = await self._apply_remote_cache_to_peer(peer_id)
        if ok:
            self._notify_for_page(
                "已启用并写回对端"
                if enabled
                else "已停用并写回对端",
                type="positive",
            )
        self._refresh_remote_view()
        return ok

    def _peer_id_short_label(self, pid: str) -> str:
        return short_peer_id(pid)

    @staticmethod
    def _move_order(items: list[Any], old_index: int, new_index: int) -> list[Any]:
        return move_order(items, old_index, new_index)

    def _on_remote_grid_row_drag_end(self, event) -> None:
        """AG Grid 拖动结束后按缓存索引生成新顺序并立即写回。"""

        if (
            not self._remote_reorder_allowed
            or self._remote_filter_model
            or self._remote_grid_sort_active
        ):
            self._notify_for_page(
                "当前正在拉取/推送、筛选/排序中或 SSH 不可用，不能调整顺序",
                type="warning",
            )
            self._refresh_remote_view()
            return

        args = getattr(event, "args", None) or {}
        if not isinstance(args, dict):
            self._refresh_remote_view()
            return

        data = args.get("data")
        if not isinstance(data, dict):
            node = args.get("node")
            data = node.get("data") if isinstance(node, dict) else None
        row_index = (data or {}).get("index")
        if not isinstance(row_index, int):
            try:
                row_index = int(str(args.get("rowId") or "").rsplit(":", 1)[-1])
            except (TypeError, ValueError):
                self._refresh_remote_view()
                return
        if row_index not in self._visible_remote_indices:
            self._refresh_remote_view()
            return

        old_position = self._visible_remote_indices.index(row_index)
        try:
            target_index = args.get("toIndex")
            if target_index is None:
                target_index = args.get("overIndex", args.get("rowIndex", old_position))
            new_position = int(target_index)
        except (TypeError, ValueError):
            new_position = old_position
        # AG Grid reports -1 when the pointer is below the final row.
        if new_position < 0:
            new_position = len(self._visible_remote_indices) - 1
        else:
            new_position = min(new_position, len(self._visible_remote_indices) - 1)
        new_order = self._move_order(
            self._visible_remote_indices,
            old_position,
            new_position,
        )
        if new_order and new_order != self._visible_remote_indices:
            self._schedule_remote_coro(
                self._do_remote_reorder_async(new_order)
            )
        else:
            self._refresh_remote_view()

    async def _do_remote_reorder_async(self, new_order: list[int]) -> None:
        if not self._require_remote_strategy_tab():
            return
        pid = self._active_remote_peer_id()
        if not pid:
            return
        c2 = remote_chain_cache.read_remote_chain_cache(pid) or {}
        cname = IptablesManager.peer_chain_name_for_id(pid)
        rws = list(c2.get("rows") or [])
        n = len(rws)
        if n < 1 or len(new_order) != n or set(new_order) != set(range(n)):
            logger.error("对端重排与本地行数不一致 n=%s order=%s", n, new_order)
            self._refresh_remote_view()
            return
        reordered = [rws[i] for i in new_order]
        remote_chain_cache.mark_pending_apply(
            pid,
            str(c2.get("chain") or cname),
            bool(c2.get("chain_exists", True)),
            reordered,
        )
        if await self._apply_remote_cache_to_peer(pid):
            self._selected_remote_indices.clear()
            self._notify_for_page("规则顺序已更新", type="positive")
        self._refresh_remote_view()

    def _show_edit_remote_line_dialog(self, idx: int, row: dict) -> None:
        """统一表单编辑对端规则；可结构化编辑，也可直接修改原始 rest。"""

        if not self._require_remote_strategy_tab():
            return
        peer_id = self._active_remote_peer_id()
        if not peer_id:
            self._notify_for_page("请选择对端", type="warning")
            return

        edit = _remote_row_to_edit_form_dict(row)
        source_subnet = edit.get("source_subnet") or ""
        source_ips = edit.get("source_ips")
        source_value = source_subnet or (
            ",".join(source_ips) if source_ips else ""
        )
        original_rest = str(row.get("rest") or "").strip()
        chain_name = IptablesManager.peer_chain_name_for_id(peer_id)
        fields: dict[str, Any] = {}

        def body() -> None:
            ui.label(
                f"链 {chain_name}；保存后立即 SSH 写回。"
                " 原始 rest 改动优先于结构化字段。"
            ).classes("section-caption")
            fields["action"] = ui.select(
                {
                    "accept": "放行 (ACCEPT)",
                    "drop": "丢弃 (DROP)",
                    "reject": "拒绝 (REJECT)",
                },
                label="动作",
                value=edit.get("action", "accept"),
            ).classes("w-full")
            fields["protocol"] = ui.select(
                {
                    "all": "不限 (ALL)",
                    "tcp": "TCP",
                    "udp": "UDP",
                    "icmp": "ICMP",
                },
                label="协议",
                value=str(edit.get("protocol") or "all").replace("any", "all"),
                with_input=True,
                new_value_mode="add-unique",
            ).classes("w-full")
            fields["source"] = ui.input(
                "源子网 CIDR 或源 IP 列表",
                value=source_value,
            ).classes("w-full")
            fields["dest_ip"] = ui.input(
                "目标地址",
                value=edit.get("dest_ip") or "",
            ).classes("w-full")
            fields["dest_port"] = ui.input(
                "目标端口",
                value=edit.get("dest_port") or "",
            ).classes("w-full")
            fields["description"] = ui.input(
                "规则描述",
                value=edit.get("description") or "",
            ).classes("w-full")
            fields["enabled"] = ui.switch(
                "启用",
                value=bool(edit.get("enabled", True)),
            )

            ui.separator().classes("q-my-sm")
            ui.label("高级：原始 iptables rest").classes("section-caption")
            fields["raw"] = (
                ui.textarea(
                    "原始 rest",
                    value=original_rest,
                )
                .classes("w-full font-mono")
                .props("rows=4")
            )
            fields["parse_status"] = ui.label("").classes("section-caption")

            def update_parse_status() -> None:
                raw = str(fields["raw"].value or "").strip()
                if not raw:
                    fields["parse_status"].text = "原始规则不能为空"
                    return
                if "\n" in raw or "\r" in raw:
                    fields["parse_status"].text = "单条规则不能包含换行"
                    return
                hints = parse_remote_rule_hints(raw)
                parsed = [
                    f"协议 {hints.get('proto') or 'ALL'}",
                    f"动作 {hints.get('action') or '—'}",
                    f"源 {hints.get('source') or '*'}",
                    f"目标 {hints.get('dest') or '*'}:{hints.get('dport') or '*'}",
                ]
                fields["parse_status"].text = " · ".join(parsed)

            fields["raw"].on_value_change(lambda _event: update_parse_status())
            update_parse_status()

        async def submit() -> bool:
            raw_value = str(fields["raw"].value or "").strip()
            if raw_value != original_rest:
                return await self._save_remote_raw_edit(
                    peer_id,
                    idx,
                    raw_value,
                    bool(fields["enabled"].value),
                )

            block_rows = self._build_remote_structured_block(
                peer_id,
                idx,
                action=str(fields["action"].value or "accept"),
                protocol=str(fields["protocol"].value or "all"),
                source_input=str(fields["source"].value or ""),
                dest_ip=str(fields["dest_ip"].value or "").strip() or None,
                dest_port=str(fields["dest_port"].value or "").strip() or None,
                description=str(fields["description"].value or "").strip() or None,
                enabled=bool(fields["enabled"].value),
            )
            if block_rows is None:
                return False
            return await self._save_remote_block_replace_and_push(
                peer_id,
                idx,
                block_rows,
            )

        form = create_form_dialog(
            f"编辑对端规则 #{idx + 1}",
            body=body,
            on_submit=submit,
            submit_label="保存并写回",
            close_on_success=True,
        )
        form.dialog.props("position=right maximized")
        form.card.classes(
            "firewall-rule-drawer w-[min(100vw,30rem)] h-full no-wrap overflow-y-auto"
        )
        form.open()

    def _build_remote_structured_block(
        self,
        peer_id: str,
        idx: int,
        *,
        action: str,
        protocol: str,
        source_input: str,
        dest_ip,
        dest_port,
        description,
        enabled: bool,
    ) -> list[dict[str, Any]] | None:
        """把结构化表单生成可替换的工作副本规则块。"""

        source_text = str(source_input or "").strip()
        source_subnet, source_ips = _parse_source_text_for_remote_save(
            source_text
        )
        port_text = str(dest_port or "").strip()
        normalized_action = str(action or "accept").lower()
        if normalized_action not in {"accept", "drop", "reject"}:
            normalized_action = "accept"
        normalized_protocol = str(protocol or "all").strip().lower()
        if normalized_protocol in {"", "any"}:
            normalized_protocol = "all"

        try:
            FirewallRule(
                owner_type="group",
                owner_id="_",
                action=normalized_action,  # type: ignore[arg-type]
                priority=1,
                protocol=normalized_protocol,
                dest_ip=str(dest_ip or "").strip() or None,
                dest_port=port_text or None,
            )
        except (ValidationError, ValueError) as exc:
            self._notify_for_page(str(exc), type="negative")
            return None

        try:
            rests = remote_rests_from_create_fields(
                source_subnet=source_subnet,
                source_ips=source_ips,
                action=normalized_action,
                protocol=normalized_protocol,
                dest_ip=str(dest_ip or "").strip() or None,
                dest_port=port_text or None,
            )
        except (ValueError, ValidationError) as exc:
            logger.error("对端编辑生成 rest 失败: %s", exc)
            self._notify_for_page(str(exc), type="negative")
            return None

        cache = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        rows = list(cache.get("rows") or [])
        priority = (
            int(rows[idx].get("priority", 0) or 0) or ((idx + 1) * 10)
            if 0 <= idx < len(rows)
            else ((idx + 1) * 10)
        )
        desc = str(description or "").strip()
        return [
            {
                "rest": rest,
                "enabled": bool(enabled),
                "priority": priority,
                **({"description": desc} if desc else {}),
            }
            for rest in rests
        ]

    async def _save_remote_raw_edit(
        self,
        peer_id: str,
        idx: int,
        raw_rest: str,
        enabled: bool,
    ) -> bool:
        """保存原始 rest，并经统一工作副本事务立即写回。"""

        body = str(raw_rest or "").strip()
        if not body:
            self._notify_for_page("原始规则不能为空", type="negative")
            return False
        if "\n" in body or "\r" in body:
            self._notify_for_page("单条规则不能含换行", type="negative")
            return False
        if self._active_remote_peer_id() != peer_id:
            self._notify_for_page("对端已切换，已取消写回", type="warning")
            return False

        cache = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        rows = list(cache.get("rows") or [])
        if not (0 <= idx < len(rows)):
            self._notify_for_page("本地数据已变化，请重试", type="warning")
            return False

        rows[idx] = {
            **rows[idx],
            "rest": body,
            "enabled": bool(enabled),
        }
        return await self._save_remote_rows_and_push(peer_id, rows)

    async def _save_remote_block_replace_and_push(
        self,
        peer_id: str,
        at_idx: int,
        new_block: list[dict[str, Any]],
    ) -> bool:
        """用一段规则替换当前行，并经统一工作副本事务写回。"""

        if not self._require_remote_strategy_tab():
            return False
        if self._active_remote_peer_id() != peer_id:
            self._notify_for_page("对端已切换，已取消写回", type="warning")
            return False

        cache = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        rows = list(cache.get("rows") or [])
        if not (0 <= at_idx < len(rows)):
            self._notify_for_page("本地数据已变化，请重试", type="warning")
            return False
        if not new_block:
            self._notify_for_page("没有可保存的规则", type="negative")
            return False

        rows[at_idx : at_idx + 1] = [
            dict(item)
            for item in new_block
        ]
        return await self._save_remote_rows_and_push(peer_id, rows)

    async def _save_remote_rows_and_push(
        self,
        peer_id: str,
        rows: list[dict[str, Any]],
    ) -> bool:
        """统一对端工作副本写事务：prepush 备份 → pending → SSH → 成功/回滚。"""

        cache = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        chain = str(
            cache.get("chain")
            or IptablesManager.peer_chain_name_for_id(peer_id)
        )
        chain_exists = bool(cache.get("chain_exists", True))

        try:
            remote_chain_cache.mark_pending_apply(
                peer_id,
                chain,
                chain_exists,
                rows,
            )
        except Exception as exc:
            logger.error("对端工作副本预写失败 peer=%s: %s", peer_id, exc)
            self._notify_for_page(f"本地工作副本保存失败: {exc}", type="negative")
            return False

        ok = await self._apply_remote_cache_to_peer(peer_id)
        if ok:
            self._notify_for_page("已写回对端", type="positive")
        self._refresh_remote_view()
        return ok

    def _do_edit_remote_line_form(
        self,
        dialog,
        peer_id: str,
        idx: int,
        *,
        action: str,
        protocol: str,
        source_input: str,
        dest_ip,
        dest_port,
        description,
        enabled: bool,
    ) -> None:
        """兼容旧同步调用入口。"""

        block_rows = self._build_remote_structured_block(
            peer_id,
            idx,
            action=action,
            protocol=protocol,
            source_input=source_input,
            dest_ip=dest_ip,
            dest_port=dest_port,
            description=description,
            enabled=enabled,
        )
        if block_rows is None:
            return
        self._schedule_remote_coro(
            self._do_edit_remote_block_replace_and_push(
                dialog,
                peer_id,
                idx,
                block_rows,
            )
        )

    async def _do_edit_remote_line_raw_apply(
        self,
        dialog,
        peer_id: str,
        idx: int,
        body: str,
        enabled: bool,
    ) -> bool:
        """兼容旧异步调用入口。"""

        ok = await self._save_remote_raw_edit(
            peer_id,
            idx,
            body,
            enabled,
        )
        if ok and dialog is not None:
            dialog.close()
        return ok

    async def _do_edit_remote_block_replace_and_push(
        self,
        dialog,
        peer_id: str,
        at_idx: int,
        new_block: list[dict],
    ) -> bool:
        """兼容旧异步调用入口。"""

        ok = await self._save_remote_block_replace_and_push(
            peer_id,
            at_idx,
            new_block,
        )
        if ok and dialog is not None:
            dialog.close()
        return ok

    def _confirm_delete_remote_line(self, idx: int) -> None:
        confirm_dialog.show(
            f"确定删除对端规则 #{idx + 1}？",
            impact=(
                "删除后会立即通过 SSH 重写当前对端链；"
                "若写回失败，工作副本会从 prepush 备份恢复并标记待同步。"
            ),
            object_count=1,
            danger_level="danger",
            on_confirm=lambda: self._schedule_remote_coro(
                self._do_delete_remote_line_async(idx)
            ),
        )

    async def _do_delete_remote_line_async(self, idx: int) -> None:
        if not self._require_remote_strategy_tab():
            return
        pid = self._active_remote_peer_id()
        if not pid:
            return
        c2 = remote_chain_cache.read_remote_chain_cache(pid) or {}
        cname = IptablesManager.peer_chain_name_for_id(pid)
        rws = list(c2.get("rows") or [])
        if not (0 <= idx < len(rws)):
            self._notify_for_page("本地数据已变化，请重试", type="warning")
            return
        rws.pop(idx)
        remote_chain_cache.mark_pending_apply(
            pid,
            str(c2.get("chain") or cname),
            bool(c2.get("chain_exists", True)),
            rws,
        )
        if await self._apply_remote_cache_to_peer(pid):
            self._reindex_selected_after_remote_delete(idx)
            self._notify_for_page("已写回对端", type="positive")
        self._refresh_remote_view()

    def _reindex_selected_after_remote_delete(self, deleted_idx: int) -> None:
        next_set: set[int] = set()
        for j in self._selected_remote_indices:
            if j < deleted_idx:
                next_set.add(j)
            elif j > deleted_idx:
                next_set.add(j - 1)
        self._selected_remote_indices = next_set

    def _show_create_remote_line_dialog(self) -> None:
        """使用共享规则字段新建对端规则并立即 SSH 写回。"""

        if not self._require_remote_strategy_tab():
            return
        peer_id = self._active_remote_peer_id()
        if not peer_id:
            self._notify_for_page("请先选择对端", type="warning")
            return
        peer = self._selected_remote_peer_row()
        if not peer:
            self._notify_for_page("对端不存在", type="negative")
            return
        try:
            if not peer_row_has_usable_ssh_auth(peer):
                self._notify_for_page(
                    "请先配置对端 SSH 凭据",
                    type="warning",
                )
                return
        except Exception as exc:
            logger.error("检查对端 SSH 凭据失败: %s", exc)
            self._notify_for_page("无法确认 SSH 凭据", type="negative")
            return

        groups = GroupService().list_all()
        users = UserService().list_all()
        default_group_id = (
            str(groups[0].get("id") or "")
            if groups
            else None
        )
        holder: dict[str, Any] = {}

        def load_peer_presets(group_id: str):
            if not group_id:
                return []
            return (
                self._peer_service
                .list_peer_lan_firewall_presets_for_center_form()
            )

        def body() -> None:
            ui.label(
                f"对端：{peer.get('name') or peer_id} · "
                f"链 {IptablesManager.peer_chain_name_for_id(peer_id)}；"
                "创建成功后立即写回。"
            ).classes("section-caption")
            holder["fields"] = render_firewall_rule_create_fields(
                groups=groups,
                users=users,
                peer_presets_loader=load_peer_presets,
                ccd_reader=_read_ccd_vpn_ips,
                parse_user_source=_parse_user_source_for_create,
                default_group_id=default_group_id,
                owner_type_label="来源类型",
                peer_lan_label="对端内网 CIDR（可选）",
                source_placeholder="可自动填组子网 / CCD，或手工填写",
                user_hint_text="用户来源可从 CCD 预填虚拟 IP。",
                dest_ip_placeholder="空=不限",
                dest_port_placeholder="空=不限",
            )

        async def submit() -> bool:
            fields = holder["fields"]
            try:
                values = fields.read_values()
                priority = self._resolve_priority(
                    values["priority_mode"],
                    values["priority_value"],
                    base=self._remote_priority_base_list(peer_id),
                )
                rests = remote_rests_from_create_fields(
                    source_subnet=values["source_subnet"],
                    source_ips=values["source_ips"],
                    action=values["action"],
                    protocol=values["protocol"],
                    dest_ip=values["dest_ip"],
                    dest_port=values["dest_port"],
                )
            except (ValueError, ValidationError, TypeError) as exc:
                self._notify_for_page(str(exc), type="negative")
                return False

            description = str(
                values.get("description") or ""
            ).strip() or None
            new_rows = [
                {
                    "rest": line,
                    "enabled": bool(values["enabled"]),
                    "priority": priority,
                    **(
                        {"description": description}
                        if description
                        else {}
                    ),
                }
                for line in rests
            ]
            return await self._insert_remote_rows_and_push(
                peer_id,
                new_rows,
                priority,
            )

        form = create_form_dialog(
            "新建对端规则",
            body=body,
            on_submit=submit,
            submit_label="创建并写回",
            close_on_success=True,
        )
        form.dialog.props("position=right maximized")
        form.card.classes(
            "firewall-rule-drawer w-[min(100vw,30rem)] h-full no-wrap overflow-y-auto"
        )
        form.open()
