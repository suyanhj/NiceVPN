# -*- coding: utf-8 -*-
"""Firewall 中心规则导入 mixin。

仅承载中心 backup / 简写导入、预检查和后台进度管理；跨 Tab 导入调度
仍由 FirewallPage 负责，远端导入仍由 FirewallRemoteMixin 负责。
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from nicegui import run, ui

from app.services.firewall.simple_rule_import import (
    center_rule_payload_from_simplified,
    is_center_backup_json_text,
    parse_center_simplified_lines,
    resolve_center_owner_type,
    source_fields_for_center,
)
from app.ui.components import confirm_dialog
from app.ui.components.form_dialog import create_form_dialog
from app.ui.firewall_helpers import read_ccd_vpn_ips

logger = logging.getLogger(__name__)


class FirewallCenterImportMixin:
    """FirewallPage 的中心导入实现。"""

    def _on_import_textarea_value_change(self, ta) -> None:
        """与上传内容不一致时，不再按「.json 整库 / .txt 简写」走文件分支。"""
        cur = (ta.value or "").strip()
        ref = (self._import_last_uploaded_text or "").strip()
        if ref and cur == ref:
            return
        self._import_source_file_mode = None
        self._import_last_uploaded_text = None

    def _prepare_center_import(
        self,
        text: str,
        *,
        file_mode: str | None = None,
    ) -> dict[str, Any]:
        """识别并预检查中心导入内容，不产生任何写入。"""

        normalized = str(text or "").strip().lstrip("\ufeff")
        if not normalized:
            raise ValueError("无内容")

        mode = str(file_mode or "").strip() or None
        backup_candidate = mode == "json_overwrite" or (
            mode is None and normalized.startswith("{")
        )

        if backup_candidate:
            if not is_center_backup_json_text(normalized):
                raise ValueError("须为导出 backup（含 rules_by_owner）。")
            try:
                payload = json.loads(normalized)
            except json.JSONDecodeError as exc:
                raise ValueError(f"backup JSON 解析失败: {exc}") from exc
            rules_by_owner = payload.get("rules_by_owner") or {}
            if not isinstance(rules_by_owner, dict):
                raise ValueError("backup 中 rules_by_owner 必须为对象")
            rule_count = sum(
                len(items)
                for items in rules_by_owner.values()
                if isinstance(items, list)
            )
            peer_items = payload.get("peers_center_forward") or []
            peer_count = len(peer_items) if isinstance(peer_items, list) else 0
            return {
                "kind": "backup",
                "text": normalized,
                "count": rule_count + peer_count,
                "rule_count": rule_count,
                "peer_count": peer_count,
            }

        if mode == "json_overwrite":
            raise ValueError("须为导出 backup（含 rules_by_owner）。")

        owner_id = str(self.current_owner_id or "").strip()
        if self._is_unified_list or not owner_id:
            raise ValueError("简写导入请先在归属筛选中选择一个归属。")

        try:
            specs = parse_center_simplified_lines(normalized)
        except ValueError as exc:
            raise ValueError(f"简写解析失败: {exc}") from exc
        if not specs:
            raise ValueError("没有可导入的简写规则")

        payloads = self._build_center_simplified_payloads(specs)
        return {
            "kind": "simplified",
            "text": normalized,
            "count": len(payloads),
            "owner_id": owner_id,
            "payloads": payloads,
        }

    def _build_center_simplified_payloads(self, specs) -> list[dict[str, Any]]:
        """把已解析简写转换为中心规则 payload，失败时给出行号。"""

        from app.core.config import load_config

        owner_id = str(self.current_owner_id or "").strip()
        if not owner_id:
            raise ValueError("简写导入缺少归属")

        try:
            owner_type = resolve_center_owner_type(owner_id)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

        config = load_config()
        instance_keys = list((config or {}).get("instances", {}).keys()) if config else []
        instance = instance_keys[0] if instance_keys else "server"

        payloads: list[dict[str, Any]] = []
        for index, spec in enumerate(specs, start=1):
            try:
                source_subnet, source_ips = source_fields_for_center(
                    spec,
                    owner_type,
                )
            except ValueError as exc:
                raise ValueError(f"第 {index} 行: {exc}") from exc

            if owner_type == "user" and not source_subnet and not source_ips:
                source_ips = read_ccd_vpn_ips([owner_id])
                if not source_ips:
                    raise ValueError(
                        f"第 {index} 行缺 -s 且 CCD 无虚拟 IP。"
                    )

            payload = center_rule_payload_from_simplified(
                spec,
                owner_type=owner_type,
                owner_id=owner_id,
                instance=instance,
                source_subnet=source_subnet,
                source_ips=source_ips,
            )
            if str(payload.get("protocol") or "").lower() in {"", "any", "all"}:
                payload["protocol"] = "all"
            payloads.append(payload)
        return payloads

    async def _execute_center_import(
        self,
        form,
        status_label,
        prepared: dict[str, Any],
    ) -> bool:
        """异步执行已预检查导入；失败保留表单与原始输入。"""

        if self._center_import_running:
            self._notify_for_page("中心规则正在导入，请稍候", type="warning")
            return False

        self._center_import_running = True
        form.set_busy(True)
        status_label.text = "导入中…正在刷新 VPN_FORWARD"
        kind = str(prepared.get("kind") or "")
        total = int(prepared.get("count") or 0)

        try:
            if kind == "backup":
                ok = await run.io_bound(
                    self.rule_service.restore,
                    str(prepared.get("text") or ""),
                )
                if not ok:
                    raise RuntimeError("backup 恢复失败")
                success = total
            elif kind == "simplified":
                payloads = [
                    dict(item)
                    for item in (prepared.get("payloads") or [])
                ]
                result = await run.io_bound(
                    self._run_center_simplified_import,
                    payloads,
                )
                if not result.get("ok"):
                    raise RuntimeError("简写导入失败")
                success = int(result.get("count") or 0)
            else:
                raise RuntimeError("未知导入类型")
        except Exception as exc:
            logger.exception("中心规则导入失败 kind=%s", kind)
            status_label.text = f"导入失败：{exc}"
            self._notify_for_page(
                f"导入失败：成功 0，跳过 0，失败 {max(total, 1)}；{exc}",
                type="negative",
                multi_line=True,
            )
            return False
        finally:
            self._center_import_running = False
            form.set_busy(False)

        status_label.text = (
            f"导入完成：成功 {success}，跳过 0，失败 0"
        )
        self._notify_for_page(
            f"导入完成：成功 {success}，跳过 0，失败 0",
            type="positive",
        )
        form.close()
        self._refresh_rules()
        return True

    def _show_center_import_dialog(self) -> None:
        """统一中心规则导入表单：上传/粘贴、自动识别、预检查和异步执行。"""

        state: dict[str, Any] = {
            "file_mode": None,
            "uploaded_text": None,
        }
        refs: dict[str, Any] = {}
        holder: dict[str, Any] = {}

        def body() -> None:
            ui.label(
                ".json = 整库 backup；.txt / 粘贴文本 = 简写追加到当前归属。"
                " 纯粘贴以 { 开头时自动识别为 backup。"
            ).classes("section-caption")
            refs["textarea"] = (
                ui.textarea("规则内容")
                .classes("w-full")
                .props("rows=16")
            )
            refs["status"] = ui.label("尚未执行预检查").classes(
                "section-caption"
            )

            def on_text_change() -> None:
                current = str(refs["textarea"].value or "").strip()
                uploaded = str(state.get("uploaded_text") or "").strip()
                if uploaded and current == uploaded:
                    return
                state["file_mode"] = None
                state["uploaded_text"] = None
                refs["status"].text = "内容已修改，将按粘贴内容自动识别格式"

            refs["textarea"].on_value_change(lambda _e: on_text_change())

            async def on_upload(event) -> None:
                try:
                    raw = await event.file.read()
                    text = raw.decode("utf-8", errors="replace")
                    name = str(getattr(event.file, "name", "") or "").strip()
                    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                    if ext == "json":
                        mode = "json_overwrite"
                    elif ext in {"txt", "text"}:
                        mode = "simplified_text"
                    else:
                        mode = None
                    state["file_mode"] = mode
                    state["uploaded_text"] = str(text or "").strip()
                    refs["textarea"].set_value(text)
                    refs["status"].text = (
                        f"已载入 {name}；"
                        + (
                            "识别为 backup 覆盖"
                            if mode == "json_overwrite"
                            else (
                                "识别为简写追加"
                                if mode == "simplified_text"
                                else "将自动识别格式"
                            )
                        )
                    )
                except Exception as exc:
                    filename = str(
                        getattr(event.file, "name", "") or ""
                    ).strip()
                    logger.exception("中心防火墙导入读文件失败: %s", filename)
                    refs["status"].text = f"读取失败：{exc}"
                    self._notify_for_page(
                        f"读文件失败 ({filename}): {exc}",
                        type="negative",
                    )
                    raise RuntimeError(f"读文件失败: {exc}") from exc

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
                prepared = self._prepare_center_import(
                    str(refs["textarea"].value or ""),
                    file_mode=state.get("file_mode"),
                )
            except ValueError as exc:
                refs["status"].text = f"预检查失败：{exc}"
                self._notify_for_page(str(exc), type="negative")
                return False

            count = int(prepared.get("count") or 0)
            if prepared["kind"] == "backup":
                refs["status"].text = (
                    f"预检查通过：backup 共 {count} 项，等待覆盖确认"
                )
                confirm_dialog.show(
                    "用 backup 覆盖全部中心规则？",
                    impact=(
                        f"将恢复 {prepared.get('rule_count', 0)} 条 JSON 规则和 "
                        f"{prepared.get('peer_count', 0)} 条对端中心策略；"
                        "服务层会在写入前生成回滚备份。"
                    ),
                    object_count=count,
                    danger_level="danger",
                    on_confirm=lambda: self._execute_center_import(
                        holder["form"],
                        refs["status"],
                        prepared,
                    ),
                )
            else:
                refs["status"].text = (
                    f"预检查通过：将向 {prepared['owner_id']} 追加 {count} 条"
                )
                confirm_dialog.show(
                    f"向当前归属追加 {count} 条规则？",
                    impact="将分配新的全局优先级并刷新中心 VPN_FORWARD。",
                    object_count=count,
                    danger_level="warning",
                    on_confirm=lambda: self._execute_center_import(
                        holder["form"],
                        refs["status"],
                        prepared,
                    ),
                )
            return False

        form = create_form_dialog(
            "导入中心规则",
            body=body,
            on_submit=submit,
            submit_label="预检查并导入",
            close_on_success=False,
        )
        holder["form"] = form
        form.card.classes("w-full max-w-3xl")
        form.open()

    def _import_ok_center_backup(self, dialog, text: str) -> None:
        self._import_source_file_mode = None
        self._import_last_uploaded_text = None
        if self._center_import_running:
            self._notify_for_page("中心规则正在导入，请稍候", type="warning")
            return
        dialog.close()
        self._start_center_import_worker(
            "backup 恢复",
            lambda: {"ok": bool(self.rule_service.restore(text)), "count": 0},
            "backup 已恢复",
        )

    def _import_ok_center_simplified(self, dialog, specs) -> None:
        """简写多行：逐条 create，优先级取全局未占用值。"""
        from app.core.config import load_config

        self._import_source_file_mode = None
        self._import_last_uploaded_text = None
        if self._center_import_running:
            self._notify_for_page("中心规则正在导入，请稍候", type="warning")
            return
        oid = (self.current_owner_id or "").strip()
        payloads: list[dict] = []
        try:
            owner_type = resolve_center_owner_type(oid)
        except ValueError as exc:
            logger.error("中心简写导入归属失败: %s", exc)
            self._notify_for_page(str(exc), type="negative")
            return
        config = load_config()
        inst_keys = list((config or {}).get("instances", {}).keys()) if config else []
        instance = inst_keys[0] if inst_keys else "server"
        for i, spec in enumerate(specs):
            try:
                src_sub, src_ips = source_fields_for_center(spec, owner_type)
            except ValueError as exc:
                logger.error("中心简写第 %s 行源字段失败: %s", i + 1, exc)
                self._notify_for_page(f"第 {i + 1} 行: {exc}", type="negative")
                return
            if owner_type == "user" and not src_sub and not src_ips:
                src_ips = read_ccd_vpn_ips([oid])
                if not src_ips:
                    self._notify_for_page(
                        f"第 {i + 1} 行缺 -s 且 CCD 无虚拟 IP。",
                        type="negative",
                    )
                    return
            payload = center_rule_payload_from_simplified(
                spec,
                owner_type=owner_type,
                owner_id=oid,
                instance=instance,
                source_subnet=src_sub,
                source_ips=src_ips,
            )
            if str(payload.get("protocol") or "").lower() in ("any", "all", ""):
                payload["protocol"] = "all"
            payloads.append(payload)
        dialog.close()
        self._start_center_import_worker(
            "中心简写导入",
            lambda: self._run_center_simplified_import(payloads),
            f"简写已追加 {len(payloads)} 条",
        )

    def _start_center_import_worker(self, label: str, worker_fn, success_message: str) -> None:
        """后台执行中心规则导入，避免 iptables 刷新阻塞页面。"""
        self._center_import_running = True
        self._center_import_result = None
        self._notify_for_page(f"{label}已开始，请稍候", type="info")
        logger.info("%s后台任务已启动", label)

        def worker() -> None:
            try:
                result = worker_fn()
                if not result.get("ok"):
                    self._center_import_result = {
                        "ok": False,
                        "message": f"{label}失败",
                    }
                    return
                self._center_import_result = {
                    "ok": True,
                    "message": success_message,
                    "count": result.get("count", 0),
                }
            except Exception as exc:
                logger.exception("%s后台任务失败", label)
                self._center_import_result = {
                    "ok": False,
                    "message": f"{label}失败: {exc}",
                }

        threading.Thread(target=worker, daemon=True, name="firewall-center-import").start()
        self._center_import_timer = ui.timer(0.3, self._poll_center_import_progress)

    def _run_center_simplified_import(self, payloads: list[dict]) -> dict[str, Any]:
        """为简写导入分配优先级后批量写入，只刷新一次 VPN_FORWARD。"""
        all_r = self.rule_service.list_all_flat()
        used = {int(r.get("priority", 0) or 0) for r in all_r}
        mx = max(used) if used else 0
        for payload in payloads:
            cand = mx + 10
            while cand in used:
                cand += 1
            payload["priority"] = int(cand)
            used.add(cand)
            mx = cand
        created = self.rule_service.create_many(payloads)
        return {"ok": True, "count": len(created)}

    def _poll_center_import_progress(self) -> None:
        """在 UI 线程收尾中心导入后台任务。"""
        if self._center_import_result is None:
            return
        if self._center_import_timer is not None:
            if hasattr(self._center_import_timer, "deactivate"):
                self._center_import_timer.deactivate()
            elif hasattr(self._center_import_timer, "active"):
                self._center_import_timer.active = False
            self._center_import_timer = None

        result = self._center_import_result
        self._center_import_result = None
        self._center_import_running = False
        if result.get("ok"):
            self._notify_for_page(str(result.get("message") or "导入完成"), type="positive")
            self._refresh_rules()
        else:
            self._notify_for_page(str(result.get("message") or "导入失败"), type="negative")
