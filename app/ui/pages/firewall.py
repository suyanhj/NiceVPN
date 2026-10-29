# -*- coding: utf-8 -*-
"""防火墙：中心 ``VPN_FORWARD`` 与对端机 ``VPN_PEER_*`` 链管理。

对端远端与数据一致性（须全体遵守）:

- **事实源在对端机**：以 SSH 上读到的用户链为唯一事实；切换策略 Tab 到「对端远端」或更换对端节点时，必须
  先 ``fetch_remote_peer_filter_chain_snapshot`` 再 ``record_from_fetch`` 落库（
  见 :mod:`app.services.peer_instance.remote_chain_cache` 与 :func:`_remote_fetch_chain_via_ssh`）。
- **本机文件是工作副本**：落库后一切列表、编辑、批处理、排序、导入均只以本地 JSON 为准，不得假定内存或旧文件
  与对端已一致而跳过拉取。
- **回写仅经 SSH 下发**：对端上链的增删改序仅通过
  :meth:`app.services.peer_instance.service.PeerService.apply_remote_peer_filter_chain_rests` 等路径
  把当前工作副本推上去，以保障「先拉真值 → 再改库 → 再推对端」。
"""

import json
import logging
from typing import Any

from nicegui import context, ui
from nicegui.functions.notify import ARG_MAP as _NICEGUI_NOTIFY_ARG_MAP

from app.services.firewall.iptables_mgr import IptablesManager
from app.services.firewall.rule_service import FirewallRuleService
from app.services.firewall.simple_rule_import import (
    is_center_backup_json_text,
    parse_center_simplified_lines,
)
from app.services.peer_instance import remote_chain_cache
from app.services.peer_instance.service import PeerService
from app.ui.adapters.firewall import format_remote_cache_timestamp
from app.ui.firewall_helpers import (
    firewall_export_json_filename,
    second_line_comment_text,
)
from app.ui.pages.firewall_center import FirewallCenterGridMixin
from app.ui.pages.firewall_center_actions import FirewallCenterActionsMixin
from app.ui.pages.firewall_center_imports import FirewallCenterImportMixin
from app.ui.pages.firewall_remote_actions import FirewallRemoteActionsMixin
from app.ui.pages.firewall_remote_imports import FirewallRemoteImportBatchMixin
from app.ui.pages.firewall_remote import FirewallRemoteMixin
from app.ui.components import confirm_dialog
from app.ui.components.actions import (
    create_action,
    danger_action,
    secondary_action,
)
from app.ui.components.toolbars import render_bulk_action_bar
from app.ui.copy_clipboard import copy_text_to_clipboard

logger = logging.getLogger(__name__)


def _format_peer_cache_timestamp(raw: str | None) -> str:
    """兼容旧调用入口；缓存时间格式化统一由纯适配层维护。"""

    return format_remote_cache_timestamp(raw)


