# -*- coding: utf-8 -*-
"""Firewall 中心规则表格、筛选与优先级排序行为 mixin。

仅承载中心 AG Grid 的展示/选择/筛选/拖动排序逻辑。页面状态、业务服务、
创建/编辑/导入与跨页签调度仍由 FirewallPage 管理。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from nicegui import background_tasks, run, ui

from app.services.firewall.rule_service import CE_PEER_RULE_PREFIX
from app.services.group.crud import GroupService
from app.ui.adapters.firewall import adapt_center_rule_rows
from app.ui.components.actions import danger_action, secondary_action
from app.ui.components.data_grid import render_data_grid
from app.ui.components.states import (
    empty_state,
    error_state,
    loading_state,
    no_match_state,
)
from app.ui.firewall_helpers import center_grid_columns, move_order
from app.ui.grid_query import first_sort_spec, set_filter_values

logger = logging.getLogger(__name__)


class FirewallCenterGridMixin:
    """FirewallPage 的中心规则表格与排序实现。"""
    @staticmethod
    def _center_grid_columns(
        *, can_reorder: bool = False, reorder_mode: bool = False
    ) -> list[dict[str, Any]]:
        return center_grid_columns(
            can_reorder=can_reorder,
            reorder_mode=reorder_mode,
        )

    def _load_center_grid_rows(self) -> list[dict[str, Any]]:
        """从 service 获取统一中心规则并转换为稳定 Grid 行。"""

        rules = self.rule_service.list_unified_flat()
        group_names = {
            str(group.get("id") or ""): str(group.get("name") or "")
            for group in GroupService().list_all()
            if str(group.get("id") or "").strip()
        }
        peer_names = {
            str(peer.get("id") or ""): str(peer.get("name") or "")
            for peer in self._peer_service.list_all()
            if str(peer.get("id") or "").strip()
        }
        rows = adapt_center_rule_rows(
            rules,
            group_names=group_names,
            peer_names=peer_names,
        )
        self._center_raw_by_id = {
            str(row["id"]): dict(row.get("raw") or {})
            for row in rows
        }
        return rows

    async def _load_center_grid_rows_async(
        self,
    ) -> tuple[list[dict[str, Any]], int]:
        """从 SQLite 分页读取中心规则，并构建完整的列筛选 facets。"""

        groups_result, peers_result, facets_result = await asyncio.gather(
            run.io_bound(GroupService().list_all),
            run.io_bound(self._peer_service.list_all),
            run.io_bound(self.rule_service.list_unified_facets),
            return_exceptions=True,
        )

        group_names: dict[str, str] = {}
        if isinstance(groups_result, BaseException):
            logger.error("加载中心规则用户组名称失败: %s", groups_result)
        else:
            group_names = {
                str(group.get("id") or ""): str(group.get("name") or "")
                for group in groups_result
                if str(group.get("id") or "").strip()
            }

        peer_names: dict[str, str] = {}
        if isinstance(peers_result, BaseException):
            logger.error("加载中心规则对端名称失败: %s", peers_result)
        else:
            peer_names = {
                str(peer.get("id") or ""): str(peer.get("name") or "")
                for peer in peers_result
                if str(peer.get("id") or "").strip()
            }

        if isinstance(facets_result, BaseException):
            raise facets_result
        facets = facets_result

        owner_ids_by_label: dict[str, list[str]] = {}
        owner_label_by_id: dict[str, str] = {}
        for owner in facets.get("owners", []):
            if not isinstance(owner, dict):
                continue
            owner_type = str(owner.get("owner_type") or "")
            owner_id = str(owner.get("owner_id") or "")
            if not owner_id:
                continue
            if owner_type == "group":
                label = group_names.get(owner_id) or owner_id
            elif owner_type == "peer":
                peer_name = peer_names.get(owner_id) or ""
                label = (
                    f"{peer_name} ({owner_id[:8]})"
                    if peer_name
                    else owner_id
                )
            else:
                label = owner_id
            owner_ids_by_label.setdefault(label, []).append(owner_id)
            owner_label_by_id[owner_id] = label

        self._center_owner_ids_by_label = owner_ids_by_label
        self._center_owner_label_by_id = owner_label_by_id

        kind_label_to_key = {
            "中心节点": "center",
            "对端仅标识": "peer_marker",
            "对端·中心放行": "center_peer",
        }
        kind_key_to_label = {value: key for key, value in kind_label_to_key.items()}
        self._center_filter_values = {
            "status_label": ["启用", "停用"],
            "owner_label": sorted(owner_ids_by_label, key=str.casefold),
            "kind_label": [
                kind_key_to_label[key]
                for key in ("center", "peer_marker", "center_peer")
                if key in set(facets.get("kind_keys") or [])
            ],
            "protocol": [
                str(value).upper()
                for value in (facets.get("protocols") or [])
            ],
            "action_label": [
                str(value).upper()
                for value in (facets.get("actions") or [])
            ],
        }

        pending_owner_id = str(self._pending_center_owner_id or "").strip()
        if pending_owner_id:
            label = owner_label_by_id.get(pending_owner_id)
            self._center_filter_model["owner_label"] = {
                "filterType": "niceVpnSet",
                "values": [label or "__no_owner_match__"],
            }
            self._pending_center_owner_id = ""

        filter_model = dict(self._center_filter_model)

        owner_ids: list[str] | None = None
        if "owner_label" in filter_model:
            owner_ids = []
            for label in set_filter_values(filter_model, "owner_label"):
                owner_ids.extend(owner_ids_by_label.get(label, []))
            if not owner_ids:
                owner_ids = ["__no_owner_match__"]

        enabled_values: list[bool] | None = None
        if "status_label" in filter_model:
            enabled_values = [
                value == "启用"
                for value in set_filter_values(filter_model, "status_label")
                if value in {"启用", "停用"}
            ]
            if not enabled_values:
                owner_ids = ["__no_owner_match__"]

        kind_keys: list[str] | None = None
        if "kind_label" in filter_model:
            kind_keys = [
                kind_label_to_key[value]
                for value in set_filter_values(filter_model, "kind_label")
                if value in kind_label_to_key
            ]
            if not kind_keys:
                owner_ids = ["__no_owner_match__"]

        protocols: list[str] | None = None
        if "protocol" in filter_model:
            protocols = [
                value.lower()
                for value in set_filter_values(filter_model, "protocol")
            ]
            if not protocols:
                owner_ids = ["__no_owner_match__"]

        actions: list[str] | None = None
        if "action_label" in filter_model:
            actions = [
                value.lower()
                for value in set_filter_values(filter_model, "action_label")
            ]
            if not actions:
                owner_ids = ["__no_owner_match__"]

        query = (
            str(self._center_search_input.value or "").strip()
            if self._center_search_input is not None
            else ""
        )
        offset = max(0, (self._center_page - 1) * self._center_page_size)

        rules, total = await run.io_bound(
            lambda: self.rule_service.list_unified_page(
                offset=offset,
                limit=self._center_page_size,
                search=query,
                owner_ids=owner_ids,
                enabled_values=enabled_values,
                protocols=protocols,
                actions=actions,
                kind_keys=kind_keys,
                sort_by=self._center_sort_field,
                sort_desc=self._center_sort_desc,
            )
        )
        pages = max(1, (total + self._center_page_size - 1) // self._center_page_size)
        if self._center_page > pages:
            self._center_page = pages
            rules, total = await run.io_bound(
                lambda: self.rule_service.list_unified_page(
                    offset=(self._center_page - 1) * self._center_page_size,
                    limit=self._center_page_size,
                    search=query,
                    owner_ids=owner_ids,
                    enabled_values=enabled_values,
                    protocols=protocols,
                    actions=actions,
                    kind_keys=kind_keys,
                    sort_by=self._center_sort_field,
                    sort_desc=self._center_sort_desc,
                )
            )

        rows = adapt_center_rule_rows(
            rules,
            group_names=group_names,
            peer_names=peer_names,
        )
        return rows, int(total)

    def _filter_center_rows(
        self,
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """计算全局关键词匹配结果；分类筛选由 AG Grid 多选过滤器处理。"""

        query = (
            str(self._center_search_input.value or "").strip().casefold()
            if self._center_search_input is not None
            else ""
        )
        owner_filter = self._center_filter_model.get("owner_label")
        self._is_unified_list = not bool(owner_filter)
        if not owner_filter:
            self.current_owner_id = ""
        elif isinstance(owner_filter, dict):
            selected_labels = owner_filter.get("values") or []
            if len(selected_labels) == 1:
                label = str(selected_labels[0])
                owner_ids = {
                    str(row.get("owner_id") or "")
                    for row in self._center_all_rows
                    if str(row.get("owner_label") or "") == label
                    and str(row.get("owner_id") or "")
                }
                self.current_owner_id = next(iter(owner_ids)) if len(owner_ids) == 1 else ""
            else:
                self.current_owner_id = ""
            self._is_unified_list = False

        if not query:
            return list(rows)
        return [
            row
            for row in rows
            if query in str(row.get("search_text") or "")
        ]

    def _refresh_rules(self) -> None:
        """异步刷新当前中心规则 SQL 页。"""

        if self._center_grid_container is None or not self._center_tab_active:
            return

        self._center_refresh_generation += 1
        generation = self._center_refresh_generation
        self._center_refresh_running = True
        self._center_grid_view = None
        self._center_grid_container.clear()
        with self._center_grid_container:
            loading_state(
                "正在刷新中心规则…" if self._center_all_rows else "正在加载中心规则…",
                refreshing=bool(self._center_all_rows),
            )
        ui.timer(
            0.01,
            lambda: self._refresh_rules_async(generation),
            once=True,
        )

    async def _refresh_rules_async(self, generation: int) -> None:
        """后台加载当前 SQL 页；丢弃已过期的加载结果。"""

        try:
            rows, total = await self._load_center_grid_rows_async()
        except Exception as exc:
            if generation != self._center_refresh_generation:
                return
            self._center_refresh_running = False
            logger.exception("加载中心防火墙规则失败")
            self._center_all_rows = []
            self._center_display_rows = []
            self._center_total_rows = 0
            self._center_grid_view = None
            if self._center_grid_container is not None:
                self._center_grid_container.clear()
                with self._center_grid_container:
                    error_state(
                        str(exc),
                        title="中心规则加载失败",
                        on_retry=self._refresh_rules,
                    )
            return

        if (
            generation != self._center_refresh_generation
            or not self._center_tab_active
        ):
            return

        self._center_refresh_running = False
        self._center_all_rows = rows
        self._center_total_rows = int(total)
        self._center_raw_by_id = {
            str(row["id"]): dict(row.get("raw") or {})
            for row in rows
        }
        self._render_center_rules()

    def _center_columns_for_query(self) -> list[dict[str, Any]]:
        columns = self._center_grid_columns(
            can_reorder=self._center_reorder_allowed,
            reorder_mode=self._center_reorder_mode,
        )
        for column in columns:
            field = str(column.get("field") or "")
            if isinstance(column.get("filterParams"), dict):
                column["filterParams"] = dict(column["filterParams"])
                values = self._center_filter_values.get(field)
                if values is not None:
                    column["filterParams"]["values"] = list(values)
            if field == "description" and not self._center_reorder_mode:
                column["sortable"] = False
            if not self._center_reorder_mode and field == self._center_sort_field:
                column["sort"] = "desc" if self._center_sort_desc else "asc"
        return columns

    def _render_center_rules(self) -> None:
        """渲染当前 SQL 页；排序模式例外地渲染完整规则集。"""

        if self._center_grid_container is None or not self._center_tab_active:
            return
        if self._center_refresh_running:
            return

        rows = list(self._center_all_rows)
        pending_owner_id = self._pending_center_owner_id
        if pending_owner_id:
            label = self._center_owner_label_by_id.get(pending_owner_id)
            if label:
                self._center_filter_model["owner_label"] = {
                    "filterType": "niceVpnSet",
                    "values": [label],
                }
                self._center_page = 1
            else:
                self._center_filter_model.pop("owner_label", None)
            self._pending_center_owner_id = ""

        query = (
            str(self._center_search_input.value or "").strip()
            if self._center_search_input is not None
            else ""
        )

        if self._center_reorder_mode:
            rows_by_id = {
                str(row.get("id") or ""): row
                for row in rows
                if str(row.get("id") or "")
            }
            pending_ids = [
                rule_id
                for rule_id in self._pending_center_order
                if rule_id in rows_by_id
            ]
            seen_ids = set(pending_ids)
            pending_ids.extend(
                str(row.get("id") or "")
                for row in rows
                if str(row.get("id") or "")
                and str(row.get("id") or "") not in seen_ids
            )
            self._pending_center_order = pending_ids
            rows = [rows_by_id[rule_id] for rule_id in pending_ids]

        self._center_display_rows = rows
        self._center_reorder_allowed = bool(
            self._center_reorder_mode
            and not query
            and not self._center_filter_model
            and not self._center_grid_sort_active
        )
        self._sync_center_reorder_controls()

        for row in rows:
            row["actions"] = "操作"

        visible_ids = [
            str(row.get("id") or "")
            for row in rows
            if str(row.get("id") or "")
        ]
        self._visible_center_rule_ids = visible_ids
        self._sync_action_bar_count()

        self._center_grid_container.clear()
        self._center_grid_view = None
        with self._center_grid_container:
            if self._center_total_rows <= 0 and not self._center_reorder_mode:
                if query or self._center_filter_model:
                    no_match_state(
                        description="当前搜索或列筛选没有匹配规则。",
                        on_clear=self._clear_center_filters,
                    )
                else:
                    empty_state(
                        "暂无中心规则",
                        description="新建规则或从备份导入后会显示在这里。",
                        action_label="新建规则",
                        on_action=self._show_create_dialog,
                        icon="shield",
                    )
                return
            if not rows:
                no_match_state(
                    description="当前页没有匹配规则。",
                    on_clear=self._clear_center_filters,
                )
                return

            self._center_grid_view = render_data_grid(
                self._center_columns_for_query(),
                rows,
                row_id="id",
                page_size=self._center_page_size,
                pagination=not self._center_reorder_mode,
                selectable=not self._center_reorder_mode,
                selected_ids=self.selected_rule_ids,
                on_cell_click=self._on_center_grid_cell_click,
                row_drag_managed=self._center_reorder_allowed,
                show_summary=False,
                server_total=(
                    None if self._center_reorder_mode else self._center_total_rows
                ),
                server_page=self._center_page,
                on_server_page_change=(
                    None
                    if self._center_reorder_mode
                    else self._on_center_page_change
                ),
            )
            if not self._center_reorder_mode:
                self._center_grid_view.grid.on(
                    "selectionChanged",
                    self._on_center_grid_selection_changed,
                )
            self._center_grid_view.grid.on(
                "filterChanged",
                self._on_center_grid_filter_changed,
            )
            self._center_grid_view.grid.on(
                "sortChanged",
                self._on_center_grid_filter_changed,
            )
            if self._center_filter_model:
                self._schedule_grid_filter_model(
                    self._center_grid_view.grid,
                    self._center_filter_model,
                    "firewall-center-filter-restore",
                )
            if self._center_reorder_allowed:
                self._center_grid_view.grid.on(
                    "rowDragEnd",
                    self._on_center_grid_row_drag_end,
                )

    def _on_center_page_change(self, page: int) -> None:
        target = max(1, int(page))
        pages = max(
            1,
            (self._center_total_rows + self._center_page_size - 1)
            // self._center_page_size,
        )
        target = min(target, pages)
        if target == self._center_page:
            return
        self._center_page = target
        self._refresh_rules()

    def _on_center_search_changed(self, _event=None) -> None:
        if self._center_reorder_mode:
            return
        self._center_page = 1
        self._refresh_rules()

    def _sync_center_reorder_controls(self) -> None:
        """同步排序模式按钮、搜索框和页脚提示。"""

        center_visible = bool(self._center_tab_active)
        reorder_mode = bool(center_visible and self._center_reorder_mode)
        if self._center_reorder_button is not None:
            self._center_reorder_button.set_visibility(
                center_visible and not reorder_mode
            )
        if self._center_save_reorder_button is not None:
            self._center_save_reorder_button.set_visibility(reorder_mode)
        if self._center_cancel_reorder_button is not None:
            self._center_cancel_reorder_button.set_visibility(reorder_mode)
        if self._center_search_input is not None:
            if reorder_mode:
                self._center_search_input.disable()
            else:
                self._center_search_input.enable()

    async def _enter_center_reorder_mode(self) -> None:
        """只有全局、无搜索/筛选/表格排序时才允许进入优先级预览。"""

        if not self._require_center_strategy_tab():
            return
        query = (
            str(self._center_search_input.value or "").strip()
            if self._center_search_input is not None
            else ""
        )
        if query:
            self._notify_for_page(
                "请先清空关键词搜索，再调整优先级",
                type="warning",
            )
            return

        filter_model = dict(self._center_filter_model)
        sort_active = self._center_grid_sort_active
        if self._center_grid_view is not None:
            try:
                model = await self._center_grid_view.grid.run_grid_method(
                    "getFilterModel"
                )
                column_state = await self._center_grid_view.grid.run_grid_method(
                    "getColumnState"
                )
                filter_model = model if isinstance(model, dict) else {}
                sort_field, sort_desc = first_sort_spec(
                    column_state if isinstance(column_state, list) else [],
                    default_field="priority",
                )
                sort_active = not (
                    sort_field == "priority" and sort_desc is False
                )
                self._center_filter_model = filter_model
                self._center_sort_field = sort_field
                self._center_sort_desc = sort_desc
                self._center_grid_sort_active = sort_active
            except Exception as exc:
                logger.debug("读取排序前的 Grid 状态失败: %s", exc)

        if filter_model or sort_active:
            self._notify_for_page(
                "请先清除列筛选和表格排序，再进入优先级调整模式",
                type="warning",
            )
            return

        try:
            full_rows = await run.io_bound(self._load_center_grid_rows)
        except Exception as exc:
            logger.exception("加载完整中心规则排序集失败")
            self._notify_for_page(f"加载完整规则失败: {exc}", type="negative")
            return

        order = [
            str(row.get("id") or "")
            for row in full_rows
            if str(row.get("id") or "")
        ]
        if not order:
            self._notify_for_page("暂无规则可排序", type="warning")
            return

        self._center_all_rows = full_rows
        self._center_total_rows = len(full_rows)
        self._center_raw_by_id = {
            str(row["id"]): dict(row.get("raw") or {})
            for row in full_rows
        }
        self._center_reorder_original_order = list(order)
        self._pending_center_order = list(order)
        self._center_reorder_mode = True
        self._center_reorder_allowed = True
        self.selected_rule_ids.clear()
        self._sync_action_bar_count()
        self._sync_center_reorder_controls()
        self._render_center_rules()

    def _cancel_center_reorder_mode(self) -> None:
        """取消排序预览，恢复数据库中的真实优先级顺序。"""

        if not self._center_reorder_mode:
            return
        self._center_reorder_mode = False
        self._center_reorder_allowed = False
        self._pending_center_order = []
        self._center_reorder_original_order = []
        self._sync_center_reorder_controls()
        self._notify_for_page("已取消优先级调整，未修改真实规则顺序", type="info")
        self._refresh_rules()

    def _save_center_reorder(self) -> None:
        """一次性持久化拖动后的全局顺序；写入失败时保留预览供重试。"""

        if not self._center_reorder_mode:
            return
        order = list(self._pending_center_order)
        actual_ids = {
            str(row.get("id") or "")
            for row in self._center_all_rows
            if str(row.get("id") or "")
        }
        if not order or len(order) != len(actual_ids) or set(order) != actual_ids:
            self._notify_for_page(
                "规则列表已变化，无法安全保存排序；请取消后重新进入排序模式",
                type="negative",
            )
            return

        if order == self._center_reorder_original_order:
            self._cancel_center_reorder_mode()
            return
        try:
            self.rule_service.reorder("", order)
        except Exception as exc:
            logger.exception("保存中心规则优先级失败")
            self._notify_for_page(f"保存排序失败: {exc}", type="negative")
            return

        self._center_reorder_mode = False
        self._center_reorder_allowed = False
        self._pending_center_order = []
        self._center_reorder_original_order = []
        self._sync_center_reorder_controls()
        self._notify_for_page("规则优先级已更新", type="positive")
        self._refresh_rules()

    def _clear_center_filters(self) -> None:
        if self._center_search_input is not None:
            self._center_search_input.set_value("")
        self._center_filter_model = {}
        self._pending_center_owner_id = ""
        self.current_owner_id = ""
        self._is_unified_list = True
        self._center_sort_field = "priority"
        self._center_sort_desc = False
        self._center_grid_sort_active = False
        self._center_page = 1
        self._refresh_rules()

    def _schedule_grid_filter_model(
        self,
        grid: Any,
        model: dict[str, Any],
        task_name: str,
    ) -> None:
        """在 Grid 创建后恢复列筛选模型，搜索框变化时不会丢失多选条件。"""

        if not model:
            return

        async def apply_model() -> None:
            try:
                await grid.run_grid_method("setFilterModel", model)
            except Exception as exc:
                logger.debug("恢复防火墙筛选模型失败: %s", exc)

        try:
            background_tasks.create(apply_model(), name=task_name)
        except RuntimeError:
            logger.debug("当前无后台任务上下文，跳过筛选模型恢复")

    def _sync_center_owner_scope_from_filter_model(self) -> None:
        """仅在归属筛选恰好选中一个唯一归属时保留旧导入/新建默认归属语义。"""

        owner_filter = self._center_filter_model.get("owner_label")
        if not isinstance(owner_filter, dict):
            self.current_owner_id = ""
            self._is_unified_list = True
            return

        values = owner_filter.get("values") or []
        self._is_unified_list = False
        if len(values) != 1:
            self.current_owner_id = ""
            return

        label = str(values[0])
        owner_ids = {
            str(owner_id)
            for owner_id in self._center_owner_ids_by_label.get(label, [])
            if str(owner_id)
        }
        self.current_owner_id = next(iter(owner_ids)) if len(owner_ids) == 1 else ""

    async def _on_center_grid_filter_changed(self, *_args) -> None:
        """把列筛选/排序状态下推到下一次 SQLite 查询。"""

        view = self._center_grid_view
        if view is None or self._center_reorder_mode:
            return
        try:
            model = await view.grid.run_grid_method("getFilterModel")
            column_state = await view.grid.run_grid_method("getColumnState")
        except Exception as exc:
            logger.debug("同步中心规则筛选状态失败: %s", exc)
            return

        normalized_model = model if isinstance(model, dict) else {}
        sort_field, sort_desc = first_sort_spec(
            column_state if isinstance(column_state, list) else [],
            default_field="priority",
        )
        sort_active = not (sort_field == "priority" and sort_desc is False)

        if (
            normalized_model == self._center_filter_model
            and sort_field == self._center_sort_field
            and sort_desc == self._center_sort_desc
        ):
            return

        self._center_filter_model = normalized_model
        self._center_sort_field = sort_field
        self._center_sort_desc = sort_desc
        self._center_grid_sort_active = sort_active
        self._center_page = 1
        self._sync_center_owner_scope_from_filter_model()
        self._refresh_rules()

    async def _on_center_grid_selection_changed(self, *_args) -> None:
        """把当前页选择合并到跨页批量操作集合。"""

        if self._center_grid_view is None:
            self._sync_action_bar_count()
            return
        selected = await self._center_grid_view.get_selected_rows()
        visible_ids = {
            str(row.get("id") or "").strip()
            for row in self._center_all_rows
            if str(row.get("id") or "").strip()
        }
        selected_on_page = {
            str(row.get("id") or "").strip()
            for row in selected
            if str(row.get("id") or "").strip()
        }
        self.selected_rule_ids.difference_update(visible_ids)
        self.selected_rule_ids.update(selected_on_page)
        self._sync_action_bar_count()

    async def _on_center_grid_cell_click(self, event) -> None:
        """复制结构化字段；点击“操作”列打开单项操作。"""

        args = getattr(event, "args", None) or {}
        if not isinstance(args, dict):
            return
        data = args.get("data") or {}
        if not isinstance(data, dict):
            return
        col_id = str(args.get("colId") or "")
        rule_id = str(data.get("id") or "").strip()

        if col_id == "actions" and rule_id:
            self._show_center_rule_actions(rule_id)
            return

        value = ""
        if col_id == "id":
            value = rule_id
        elif col_id == "owner_label":
            value = str(data.get("owner_id") or data.get("owner_label") or "")
        elif col_id in {"source", "dest", "dest_port"}:
            value = str(data.get(col_id) or "")
        if value and value not in {"—", "*"}:
            await self._copy_text_to_clipboard(value)

    def _show_center_rule_actions(self, rule_id: str) -> None:
        """打开单条中心规则操作面板。"""

        raw = self._center_raw_by_id.get(str(rule_id))
        if not raw:
            self._notify_for_page("规则已变化，请刷新后重试", type="warning")
            return
        enabled = bool(raw.get("enabled", True))
        is_center_peer = (
            str(rule_id).startswith(CE_PEER_RULE_PREFIX)
            or raw.get("_row_kind") == "center_peer"
        )

        with ui.dialog() as dialog, ui.card().classes(
            "vpn-form-dialog firewall-action-dialog w-full max-w-md"
        ):
            ui.label("规则操作").classes("text-h6")
            ui.label(str(rule_id)).classes("font-mono text-caption break-all")
            if is_center_peer:
                ui.label(
                    "这是对端在中心 VPN_FORWARD 的虚拟放行行；删除会清空该对端 LAN。"
                ).classes("section-caption")

            with ui.column().classes("w-full gap-2 q-mt-sm"):
                secondary_action(
                    "编辑",
                    icon="edit",
                    on_click=lambda: (
                        dialog.close(),
                        self._show_edit_dialog(dict(raw)),
                    ),
                )
                secondary_action(
                    "停用" if enabled else "启用",
                    icon="pause_circle" if enabled else "play_circle",
                    on_click=lambda: self._toggle_center_rule_enabled(
                        str(rule_id),
                        not enabled,
                        dialog=dialog,
                    ),
                )
                secondary_action(
                    "复制规则 JSON",
                    icon="content_copy",
                    on_click=lambda: self._schedule_center_rule_copy(dict(raw)),
                )
                danger_action(
                    "删除",
                    icon="delete",
                    on_click=lambda: (
                        dialog.close(),
                        self._confirm_delete(str(rule_id)),
                    ),
                )
            secondary_action("关闭", on_click=dialog.close)

        dialog.open()

    def _schedule_center_rule_copy(self, raw: dict[str, Any]) -> None:
        """复制单条规则 JSON；从同步按钮安全调度异步剪贴板。"""

        try:
            background_tasks.create(
                self._copy_text_to_clipboard(
                    json.dumps(raw, ensure_ascii=False, indent=2)
                ),
                name="firewall-copy-rule",
            )
        except RuntimeError as exc:
            logger.error("调度规则复制失败: %s", exc)
            self._notify_for_page("复制任务未能启动", type="negative")

    def _toggle_center_rule_enabled(
        self,
        rule_id: str,
        enabled: bool,
        *,
        dialog=None,
    ) -> bool:
        """单条规则启停并刷新。"""

        try:
            self.rule_service.set_enabled(rule_id, enabled)
        except Exception as exc:
            logger.error("切换中心规则状态失败 rule=%s: %s", rule_id, exc)
            self._notify_for_page(str(exc), type="negative")
            return False
        if dialog is not None:
            dialog.close()
        self._notify_for_page("已启用" if enabled else "已停用", type="positive")
        self._refresh_rules()
        return True

    def _on_center_grid_row_drag_end(self, event) -> None:
        """拖动只更新本地预览；点击“保存排序”后才写入真实优先级。"""

        if (
            not self._center_reorder_mode
            or not self._center_reorder_allowed
            or self._center_filter_model
            or self._center_grid_sort_active
        ):
            self._notify_for_page(
                "请进入“调整优先级”模式，并确保没有搜索、筛选或表格排序",
                type="warning",
            )
            self._render_center_rules()
            return

        args = getattr(event, "args", None) or {}
        if not isinstance(args, dict):
            self._render_center_rules()
            return
        data = args.get("data")
        if not isinstance(data, dict):
            node = args.get("node")
            data = node.get("data") if isinstance(node, dict) else None
        rule_id = str((data or {}).get("id") or args.get("rowId") or "").strip()
        current_order = list(
            self._pending_center_order or self._visible_center_rule_ids
        )
        if not rule_id or rule_id not in current_order:
            self._render_center_rules()
            return

        old_index = current_order.index(rule_id)
        try:
            target_index = args.get("toIndex")
            if target_index is None:
                target_index = args.get("overIndex", args.get("rowIndex", old_index))
            new_index = int(target_index)
        except (TypeError, ValueError):
            new_index = old_index
        # AG Grid reports -1 when the pointer is below the final row.
        if new_index < 0:
            new_index = len(current_order) - 1
        else:
            new_index = min(new_index, len(current_order) - 1)

        new_order = move_order(current_order, old_index, new_index)
        if not new_order or new_order == current_order:
            return

        self._pending_center_order = new_order
        self._visible_center_rule_ids = list(new_order)
        rows_by_id = {
            str(row.get("id") or ""): row
            for row in self._center_all_rows
            if str(row.get("id") or "")
        }
        self._center_display_rows = [
            rows_by_id[rule_id]
            for rule_id in new_order
            if rule_id in rows_by_id
        ]
        if self._center_grid_view is not None:
            self._center_grid_view.set_rows(self._center_display_rows)
