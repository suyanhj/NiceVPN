# -*- coding: utf-8 -*-
"""Firewall 批量操作 mixin。"""

from __future__ import annotations

import logging

from app.services.firewall.iptables_mgr import IptablesManager
from app.services.peer_instance import remote_chain_cache
from app.ui.components import confirm_dialog

logger = logging.getLogger(__name__)


class FirewallBatchMixin:
    """FirewallPage 的中心/远端批量启停与删除流程。"""

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

    def _do_batch_delete_center(self) -> None:
        success = 0
        failed = 0
        for rid in list(self.selected_rule_ids):
            try:
                self.rule_service.delete(rid)
                success += 1
            except Exception as exc:
                failed += 1
                logger.error("批量删除中心规则失败 rule=%s: %s", rid, exc)
        self.selected_rule_ids.clear()
        if failed:
            self._notify_for_page(
                f"批量删除完成：成功 {success}，失败 {failed}",
                type="warning" if success else "negative",
            )
        else:
            self._notify_for_page(f"已删除 {success} 条规则", type="positive")
        self._refresh_rules()

    async def _do_batch_remote_delete_async(self) -> bool:
        if not self._require_remote_strategy_tab():
            return False
        peer_id = self._active_remote_peer_id()
        if not peer_id:
            self._notify_for_page("请先选择对端", type="warning")
            return False
        if self._remote_push_running_peer is not None:
            self._notify_for_page(
                "已有对端写回正在执行，请勿重复提交",
                type="warning",
            )
            return False

        cache = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        rows = list(cache.get("rows") or [])
        selected = sorted(self._selected_remote_indices)
        valid = [index for index in selected if 0 <= index < len(rows)]
        skipped = len(selected) - len(valid)
        if not valid:
            self._notify_for_page("未勾选有效规则", type="warning")
            return False

        for index in sorted(valid, reverse=True):
            rows.pop(index)

        chain = str(
            cache.get("chain")
            or IptablesManager.peer_chain_name_for_id(peer_id)
        )
        self._remote_push_running_peer = peer_id
        self._refresh_remote_toolbar_state()
        try:
            remote_chain_cache.mark_pending_apply(
                peer_id,
                chain,
                bool(cache.get("chain_exists", True)),
                rows,
            )
            ok = await self._apply_remote_cache_to_peer(peer_id)
            if ok:
                self._selected_remote_indices.clear()
                self._notify_for_page(
                    f"批量删除完成：成功 {len(valid)}，"
                    f"跳过 {skipped}，失败 0",
                    type="positive",
                )
                return True

            self._notify_for_page(
                f"批量删除失败：成功 0，跳过 {skipped}，"
                f"失败 {len(valid)}",
                type="negative",
            )
            return False
        finally:
            if self._remote_push_running_peer == peer_id:
                self._remote_push_running_peer = None
            self._refresh_remote_toolbar_state()
            self._refresh_remote_view()

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

    async def _do_batch_remote_set_enabled_async(self, en: bool) -> bool:
        if not self._require_remote_strategy_tab():
            return False
        peer_id = self._active_remote_peer_id()
        if not peer_id:
            return False
        if self._remote_push_running_peer is not None:
            self._notify_for_page(
                "已有对端写回正在执行，请勿重复提交",
                type="warning",
            )
            return False

        cache = remote_chain_cache.read_remote_chain_cache(peer_id) or {}
        rows = list(cache.get("rows") or [])
        selected = sorted(self._selected_remote_indices)
        valid = [index for index in selected if 0 <= index < len(rows)]
        invalid_count = len(selected) - len(valid)
        if not valid:
            self._notify_for_page("未勾选有效规则", type="warning")
            return False

        changed = 0
        already = 0
        for index in valid:
            if bool(rows[index].get("enabled", True)) == bool(en):
                already += 1
                continue
            rows[index]["enabled"] = bool(en)
            changed += 1

        skipped = invalid_count + already
        label = "启用" if en else "停用"
        if changed == 0:
            self._selected_remote_indices.clear()
            self._notify_for_page(
                f"批量{label}完成：成功 0，跳过 {skipped}，失败 0",
                type="positive",
            )
            self._refresh_remote_view()
            return True

        chain = str(
            cache.get("chain")
            or IptablesManager.peer_chain_name_for_id(peer_id)
        )
        self._remote_push_running_peer = peer_id
        self._refresh_remote_toolbar_state()
        try:
            remote_chain_cache.mark_pending_apply(
                peer_id,
                chain,
                bool(cache.get("chain_exists", True)),
                rows,
            )
            ok = await self._apply_remote_cache_to_peer(peer_id)
            if ok:
                self._selected_remote_indices.clear()
                self._notify_for_page(
                    f"批量{label}完成：成功 {changed}，"
                    f"跳过 {skipped}，失败 0",
                    type="positive",
                )
                return True

            self._notify_for_page(
                f"批量{label}失败：成功 0，跳过 {skipped}，"
                f"失败 {changed}",
                type="negative",
            )
            return False
        finally:
            if self._remote_push_running_peer == peer_id:
                self._remote_push_running_peer = None
            self._refresh_remote_toolbar_state()
            self._refresh_remote_view()
