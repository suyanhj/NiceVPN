# -*- coding: utf-8 -*-
"""Firewall 对端远端同步与 Grid 行为 mixin。

仅承载 `FirewallPage` 的对端链 UI/交互方法；状态仍由主页面初始化，
SSH、iptables 与服务层调用路径保持不变。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from nicegui import background_tasks, run, ui

from app.services.firewall.iptables_mgr import IptablesManager
from app.services.peer_instance import remote_chain_cache
from app.services.peer_instance.peer_ssh_connect import peer_row_has_usable_ssh_auth
from app.services.peer_instance.service import PeerService
from app.ui.adapters.firewall import (
    adapt_remote_cache_status,
    adapt_remote_peer_capability,
    adapt_remote_rule_rows,
    parse_remote_rule_hints,
)
from app.ui.components import confirm_dialog
from app.ui.components.actions import danger_action, primary_action, secondary_action
from app.ui.components.data_grid import render_data_grid
from app.ui.components.states import empty_state, error_state, loading_state
from app.ui.components.status import status_badge
from app.ui.firewall_helpers import (
    move_order,
    remote_grid_columns,
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