class FirewallPage(
    FirewallCenterGridMixin,
    FirewallCenterActionsMixin,
    FirewallCenterImportMixin,
    FirewallRemoteActionsMixin,
    FirewallRemoteImportBatchMixin,
    FirewallRemoteMixin,
):
    """中心 JSON 规则、中心侧对端虚拟行、以及对端机 filter 用户链的本地工作副本与 UI。

    对端机链的交互顺序见模块 docstring；本页在「对端远端」下通过 :meth:`_remote_fetch_chain_via_ssh` 与
    :meth:`_apply_remote_cache_to_peer` 落实「先拉后改再推」。"""

    def __init__(self) -> None:
        self.rule_service = FirewallRuleService()
        self._peer_service = PeerService()
        self.current_owner_id: str = ""
        self.rules_container: ui.column | None = None
        self.selected_rule_ids: set = set()
        # 对端链 ``rows`` 下标，与中心 ``selected_rule_ids`` 互斥使用（按页签清理）
        self._selected_remote_indices: set[int] = set()
        self._visible_center_rule_ids: list[str] = []
        self._visible_remote_indices: list[int] = []
        # 与 ``list_unified_flat`` 的全局排序一致：留空 = 全量
        self._is_unified_list: bool = True
        self._center_tab_active: bool = True
        self._strat_tabs: ui.tabs | None = None
        self._remote_peer_select: ui.select | None = None
        # 对端机链可滚动列表区，与中心 ``rules_container`` 同构
        self._remote_list_container: ui.column | None = None
        self._remote_tab_shell: ui.column | None = None
        # 供后台 asyncio Task 发 JS/通知：无当前 slot 栈时不能依赖 context.client
        self._ng_client: Any = None
        # 导入：上传 .json/.txt 时的分支；与当前框内文本一致时保留，手改后清空
        self._import_source_file_mode: str | None = None
        self._import_last_uploaded_text: str | None = None
        self._center_import_running = False
        self._center_import_result: dict[str, Any] | None = None
        self._center_import_timer = None
        self._center_grid_container = None
        self._center_grid_view = None
        self._center_search_controls = None
        self._center_search_input = None
        # 归属筛选移入 AG Grid 列头，避免工具栏与表格重复。
        self._pending_center_owner_id = ""
        self._center_filter_model: dict[str, Any] = {}
        self._center_grid_sort_active = False
        self._center_sort_field = "priority"
        self._center_sort_desc = False
        self._center_page = 1
        self._center_page_size = 50
        self._center_total_rows = 0
        self._center_filter_values: dict[str, list[str]] = {}
        self._center_owner_ids_by_label: dict[str, list[str]] = {}
        self._center_owner_label_by_id: dict[str, str] = {}
        self._center_refresh_running = False
        self._center_refresh_generation = 0
        self._center_all_rows: list[dict[str, Any]] = []
        self._center_display_rows: list[dict[str, Any]] = []
        self._center_raw_by_id: dict[str, dict[str, Any]] = {}
        self._center_reorder_allowed = False
        self._center_reorder_mode = False
        self._pending_center_order: list[str] = []
        self._center_reorder_original_order: list[str] = []
        self._center_reorder_button = None
        self._center_save_reorder_button = None
        self._center_cancel_reorder_button = None
        self._remote_fetch_generation = 0
        self._remote_fetch_running_peer: str | None = None
        self._remote_fetch_error_by_peer: dict[str, str] = {}
        self._remote_push_running_peer: str | None = None
        self._remote_status_container = None
        self._remote_cache_status_container = None
        self._remote_cache_meta_label = None
        self._remote_pull_button = None
        self._remote_push_button = None
        self._remote_grid_view = None
        self._remote_grid_rows: list[dict[str, Any]] = []
        self._remote_filter_model: dict[str, Any] = {}
        self._remote_grid_sort_active = False
        self._remote_reorder_allowed = False
        self._action_bar = None

    def _page_client(self) -> Any:
        """当前页面浏览器会话。优先 render 时缓存的 client，否则从列表区容器解析。"""
        c = self._ng_client
        if c is not None:
            return c
        for cont in (self._remote_list_container, self.rules_container):
            if cont is not None:
                cl = getattr(cont, "client", None)
                if cl is not None:
                    return cl
        return None

    def _notify_for_page(
        self,
        message: Any,
        *,
        type: str | None = None,  # noqa: A001 与 Quasar / nicegui 一致
        position: str = "bottom",
        close_button: bool | str = False,
        color: str | None = None,
        multi_line: bool = False,
        **kwargs: Any,
    ) -> None:
        """在任意协程中向本页发通知；与 :func:`nicegui.ui.notify` 等效，不依赖当前任务的 slot 栈。"""
        c = self._page_client()
        if c is None:
            logger.error("firewall: 无 page client，无法弹通知: %s", message)
            return
        options = {
            _NICEGUI_NOTIFY_ARG_MAP.get(key, key): value
            for key, value in locals().items()
            if key not in ("self", "c", "message", "kwargs") and value is not None
        }
        options["message"] = str(message)
        options.update(kwargs)
        c.outbox.enqueue_message("notify", options, c.id)

    def _require_center_strategy_tab(self) -> bool:
        if not self._center_tab_active:
            self._notify_for_page("请先切到「中心策略」Tab。", type="warning")
            return False
        return True

    def _require_remote_strategy_tab(self) -> bool:
        if self._center_tab_active:
            self._notify_for_page("请先切到「对端远端」Tab。", type="warning")
            return False
        return True

    def render(self) -> None:
        """使用 V2 管理页结构渲染中心与对端策略入口。"""

        try:
            self._ng_client = context.client
        except RuntimeError:
            self._ng_client = None

        def action_icon(
            label: str,
            icon: str,
            callback,
            *,
            danger: bool = False,
        ):
            factory = danger_action if danger else secondary_action
            button = factory(
                label,
                icon=icon,
                on_click=callback,
                icon_only=True,
                tooltip=label,
            )
            button.props("round dense")
            classes = "firewall-bar-icon-action"
            if danger:
                classes += " firewall-bar-icon-action--danger"
            button.classes(add=classes)
            return button

        def bulk_actions() -> None:
            create_button = create_action(
                "新建规则",
                icon="add",
                on_click=self._on_new_rule_click,
                icon_only=True,
                tooltip="新建规则",
            )
            create_button.props("round dense")
            create_button.classes(
                add="firewall-bar-icon-action firewall-bar-icon-action--create"
            )
            action_icon(
                "导入",
                "upload_file",
                self._show_import_dialog,
            )
            action_icon(
                "导出",
                "download",
                self._export_backup,
            )
            ui.element("span").classes("firewall-action-separator")
            self._center_reorder_button = action_icon(
                "调整优先级",
                "swap_vert",
                self._enter_center_reorder_mode,
            )
            self._center_save_reorder_button = action_icon(
                "保存排序",
                "save",
                self._save_center_reorder,
            )
            self._center_cancel_reorder_button = action_icon(
                "取消排序",
                "close",
                self._cancel_center_reorder_mode,
            )
            self._center_save_reorder_button.set_visibility(False)
            self._center_cancel_reorder_button.set_visibility(False)
            ui.element("span").classes("firewall-action-separator")
            action_icon(
                "批量启用",
                "toggle_on",
                self._batch_enable,
            )
            action_icon(
                "批量停用",
                "toggle_off",
                self._batch_disable,
            )
            action_icon(
                "批量删除",
                "delete",
                self._batch_delete,
                danger=True,
            )

        with ui.column().classes(
            "page-shell mgmt-page firewall-page--v2 page-shell--firewall"
        ):
            with ui.element("div").classes("firewall-tab-shell"):
                with ui.tabs(
                    value="center",
                    on_change=self._on_strat_change,
                ).classes("firewall-compact-tabs w-full") as self._strat_tabs:
                    ui.tab("center", label="中心策略")
                    ui.tab("remote", label="对端远端")

            self._action_bar = render_bulk_action_bar(
                0,
                actions=bulk_actions,
            )
            self._action_bar.container.classes(add="firewall-bulk-bar")

            with ui.element("section").classes(
                "firewall-filter-bar"
            ) as self._center_search_controls:
                with ui.row().classes(
                    "firewall-filter-row w-full items-center gap-2 no-wrap"
                ):
                    self._center_search_input = (
                        ui.input(
                            placeholder="搜索 IP / 端口 / 规则 ID",
                            on_change=self._on_center_search_changed,
                        )
                        .props("outlined dense clearable debounce=250")
                        .classes("firewall-search-input flex-1 min-w-0")
                    )

            with ui.tab_panels(
                self._strat_tabs,
                value="center",
                keep_alive=False,
            ).classes(
                "w-full h-full min-h-0 flex-1 "
                "firewall-tabpanels firewall-tabpanels--v2"
            ):
                with ui.tab_panel("center").classes(
                    "h-full min-h-0 flex-1 firewall-center-panel"
                ):
                    with ui.column().classes(
                        "firewall-center-canvas w-full min-h-0 flex-1 gap-0"
                    ):
                        with ui.element("section").classes(
                            "mgmt-panel mgmt-panel-list firewall-center-shell "
                            "w-full min-h-0 flex-1"
                        ):
                            self._center_grid_container = ui.column().classes(
                                "firewall-center-data-region "
                                "w-full min-h-0 flex-1"
                            )
                            self.rules_container = self._center_grid_container

                with ui.tab_panel("remote").classes(
                    "h-full min-h-0 flex-1 firewall-remote-panel"
                ):
                    self._remote_tab_shell = ui.column().classes(
                        "firewall-peer-remote firewall-remote-canvas "
                        "w-full min-h-0 flex-1 gap-0"
                    )
                    with self._remote_tab_shell:
                        self._build_remote_tab_static()

        self._refresh_rules()

    def _sync_action_bar_count(self) -> None:
        """让中心/远端共用操作栏始终反映当前页签选择数量。"""

        if self._action_bar is None:
            return
        count = (
            len(self.selected_rule_ids)
            if self._center_tab_active
            else len(self._selected_remote_indices)
        )
        self._action_bar.set_selected_count(count)

    def _on_strat_change(self, e) -> None:
        v = str(getattr(e, "value", None) or "center")
        if v != "center" and self._center_reorder_mode:
            # Leaving the center tab discards an unsaved preview, never silently saves it.
            self._center_reorder_mode = False
            self._pending_center_order = []
            self._center_reorder_original_order = []
            self._center_reorder_allowed = False
            if self._center_search_input is not None:
                self._center_search_input.enable()
        self._center_tab_active = v == "center"
        logger.debug("firewall 策略页签切换为 %s", v)
        if self._center_search_controls is not None:
            self._center_search_controls.set_visibility(self._center_tab_active)
        self._sync_center_reorder_controls()
        if self._center_tab_active:
            self._remote_fetch_generation += 1
            self._remote_fetch_running_peer = None
            self._selected_remote_indices.clear()
            self._sync_action_bar_count()
            self._refresh_rules()
        else:
            self._center_refresh_generation += 1
            self._center_refresh_running = False
            self.selected_rule_ids.clear()
            self._sync_action_bar_count()
            self._on_enter_remote_tab()

    def _on_new_rule_click(self) -> None:
        if self._center_tab_active:
            self._show_create_dialog()
        else:
            self._show_create_remote_line_dialog()

    def _peer_options_for_select(self) -> dict[str, str]:
        """对端下拉的 ``value->label``，与 `PeerService.list_all` 一致。"""
        opts: dict[str, str] = {}
        for p in self._peer_service.list_all():
            pid = str(p.get("id") or "").strip()
            if not pid:
                continue
            opts[pid] = f"{p.get('name', '')} ({pid[:8]}…)"
        return opts

    def _switch_owner(self, owner_id: str) -> None:
        """兼容旧跳转入口：把归属条件写入 Grid 的多选筛选模型。"""

        normalized = str(owner_id or "").strip()
        self.current_owner_id = normalized
        self._pending_center_owner_id = normalized
        if not normalized:
            self._center_filter_model.pop("owner_label", None)
        self._center_page = 1
        self._refresh_rules()

    async def _copy_text_to_clipboard(self, text: str) -> None:
        """将文本写入浏览器剪贴板并轻提示；供归属等可点击区域使用。"""
        t = (text or "").strip()
        if not t:
            return
        if await copy_text_to_clipboard(t):
            self._notify_for_page("已复制", type="positive")

    @staticmethod
    def _second_line_comment_text(
        data: dict,
        *,
        title_text: str,
        is_center_peer: bool = False,
        is_remote_row: bool = False,
        iptables_comment: str = "",
    ) -> str:
        return second_line_comment_text(
            data,
            title_text=title_text,
            is_center_peer=is_center_peer,
            is_remote_row=is_remote_row,
            iptables_comment=iptables_comment,
        )

    def _show_create_dialog(self) -> None:
        self._show_create_center_dialog()

    def _resolve_priority(self, mode: str, input_value, *, base: list[dict] | None = None) -> int:
        if base is None:
            if self._is_unified_list:
                base = self.rule_service.list_unified_flat()
            else:
                oid = (self.current_owner_id or "").strip()
                base = self.rule_service.list_by_owner(oid) if oid else []
        existing = {r.get("priority", 0) for r in base} if base else set()
        if mode == "insert":
            if input_value is not None and input_value != "":
                return int(input_value)
            if not existing:
                return 10
            m = min(existing)
            cand = max(1, m - 10)
            while cand in existing:
                cand += 1
            return cand
        if not existing:
            return 10
        return max(r.get("priority", 0) for r in base) + 10

    def _export_backup(self) -> None:
        if self._center_tab_active:
            try:
                s = self.rule_service.backup()
                ui.download(
                    s.encode("utf-8"),
                    firewall_export_json_filename("firewall-rules-backup"),
                )
                self._notify_for_page(
                    "已导出（中心 JSON + 对端中心侧策略）",
                    type="positive",
                )
            except Exception as exc:
                self._notify_for_page(str(exc), type="negative")
            return
        if not self._require_remote_strategy_tab():
            return
        pid = self._active_remote_peer_id()
        if not pid:
            self._notify_for_page("请先选择对端", type="warning")
            return
        cache = remote_chain_cache.read_remote_chain_cache(pid)
        cname = IptablesManager.peer_chain_name_for_id(pid)
        if cache is None:
            payload: dict = {
                "version": 1,
                "peer_id": pid,
                "chain": cname,
                "chain_exists": True,
                "rows": [],
            }
        else:
            payload = {k: cache[k] for k in cache if k in cache}
        try:
            s = json.dumps(payload, ensure_ascii=False, indent=2)
        except (TypeError, ValueError) as exc:
            self._notify_for_page(f"序列化失败: {exc}", type="negative")
            return
        short = pid[:8] if len(pid) > 8 else pid
        ui.download(
            s.encode("utf-8"),
            firewall_export_json_filename(f"firewall-remote-peer-{short}"),
        )
        self._notify_for_page("已导出对端副本", type="positive")

    def _show_import_dialog(self) -> None:
        self._import_source_file_mode = None
        self._import_last_uploaded_text = None
        if self._center_tab_active:
            self._show_center_import_dialog()
        else:
            self._show_remote_import_dialog()

    def _do_import(self, dialog, text: str) -> None:
        if not (text or "").strip():
            self._notify_for_page("无内容", type="negative")
            return
        t_raw = (text or "").strip()
        t = t_raw.lstrip("\ufeff")
        fmode = self._import_source_file_mode
        if self._center_tab_active:
            # 上传 .json：整库覆盖；上传 .txt：仅简写追加；与纯粘贴的「{ 开头 / 简写」并存
            if fmode == "json_overwrite":
                if not is_center_backup_json_text(t):
                    self._notify_for_page(
                        "须为导出 backup（含 rules_by_owner）。",
                        type="negative",
                    )
                    return
                confirm_dialog.show(
                    "用 backup 覆盖全部中心规则。确定？",
                    on_confirm=lambda: self._import_ok_center_backup(dialog, t),
                )
                return
            if fmode == "simplified_text":
                if self._is_unified_list or not (self.current_owner_id or "").strip():
                    self._notify_for_page("简写导入请先关键词选中归属。", type="negative")
                    return
                try:
                    specs = parse_center_simplified_lines(t)
                except ValueError as exc:
                    logger.error("中心 .txt 简写解析失败: %s", exc)
                    self._notify_for_page(str(exc), type="negative")
                    return
                n = len(specs)
                confirm_dialog.show(
                    f"向当前归属追加 {n} 条简写。确定？",
                    on_confirm=lambda: self._import_ok_center_simplified(dialog, specs),
                )
                return
            if t.startswith("{"):
                if is_center_backup_json_text(t):
                    confirm_dialog.show(
                        "用 backup 覆盖全部中心规则。确定？",
                        on_confirm=lambda: self._import_ok_center_backup(dialog, t),
                    )
                else:
                    self._notify_for_page(
                        "`{` 开头须为导出 backup（含 rules_by_owner）。",
                        type="negative",
                    )
                return
            if self._is_unified_list or not (self.current_owner_id or "").strip():
                self._notify_for_page("简写导入请先关键词选中归属。", type="negative")
                return
            try:
                specs = parse_center_simplified_lines(t)
            except ValueError as exc:
                logger.error("中心简写解析失败: %s", exc)
                self._notify_for_page(
                    f"{exc} 整库恢复请贴 `{{` 开头的 backup 或上传 .json。",
                    type="negative",
                )
                return
            n = len(specs)
            confirm_dialog.show(
                f"向当前归属追加 {n} 条简写。确定？",
                on_confirm=lambda: self._import_ok_center_simplified(dialog, specs),
            )
            return
        else:
            if not self._require_remote_strategy_tab():
                return
            confirm_dialog.show(
                "覆盖对端本地副本并写回。确定？",
                on_confirm=lambda: self._schedule_remote_coro(self._import_ok_remote_async(dialog, t)),
            )

    def _batch_delete(self) -> None:
        if self._center_tab_active:
            if not self._require_center_strategy_tab():
                return
            if not self.selected_rule_ids:
                self._notify_for_page("未勾选", type="warning")
                return
            count = len(self.selected_rule_ids)
            confirm_dialog.show(
                f"批量删除所选 {count} 条中心规则？",
                impact="删除后会逐条刷新中心策略；若其中某条失败，其余成功项不会自动回滚。",
                object_count=count,
                danger_level="danger",
                on_confirm=self._do_batch_delete_center,
            )
            return
        if not self._require_remote_strategy_tab():
            return
        pid = self._active_remote_peer_id()
        if not pid:
            self._notify_for_page("请先选择对端", type="warning")
            return
        if not self._selected_remote_indices:
            self._notify_for_page("未勾选", type="warning")
            return
        count = len(self._selected_remote_indices)
        confirm_dialog.show(
            f"删除所选 {count} 条对端规则？",
            impact=(
                "会一次性重写当前对端链；失败时整批按 prepush 备份恢复。"
            ),
            object_count=count,
            danger_level="danger",
            on_confirm=lambda: self._schedule_remote_coro(
                self._do_batch_remote_delete_async()
            ),
        )

    def _batch_enable(self) -> None:
        self._batch_set_enabled(True)

    def _batch_disable(self) -> None:
        self._batch_set_enabled(False)

    def _batch_set_enabled(self, en: bool) -> None:
        if self._center_tab_active:
            if not self._require_center_strategy_tab():
                return
            if not self.selected_rule_ids:
                self._notify_for_page("未勾选", type="warning")
                return
            success = 0
            skipped = 0
            failed = 0
            for rid in list(self.selected_rule_ids):
                raw = self._center_raw_by_id.get(str(rid), {})
                if raw and bool(raw.get("enabled", True)) == bool(en):
                    skipped += 1
                    continue
                try:
                    self.rule_service.set_enabled(rid, en)
                    success += 1
                except Exception as exc:
                    failed += 1
                    logger.error(
                        "批量切换中心规则状态失败 rule=%s enabled=%s: %s",
                        rid,
                        en,
                        exc,
                    )
            self.selected_rule_ids.clear()
            label = "启用" if en else "停用"
            message = (
                f"批量{label}完成：成功 {success}，跳过 {skipped}，失败 {failed}"
            )
            self._notify_for_page(
                message,
                type="warning" if failed else "positive",
            )
            self._refresh_rules()
            return
        if not self._require_remote_strategy_tab():
            return
        pid = self._active_remote_peer_id()
        if not pid:
            self._notify_for_page("请先选择对端", type="warning")
            return
        if not self._selected_remote_indices:
            self._notify_for_page("未勾选", type="warning")
            return
        self._schedule_remote_coro(self._do_batch_remote_set_enabled_async(en))
