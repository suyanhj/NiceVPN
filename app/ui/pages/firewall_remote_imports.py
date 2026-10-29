# -*- coding: utf-8 -*-
"""Firewall 对端导入与批量操作 mixin。

远端 SSH 拉取/推送、Grid 与单条规则动作由 FirewallRemoteMixin 管理；
本模块只负责导入表单/执行与远端批量删除、批量启停。
"""

from __future__ import annotations

import logging
from typing import Any

from nicegui import ui

from app.services.firewall.iptables_mgr import IptablesManager
from app.services.peer_instance import remote_chain_cache
from app.ui.components import confirm_dialog
from app.ui.components.form_dialog import create_form_dialog

logger = logging.getLogger(__name__)


class FirewallRemoteImportBatchMixin:
    """FirewallPage 的远端导入与批量实现。"""

    def _prepare_remote_import(
        self,
        text: str,
        *,
        peer_id: str | None = None,
    ) -> dict[str, Any]:
        """预检查远端备份 / iptables 粘贴 / 混合文本，不产生写入。"""

        target = str(peer_id or self._active_remote_peer_id() or "").strip()
        if not target:
            raise ValueError("请先选择对端")
        normalized = str(text or "").strip().lstrip("\ufeff")
        if not normalized:
            raise ValueError("无内容")

        chain = IptablesManager.peer_chain_name_for_id(target)
        rows, parsed_chain, chain_exists = (
            self._rows_from_remote_import_text_mixed(
                normalized,
                target,
                chain,
            )
        )
        if normalized.startswith("{"):
            kind = "backup"
        elif any(
            line.strip().startswith("-A ")
            for line in normalized.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ):
            kind = "iptables"
        else:
            kind = "mixed"

        return {
            "peer_id": target,
            "kind": kind,
            "rows": [dict(row) for row in rows],
            "chain": parsed_chain,
            "chain_exists": bool(chain_exists),
            "count": len(rows),
            "text": normalized,
        }

    async def _execute_remote_import(
        self,
        form,
        status_label,
        prepared: dict[str, Any],
    ) -> bool:
        """写入已预检查的远端工作副本；失败保留表单与输入。"""

        peer_id = str(prepared.get("peer_id") or "").strip()
        if not peer_id or self._active_remote_peer_id() != peer_id:
            self._notify_for_page(
                "对端已切换，已取消导入",
                type="warning",
            )
            return False
        if self._remote_push_running_peer is not None:
            self._notify_for_page(
                "已有对端写回正在执行，请勿重复提交",
                type="warning",
            )
            return False

        rows = [
            dict(row)
            for row in (prepared.get("rows") or [])
        ]
        chain = str(
            prepared.get("chain")
            or IptablesManager.peer_chain_name_for_id(peer_id)
        )
        chain_exists = bool(prepared.get("chain_exists", True))
        total = len(rows)

        self._remote_push_running_peer = peer_id
        form.set_busy(True)
        status_label.text = f"导入中…准备写回 {total} 条规则"
        self._refresh_remote_toolbar_state()
        try:
            remote_chain_cache.mark_pending_apply(
                peer_id,
                chain,
                chain_exists,
                rows,
            )
            ok = await self._apply_remote_cache_to_peer(peer_id)
            if not ok:
                status_label.text = (
                    f"导入失败：成功 0，跳过 0，失败 {max(total, 1)}"
                )
                return False

            self._selected_remote_indices.clear()
            status_label.text = (
                f"导入完成：成功 {total}，跳过 0，失败 0"
            )
            self._notify_for_page(
                status_label.text,
                type="positive",
            )
            form.close()
            return True
        finally:
            if self._remote_push_running_peer == peer_id:
                self._remote_push_running_peer = None
            form.set_busy(False)
            self._refresh_remote_toolbar_state()
            self._refresh_remote_view()

    def _show_remote_import_dialog(self) -> None:
        """统一远端导入表单：上传/粘贴、预检查、确认后一次写回。"""

        peer_id = self._active_remote_peer_id()
        if not peer_id:
            self._notify_for_page("请先选择对端", type="warning")
            return

        refs: dict[str, Any] = {}
        holder: dict[str, Any] = {}

        def body() -> None:
            ui.label(
                "支持对端副本 JSON、iptables -S 输出、"
                "每行 rest 片段与简化规则；预检查不会写入远端。"
            ).classes("section-caption")
            refs["textarea"] = (
                ui.textarea("规则内容")
                .classes("w-full")
                .props("rows=16")
            )
            refs["status"] = ui.label(
                "尚未执行预检查"
            ).classes("section-caption")

            async def on_upload(event) -> None:
                try:
                    raw = await event.file.read()
                    text = raw.decode("utf-8", errors="replace")
                    refs["textarea"].set_value(text)
                    name = str(
                        getattr(event.file, "name", "") or ""
                    ).strip()
                    refs["status"].text = f"已载入 {name}，等待预检查"
                except Exception as exc:
                    logger.exception("远端规则导入读文件失败")
                    refs["status"].text = f"读取失败：{exc}"
                    self._notify_for_page(
                        f"读文件失败: {exc}",
                        type="negative",
                    )

            ui.upload(
                label="从文件加载（.json / .txt）",
                on_upload=on_upload,
                auto_upload=True,
                max_files=1,
            ).props(
                'accept=".json,.txt,.text,application/json,text/plain"'
            ).classes("w-full")

        def submit() -> bool:
            try:
                prepared = self._prepare_remote_import(
                    str(refs["textarea"].value or ""),
                    peer_id=peer_id,
                )
            except ValueError as exc:
                refs["status"].text = f"预检查失败：{exc}"
                self._notify_for_page(str(exc), type="negative")
                return False

            count = int(prepared.get("count") or 0)
            kind_label = {
                "backup": "备份 JSON",
                "iptables": "iptables 输出",
                "mixed": "混合/简化文本",
            }.get(str(prepared.get("kind") or ""), "规则文本")
            refs["status"].text = (
                f"预检查通过：{kind_label}，共 {count} 条"
            )
            confirm_dialog.show(
                f"用预检查结果覆盖当前对端链（{count} 条）？",
                impact=(
                    "会覆盖本地工作副本并立即通过 SSH 重写对端链；"
                    "失败时按 prepush 备份恢复，输入内容保留。"
                ),
                object_count=count,
                danger_level="danger",
                on_confirm=lambda: self._schedule_remote_coro(
                    self._execute_remote_import(
                        holder["form"],
                        refs["status"],
                        prepared,
                    )
                ),
            )
            return False

        form = create_form_dialog(
            "导入对端规则",
            body=body,
            on_submit=submit,
            submit_label="预检查并导入",
            close_on_success=False,
        )
        holder["form"] = form
        form.card.classes("w-full max-w-3xl")
        form.open()

    async def _import_ok_remote_async(self, dialog, text: str) -> None:
        self._import_source_file_mode = None
        self._import_last_uploaded_text = None
        if not self._require_remote_strategy_tab():
            return
        pid = self._active_remote_peer_id()
        if not pid:
            self._notify_for_page("请先选择对端", type="warning")
            return
        cname = IptablesManager.peer_chain_name_for_id(pid)
        try:
            rows, chn, cex = self._rows_from_remote_import_text_mixed(text, pid, cname)
        except ValueError as exc:
            logger.error("对端规则导入解析失败: %s", exc)
            self._notify_for_page(str(exc), type="negative")
            return
        remote_chain_cache.mark_pending_apply(pid, chn, cex, rows)
        if await self._apply_remote_cache_to_peer(pid):
            self._selected_remote_indices.clear()
            dialog.close()
            self._notify_for_page("已导入并写回", type="positive")
        self._refresh_remote_view()

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
