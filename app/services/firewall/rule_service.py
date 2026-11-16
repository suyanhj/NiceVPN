# -*- coding: utf-8 -*-
"""防火墙规则业务逻辑服务

提供规则的 CRUD、拖拽排序、备份恢复等业务操作。
规则以 SQLite 持久化，按 owner 保持原有逻辑分组。

严格语义：覆盖某 owner 后，若 :meth:`_rebuild_all_rules` **任意**失败，
立即用本次操作前的数据库快照恢复该 owner，与对端 ``remote_chain_cache``
的写回前快照语义对齐。
"""

import json
import logging
import os
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Optional

from app.models.firewall import FirewallRule
from app.services.firewall.iptables_mgr import IptablesManager
from app.utils.audit_log import AuditLogger
from app.utils.cidr import validate_cidr, validate_iptables_addr_or_cidr
from app.storage.sqlite_store import SqliteStore, get_default_store

logger = logging.getLogger(__name__)

CE_PEER_RULE_PREFIX = "ce-peer-"


class FirewallRuleService:
    """防火墙规则业务逻辑层。

    规则存储于 SQLite firewall_rules 表，对外继续按 owner_id 返回规则字典列表。
    """

    def __init__(self, store: SqliteStore | None = None):
        self.iptables_mgr = IptablesManager()
        self.audit = AuditLogger()
        self.store = store or get_default_store()
        self._prepush_snapshots: dict[str, list[dict]] = {}

    def _backup_center_rules_before_mutation(self, owner_id: str) -> None:
        """保存 owner 当前数据库快照，供 iptables 刷新失败时回滚。"""
        self._prepush_snapshots[str(owner_id)] = [
            dict(rule) for rule in self._load_rules(owner_id)
        ]

    def _clear_center_prepush_backup(self, owner_id: str) -> None:
        self._prepush_snapshots.pop(str(owner_id), None)

    def _rollback_center_json_after_rebuild_fail(
        self, owner_id: str, had_main_before: bool
    ) -> None:
        """恢复 owner 写入前快照；原先不存在时恢复为空规则集。"""
        key = str(owner_id)
        snapshot = self._prepush_snapshots.get(key)
        if snapshot is None:
            snapshot = [] if not had_main_before else self._load_rules(key)
        self._save_rules(key, snapshot)
        self._prepush_snapshots.pop(key, None)
        logger.warning("中心规则已回滚 SQLite 快照 owner=%s", key)

    def _rollback_center_json_owners_after_rebuild_fail(
        self, owner_ids: list[str], had_main: dict[str, bool]
    ) -> None:
        failures: list[tuple[str, Exception]] = []
        for owner_id in owner_ids:
            try:
                self._rollback_center_json_after_rebuild_fail(
                    owner_id,
                    had_main.get(owner_id, False),
                )
            except Exception as exc:
                failures.append((owner_id, exc))
                logger.critical(
                    "中心规则 SQLite 快照回滚失败 owner=%s: %s",
                    owner_id,
                    exc,
                    exc_info=True,
                )
        if failures:
            first_owner, first_error = failures[0]
            raise RuntimeError(
                f"中心规则 SQLite 回滚不完整，失败 owner 数量={len(failures)}，首个={first_owner}"
            ) from first_error

    def _restore_peer_snapshots(
        self,
        peer_service,
        snapshots: dict[str, dict],
        *,
        reason: str,
    ) -> bool:
        """补偿恢复已修改的 Peer 元数据，并同步其本地 CCD/mesh 派生状态。"""
        ok = True
        for peer_id, snapshot in reversed(list(snapshots.items())):
            try:
                # update(refresh=False) 负责恢复 LAN/CCD/mesh 等派生状态，
                # 最后直接写回原始快照，保留原 updated_at 与完整 payload。
                peer_service.update(
                    peer_id,
                    snapshot,
                    refresh_center_iptables=False,
                )
                peer_service.store.upsert_peer(snapshot)
            except Exception as exc:
                ok = False
                logger.critical(
                    "%s：恢复 Peer 快照失败 peer=%s: %s",
                    reason,
                    peer_id,
                    exc,
                    exc_info=True,
                )
                try:
                    peer_service.store.upsert_peer(snapshot)
                except Exception as store_exc:
                    logger.critical(
                        "%s：恢复 Peer SQLite 快照也失败 peer=%s: %s",
                        reason,
                        peer_id,
                        store_exc,
                        exc_info=True,
                    )
        return ok

    @staticmethod
    def _validate_json_rule_addr_fields(rule: FirewallRule) -> None:
        """落库前校验目标/源 IP 字串，与对端链地址语义保持一致。"""
        dest = (rule.dest_ip or "").strip()
        if dest:
            for part in dest.split(","):
                value = part.strip()
                if value:
                    validate_iptables_addr_or_cidr(value)
        if rule.source_ips:
            for raw in rule.source_ips:
                value = str(raw).strip()
                if value:
                    validate_iptables_addr_or_cidr(value)

    def create(self, rule_data: dict, *, refresh_kernel: bool = True) -> dict:
        """创建防火墙规则，校验优先级后持久化并可选刷新 VPN_FORWARD。"""
        payload = dict(rule_data)
        source_subnet = payload.get("source_subnet")
        if source_subnet and not validate_cidr(source_subnet):
            raise ValueError(f"无效的源子网 CIDR 格式: {source_subnet}")

        if "id" not in payload:
            payload["id"] = str(uuid.uuid4())

        rule = FirewallRule(**payload)
        self._validate_json_rule_addr_fields(rule)

        owner_id = rule.owner_id
        existing_rules = self._load_rules(owner_id)
        had_main = self.store.firewall_owner_exists(owner_id)

        # 中心规则与 Peer 虚拟规则共享同一 priority 空间。
        all_priorities = {
            int(row.get("priority", 0) or 0)
            for row in self.list_unified_flat()
        }
        if rule.priority in all_priorities:
            raise ValueError(
                f"优先级 {rule.priority} 已被占用，"
                f"请选择其他优先级值（已用: {sorted(all_priorities)}）"
            )

        if refresh_kernel and had_main:
            self._backup_center_rules_before_mutation(owner_id)

        rule_dict = rule.model_dump()
        existing_rules.append(rule_dict)
        self._save_rules(owner_id, existing_rules)

        if refresh_kernel:
            try:
                self._refresh_vpn_forward_only()
            except Exception:
                self._rollback_center_json_after_rebuild_fail(owner_id, had_main)
                raise
            if had_main:
                self._clear_center_prepush_backup(owner_id)

        self.audit.log(
            action="create_firewall_rule",
            target_type="firewall_rule",
            target_id=rule.id,
            detail=f"创建防火墙规则: {rule.action} {rule.source_subnet or '*'} -> "
                   f"{rule.dest_ip or '*'}:{rule.dest_port or '*'}",
            result="success",
        )
        logger.info("防火墙规则 %s 创建成功", rule.id)
        return rule_dict

    def create_many(self, rule_payloads: list[dict]) -> list[dict]:
        """批量创建同一 owner 下的防火墙规则，仅刷新一次 ``VPN_FORWARD``。

        参数:
            rule_payloads: 与 :meth:`create` 同形的规则字典列表，必须归属同一 owner。

        返回:
            成功落库的规则字典列表。

        异常:
            ValueError: 空列表、跨 owner、字段校验失败或优先级冲突。
            RuntimeError: VPN_FORWARD 刷新失败，已按现有回滚策略恢复 SQLite。
        """
        if not rule_payloads:
            raise ValueError("批量创建规则不能为空")

        rules_to_add: list[dict] = []
        owner_id = ""
        incoming_priorities: set[int] = set()
        for idx, raw in enumerate(rule_payloads, start=1):
            payload = dict(raw)
            source_subnet = payload.get("source_subnet")
            if source_subnet and not validate_cidr(source_subnet):
                raise ValueError(f"第 {idx} 条规则源子网 CIDR 无效: {source_subnet}")
            if "id" not in payload:
                payload["id"] = str(uuid.uuid4())
            rule = FirewallRule(**payload)
            self._validate_json_rule_addr_fields(rule)
            if not owner_id:
                owner_id = rule.owner_id
            elif owner_id != rule.owner_id:
                raise ValueError("批量创建规则必须属于同一归属")
            if rule.priority in incoming_priorities:
                raise ValueError(f"批量数据中优先级重复: {rule.priority}")
            incoming_priorities.add(rule.priority)
            rules_to_add.append(rule.model_dump())

        existing_rules = self._load_rules(owner_id)
        had_main = self.store.firewall_owner_exists(owner_id)
        existing_ids = {str(r.get("id") or "") for r in existing_rules}
        incoming_ids = {str(r.get("id") or "") for r in rules_to_add}
        if existing_ids & incoming_ids:
            raise ValueError("批量数据中存在已落库的规则 ID")

        all_rules = self.list_unified_flat()
        all_priorities = {int(r.get("priority", 0) or 0) for r in all_rules}
        duplicated = sorted(incoming_priorities & all_priorities)
        if duplicated:
            raise ValueError(f"优先级已被占用: {duplicated}")

        if had_main:
            self._backup_center_rules_before_mutation(owner_id)
        merged_rules = existing_rules + rules_to_add
        self._save_rules(owner_id, merged_rules)
        try:
            self._refresh_vpn_forward_only()
        except Exception:
            self._rollback_center_json_after_rebuild_fail(owner_id, had_main)
            raise
        if had_main:
            self._clear_center_prepush_backup(owner_id)

        self.audit.log(
            action="create_firewall_rules_batch",
            target_type="firewall_rule",
            target_id=owner_id,
            detail=f"批量创建防火墙规则 {len(rules_to_add)} 条",
            result="success",
        )
        logger.info("已批量创建防火墙规则 owner=%s count=%s", owner_id, len(rules_to_add))
        return rules_to_add

    def update_by_id(self, rule_id: str, rule_data: dict) -> dict:
        """原地更新一条规则并仅刷新 ``VPN_FORWARD``；校验同 :meth:`create`，但优先级允许与**自身**相同。

        与「先删后建」不同：任一步失败时原规则仍在库里，避免保存失败后规则 ID 丢失。

        参数:
            rule_id: 已有规则 id
            rule_data: 与 ``create`` 同形字段，与库中该条合并（``id``、``owner_id`` 以库为准除非显式覆盖）

        返回:
            更新后的规则字典

        异常:
            ValueError: 规则不存在、校验失败或优先级与其他规则冲突
        """
        rid = str(rule_id)
        owner_id, rules = self._find_rule_owner(rid)
        if owner_id is None:
            raise ValueError(f"规则 {rid} 不存在")
        idx = next((i for i, r in enumerate(rules) if str(r.get("id")) == rid), None)
        if idx is None:
            raise ValueError(f"规则 {rid} 不存在")
        merged = {**rules[idx], **rule_data, "id": rid}
        source_subnet = merged.get("source_subnet")
        if source_subnet and not validate_cidr(source_subnet):
            raise ValueError(f"无效的源子网 CIDR 格式: {source_subnet}")
        rule = FirewallRule(**merged)
        self._validate_json_rule_addr_fields(rule)
        all_rules = self.list_unified_flat()
        all_priorities = {
            int(r.get("priority", 0) or 0)
            for r in all_rules
            if str(r.get("id")) != rid
        }
        current_priority = int(rules[idx].get("priority", 0) or 0)
        if rule.priority != current_priority and rule.priority in all_priorities:
            raise ValueError(
                f"优先级 {rule.priority} 已被占用，"
                f"请选择其他优先级值（已用: {sorted(all_priorities)}）"
            )
        out = rule.model_dump()
        rules[idx] = out
        had_main = self.store.firewall_owner_exists(owner_id)
        if had_main:
            self._backup_center_rules_before_mutation(owner_id)
        self._save_rules(owner_id, rules)
        try:
            self._refresh_vpn_forward_only()
        except Exception:
            self._rollback_center_json_after_rebuild_fail(owner_id, had_main)
            raise
        if had_main:
            self._clear_center_prepush_backup(owner_id)
        self.audit.log(
            action="update_firewall_rule",
            target_type="firewall_rule",
            target_id=rid,
            detail=f"更新防火墙规则: {rule.action} {rule.source_subnet or '*'} -> "
            f"{rule.dest_ip or '*'}:{rule.dest_port or '*'}",
            result="success",
        )
        logger.info("防火墙规则 %s 已更新", rid)
        return out

    def reorder(self, owner_id: str, new_id_order: list[str]) -> bool:
        """按传入顺序重排规则；未传入的规则稳定追加到末尾并重新分配优先级。"""
        if not (owner_id or "").strip():
            return self._reorder_unified_global(new_id_order)

        rules = self._load_rules(owner_id)
        rule_map = {str(row.get("id") or ""): row for row in rules}
        ordered_ids: list[str] = []
        seen: set[str] = set()
        for raw_id in new_id_order:
            rule_id = str(raw_id or "")
            if rule_id not in rule_map:
                logger.warning("排序时发现未知规则 ID: %s，跳过", rule_id)
                continue
            if rule_id in seen:
                logger.warning("排序时发现重复规则 ID: %s，跳过", rule_id)
                continue
            seen.add(rule_id)
            ordered_ids.append(rule_id)

        for row in rules:
            rule_id = str(row.get("id") or "")
            if rule_id and rule_id not in seen:
                seen.add(rule_id)
                ordered_ids.append(rule_id)

        now = datetime.now().isoformat()
        reordered: list[dict] = []
        for idx, rule_id in enumerate(ordered_ids):
            row = dict(rule_map[rule_id])
            row["priority"] = (idx + 1) * 10
            row["updated_at"] = now
            reordered.append(row)

        had_main = self.store.firewall_owner_exists(owner_id)
        if had_main:
            self._backup_center_rules_before_mutation(owner_id)
        self._save_rules(owner_id, reordered)
        try:
            self._refresh_vpn_forward_only()
        except Exception:
            self._rollback_center_json_after_rebuild_fail(owner_id, had_main)
            raise
        if had_main:
            self._clear_center_prepush_backup(owner_id)

        self.audit.log(
            action="reorder_firewall_rules",
            target_type="firewall_rule",
            target_id=owner_id,
            detail=f"规则重新排序，新顺序: {ordered_ids}",
            result="success",
        )
        logger.info("owner %s 的防火墙规则已重新排序", owner_id)
        return True

    def _reorder_unified_global(self, new_id_order: list[str]) -> bool:
        """全局排序：中心规则与 Peer 中心放行统一编号，失败时同时恢复两侧元数据。"""
        from app.services.peer_instance.service import PeerService

        unified = self.list_unified_flat()
        rule_map = {str(row.get("id") or ""): row for row in unified}
        ordered_ids: list[str] = []
        seen: set[str] = set()
        for raw_id in new_id_order:
            rule_id = str(raw_id or "")
            if rule_id not in rule_map:
                logger.warning("全局排序时发现未知规则 ID: %s，跳过", rule_id)
                continue
            if rule_id in seen:
                logger.warning("全局排序时发现重复规则 ID: %s，跳过", rule_id)
                continue
            seen.add(rule_id)
            ordered_ids.append(rule_id)
        for row in unified:
            rule_id = str(row.get("id") or "")
            if rule_id and rule_id not in seen:
                seen.add(rule_id)
                ordered_ids.append(rule_id)

        if not ordered_ids:
            return True

        priorities = {
            rule_id: (index + 1) * 10
            for index, rule_id in enumerate(ordered_ids)
        }
        peer_service = PeerService()
        peer_snapshots: dict[str, dict] = {}
        peer_priority_by_id: dict[str, int] = {}
        reordered_by_owner: dict[str, list[dict]] = defaultdict(list)
        now = datetime.now().isoformat()

        for rule_id in ordered_ids:
            row = rule_map[rule_id]
            if row.get("_row_kind") != "center_peer":
                continue
            peer_id = str(row.get("_peer_id") or "")
            snapshot = peer_service.get(peer_id)
            if not snapshot:
                raise ValueError(f"全局排序失败：对端实例不存在 {peer_id}")
            peer_snapshots.setdefault(peer_id, dict(snapshot))
            peer_priority_by_id[peer_id] = priorities[rule_id]

        for rule_id in ordered_ids:
            row = rule_map[rule_id]
            if row.get("_row_kind") == "center_peer":
                continue
            rule = dict(row)
            rule["priority"] = priorities[rule_id]
            rule["updated_at"] = now
            rule.pop("_row_kind", None)
            rule.pop("_peer_id", None)
            owner_id = str(rule.pop("_owner_id", "") or "")
            if owner_id:
                reordered_by_owner[owner_id].append(rule)

        owner_ids = list(reordered_by_owner)
        had_main = {
            owner_id: self.store.firewall_owner_exists(owner_id)
            for owner_id in owner_ids
        }
        for owner_id in owner_ids:
            if had_main[owner_id]:
                self._backup_center_rules_before_mutation(owner_id)

        try:
            for peer_id, priority in peer_priority_by_id.items():
                peer_service.patch_center_forward_priority(peer_id, priority)
            for owner_id, rules in reordered_by_owner.items():
                self._save_rules(owner_id, rules)
            self._refresh_vpn_forward_only()
        except Exception:
            try:
                self._rollback_center_json_owners_after_rebuild_fail(owner_ids, had_main)
            except Exception:
                logger.critical("全局排序失败：中心规则 SQLite 回滚不完整", exc_info=True)
            if peer_snapshots:
                self._restore_peer_snapshots(
                    peer_service,
                    peer_snapshots,
                    reason="全局防火墙排序失败",
                )
            raise

        for owner_id in owner_ids:
            if had_main[owner_id]:
                self._clear_center_prepush_backup(owner_id)
        self.audit.log(
            action="reorder_firewall_rules",
            target_type="firewall_rule",
            target_id="global",
            detail=f"全局规则重新排序（含对端中心放行），新顺序: {ordered_ids}",
            result="success",
        )
        logger.info("全局防火墙规则已重新排序（含对端，仅刷新 VPN_FORWARD）")
        return True

    def delete(self, rule_id: str) -> bool:
        """删除指定规则；Peer 虚拟规则失败时恢复 Peer 与派生配置。"""
        rid = str(rule_id)
        if rid.startswith(CE_PEER_RULE_PREFIX):
            from app.services.peer_instance.service import PeerService

            peer_id = rid[len(CE_PEER_RULE_PREFIX):]
            peer_service = PeerService()
            snapshot = peer_service.get(peer_id)
            if not snapshot:
                raise ValueError(f"对端实例不存在: {peer_id}")
            try:
                peer_service.update(
                    peer_id,
                    {"lan_cidrs": []},
                    refresh_center_iptables=False,
                )
                self._refresh_vpn_forward_only()
            except Exception:
                self._restore_peer_snapshots(
                    peer_service,
                    {peer_id: dict(snapshot)},
                    reason=f"删除中心对端放行失败 peer={peer_id}",
                )
                raise

            self.audit.log(
                action="delete_firewall_rule",
                target_type="firewall_rule",
                target_id=rid,
                detail=f"清空对端 LAN 以移除中心放行（对端实例 {peer_id}）",
                result="success",
            )
            logger.info("已通过对端 %s 清空 LAN 删除防火墙列表项", peer_id)
            return True

        owner_id, rules = self._find_rule_owner(rid)
        if owner_id is None:
            raise ValueError(f"规则 {rid} 不存在")

        updated_rules = [row for row in rules if str(row.get("id")) != rid]
        had_main = self.store.firewall_owner_exists(owner_id)
        if had_main:
            self._backup_center_rules_before_mutation(owner_id)
        self._save_rules(owner_id, updated_rules)
        try:
            self._refresh_vpn_forward_only()
        except Exception:
            self._rollback_center_json_after_rebuild_fail(owner_id, had_main)
            raise
        if had_main:
            self._clear_center_prepush_backup(owner_id)

        self.audit.log(
            action="delete_firewall_rule",
            target_type="firewall_rule",
            target_id=rid,
            detail=f"删除防火墙规则 {rid}",
            result="success",
        )
        logger.info("防火墙规则 %s 已删除", rid)
        return True

    def set_enabled(self, rule_id: str, enabled: bool) -> bool:
        """设置规则启停状态；Peer 虚拟规则刷新失败时恢复其原始状态。"""
        rid = str(rule_id)
        if rid.startswith(CE_PEER_RULE_PREFIX):
            from app.services.peer_instance.service import PeerService

            peer_id = rid[len(CE_PEER_RULE_PREFIX):]
            peer_service = PeerService()
            peer_service.patch_center_forward_enabled(peer_id, enabled)

            status_text = "启用" if enabled else "停用"
            self.audit.log(
                action="toggle_firewall_rule",
                target_type="firewall_rule",
                target_id=rid,
                detail=f"中心侧对端 LAN 放行已{status_text}（对端 {peer_id}）",
                result="success",
            )
            logger.info("中心侧对端 LAN 放行已%s peer=%s", status_text, peer_id[:8] if len(peer_id) > 8 else peer_id)
            return True

        owner_id, rules = self._find_rule_owner(rid)
        if owner_id is None:
            raise ValueError(f"规则 {rid} 不存在")

        for row in rules:
            if str(row.get("id")) == rid:
                row["enabled"] = bool(enabled)
                row["updated_at"] = datetime.now().isoformat()
                break
        had_main = self.store.firewall_owner_exists(owner_id)
        if had_main:
            self._backup_center_rules_before_mutation(owner_id)
        self._save_rules(owner_id, rules)
        try:
            self._refresh_vpn_forward_only()
        except Exception:
            self._rollback_center_json_after_rebuild_fail(owner_id, had_main)
            raise
        if had_main:
            self._clear_center_prepush_backup(owner_id)

        status_text = "启用" if enabled else "停用"
        self.audit.log(
            action="toggle_firewall_rule",
            target_type="firewall_rule",
            target_id=rid,
            detail=f"防火墙规则 {rid} 已{status_text}",
            result="success",
        )
        logger.info("防火墙规则 %s 已%s", rid, status_text)
        return True

    def backup(self) -> str:
        """导出所有 owner 的防火墙规则为 JSON 字符串。

        除 SQLite 中按归属保存的中心规则外，包含对端在中心侧
        ``VPN_FORWARD`` 合并所需的 LAN / 优先级 / 启停，与界面「全部归属」中对端
        卡片一致，避免仅配置对端放行时导出看似「无规则」。

        返回:
            包含 ``rules_by_owner`` 与 ``peers_center_forward`` 的 JSON 字符串
        """
        all_rules: dict[str, list[dict]] = {
            owner_id: self._load_rules(owner_id)
            for owner_id in self.store.list_firewall_owner_ids()
        }

        from app.services.peer_instance.service import PeerService

        peers_center_forward: list[dict] = []
        for p in PeerService().list_all():
            pid = str(p.get("id") or "").strip()
            if not pid:
                continue
            peers_center_forward.append(
                {
                    "id": pid,
                    "name": str(p.get("name") or ""),
                    "lan_cidrs": [str(c).strip() for c in (p.get("lan_cidrs") or []) if str(c).strip()],
                    "center_forward_priority": int(p.get("center_forward_priority", 500_000)),
                    "center_forward_enabled": bool(p.get("center_forward_enabled", True)),
                    "center_forward_dest_ip": str(p.get("center_forward_dest_ip") or "").strip(),
                    "center_forward_dest_port": str(p.get("center_forward_dest_port") or "").strip(),
                    "center_forward_protocol": str(p.get("center_forward_protocol") or "all").strip() or "all",
                    "center_forward_rule_description": str(p.get("center_forward_rule_description") or "").strip(),
                }
            )

        backup_data = {
            "version": 2,
            "exported_at": datetime.now().isoformat(),
            "rules_by_owner": all_rules,
            "peers_center_forward": peers_center_forward,
        }

        n_rules = sum(len(v) for v in all_rules.values())
        self.audit.log(
            action="backup_firewall_rules",
            target_type="firewall",
            target_id="all",
            detail=f"备份：JSON 规则 {n_rules} 条，对端中心策略 {len(peers_center_forward)} 条",
            result="success",
        )

        return json.dumps(backup_data, ensure_ascii=False, indent=2)

    def restore(self, json_str: str) -> bool:
        """从 JSON 恢复规则集；任何写入或内核刷新失败均补偿恢复 SQLite 与 Peer。"""
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            logger.error("恢复规则 JSON 解析失败: %s", exc)
            return False

        if not isinstance(data, dict) or "rules_by_owner" not in data:
            logger.error("恢复数据中缺少 rules_by_owner 键")
            return False
        rules_by_owner = data.get("rules_by_owner") or {}
        peers_cf = data.get("peers_center_forward")
        if not isinstance(rules_by_owner, dict):
            logger.error("恢复数据 rules_by_owner 必须为对象")
            return False
        if not rules_by_owner and not (isinstance(peers_cf, list) and peers_cf):
            logger.error("恢复数据为空：rules_by_owner 无内容且未包含对端中心策略")
            return False

        owner_ids = list(
            dict.fromkeys(
                str(owner_id)
                for owner_id, rows in rules_by_owner.items()
                if isinstance(rows, list)
            )
        )
        had_main = {
            owner_id: self.store.firewall_owner_exists(owner_id)
            for owner_id in owner_ids
        }
        for owner_id in owner_ids:
            if had_main[owner_id]:
                self._backup_center_rules_before_mutation(owner_id)

        from app.services.peer_instance.service import PeerService
        peer_service = PeerService()
        peer_snapshots: dict[str, dict] = {}

        try:
            for owner_id, rows in rules_by_owner.items():
                if not isinstance(rows, list):
                    logger.warning("owner %s 的 rules 非列表，已跳过", owner_id)
                    continue
                validated_rules: list[dict] = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    try:
                        rule = FirewallRule(**row)
                        self._validate_json_rule_addr_fields(rule)
                        validated_rules.append(rule.model_dump())
                    except Exception as exc:
                        logger.warning(
                            "跳过无效规则: %s, 原因: %s",
                            row.get("id", "?"),
                            exc,
                        )
                        continue
                self._save_rules(str(owner_id), validated_rules)

            if isinstance(peers_cf, list) and peers_cf:
                for item in peers_cf:
                    if not isinstance(item, dict):
                        continue
                    peer_id = str(item.get("id") or "").strip()
                    if not peer_id:
                        continue
                    current = peer_service.get(peer_id)
                    if not current:
                        logger.warning(
                            "备份中 peers_center_forward 对端已不存在，跳过: %s",
                            peer_id,
                        )
                        continue
                    peer_snapshots.setdefault(peer_id, dict(current))
                    patch: dict = {
                        "lan_cidrs": [
                            str(c).strip()
                            for c in (item.get("lan_cidrs") or [])
                            if str(c).strip()
                        ],
                        "center_forward_priority": int(
                            item.get("center_forward_priority")
                            or current.get("center_forward_priority")
                            or 500_000
                        ),
                        "center_forward_enabled": bool(
                            item.get("center_forward_enabled")
                            if "center_forward_enabled" in item
                            else current.get("center_forward_enabled", True)
                        ),
                    }
                    for key in (
                        "center_forward_dest_ip",
                        "center_forward_dest_port",
                        "center_forward_protocol",
                        "center_forward_rule_description",
                    ):
                        if key in item and item.get(key) is not None:
                            patch[key] = str(item.get(key) or "").strip()
                    if "center_forward_protocol" in patch and not patch.get("center_forward_protocol"):
                        patch["center_forward_protocol"] = "all"
                    name = str(item.get("name") or "").strip()
                    if name:
                        patch["name"] = name
                    peer_service.update(
                        peer_id,
                        patch,
                        refresh_center_iptables=False,
                    )

            self._refresh_vpn_forward_only()
        except Exception as exc:
            logger.error("防火墙规则恢复失败，开始补偿回滚: %s", exc, exc_info=True)
            try:
                self._rollback_center_json_owners_after_rebuild_fail(owner_ids, had_main)
            except Exception:
                logger.critical("防火墙恢复失败：中心规则 SQLite 回滚不完整", exc_info=True)
            if peer_snapshots:
                self._restore_peer_snapshots(
                    peer_service,
                    peer_snapshots,
                    reason="防火墙备份恢复失败",
                )
            return False

        for owner_id in owner_ids:
            if had_main[owner_id]:
                self._clear_center_prepush_backup(owner_id)

        peer_count = len(peers_cf) if isinstance(peers_cf, list) else 0
        self.audit.log(
            action="restore_firewall_rules",
            target_type="firewall",
            target_id="all",
            detail=f"从备份恢复：{len(rules_by_owner)} 个 owner，对端中心策略 {peer_count} 条",
            result="success",
        )
        logger.info("防火墙规则已从备份恢复")
        return True

    def list_by_owner(self, owner_id: str) -> list[dict]:
        """按 owner 列出规则，按 priority 升序排序。

        参数:
            owner_id: 归属对象 ID（组ID 或用户名）

        返回:
            规则字典列表（按优先级排序）
        """
        rules = self._load_rules(owner_id)
        return sorted(rules, key=lambda r: r.get("priority", 0))

    def list_all_flat(self) -> list[dict]:
        """列出所有 owner 的规则，每项附带 owner_id 字段。"""
        merged: list[dict] = []
        for rule in self.store.list_firewall_rules():
            row = dict(rule)
            row["_owner_id"] = str(row.get("owner_id") or "")
            merged.append(row)
        return merged

    @staticmethod
    def _center_peer_virtual_rule(peer: dict) -> dict | None:
        """把 Peer 元数据转换为中心 VPN_FORWARD 的虚拟规则行。"""

        pid = str(peer.get("id") or "").strip()
        if not pid:
            return None
        lan = [
            str(c).strip()
            for c in (peer.get("lan_cidrs") or [])
            if str(c).strip()
        ]
        if not lan:
            return None
        c_raw = f"{IptablesManager.CENTER_PEER_COMMENT_TAG} peer={pid}"
        c_peer = c_raw[:256].replace('"', "'")
        return {
            "id": f"{CE_PEER_RULE_PREFIX}{pid}",
            "_row_kind": "center_peer",
            "_peer_id": pid,
            "owner_type": "peer",
            "owner_id": pid,
            "priority": int(peer.get("center_forward_priority", 500_000)),
            "description": str(
                peer.get("center_forward_rule_description") or ""
            ).strip(),
            "_center_tag_line": c_peer,
            "enabled": bool(peer.get("center_forward_enabled", True)),
            "protocol": str(
                peer.get("center_forward_protocol") or "all"
            ).strip()
            or "all",
            "action": "accept",
            "deployment_target": "center",
            "source_subnet": ",".join(lan),
            "source_ips": None,
            "dest_ip": str(peer.get("center_forward_dest_ip") or "").strip() or None,
            "dest_port": (
                str(peer.get("center_forward_dest_port") or "").strip() or None
            ),
            "instance": "server",
            "_owner_id": pid,
        }

    def list_unified_flat(self) -> list[dict]:
        """列出全部中心规则；完整排序/备份/优先级调整路径使用。"""

        from app.services.peer_instance.service import PeerService

        out: list[dict] = []
        for rule in self.list_all_flat():
            row = dict(rule)
            row["_row_kind"] = "json"
            out.append(row)
        for peer in PeerService().list_all():
            row = self._center_peer_virtual_rule(peer)
            if row is not None:
                out.append(row)
        return sorted(out, key=lambda x: (x.get("priority", 0), str(x.get("id", ""))))

    def list_unified_page(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        search: str = "",
        owner_ids: list[str] | None = None,
        owner_types: list[str] | None = None,
        enabled_values: list[bool] | None = None,
        protocols: list[str] | None = None,
        actions: list[str] | None = None,
        kind_keys: list[str] | None = None,
        text_filters: dict[str, tuple[list[str], str, str]] | None = None,
        sort_by: str = "priority",
        sort_desc: bool = False,
    ) -> tuple[list[dict], int]:
        """数据库级分页读取统一中心规则集合。"""

        records, total = self.store.list_firewall_unified_page(
            offset=offset,
            limit=limit,
            search=search,
            owner_ids=owner_ids,
            owner_types=owner_types,
            enabled_values=enabled_values,
            protocols=protocols,
            actions=actions,
            kind_keys=kind_keys,
            text_filters=text_filters,
            sort_by=sort_by,
            sort_desc=sort_desc,
        )
        rows: list[dict] = []
        for record in records:
            payload = dict(record.get("payload") or {})
            if record.get("row_kind") == "center_peer":
                row = self._center_peer_virtual_rule(payload)
                if row is not None:
                    rows.append(row)
                continue
            payload["_row_kind"] = "json"
            payload["_owner_id"] = str(
                payload.get("owner_id") or record.get("owner_id") or ""
            )
            rows.append(payload)
        return rows, total

    def list_unified_facets(self) -> dict[str, list]:
        """服务端分页 Grid 使用的完整筛选值元数据。"""

        return self.store.list_firewall_unified_facets()

    # ---- 内部方法 ----

    def _load_rules(self, owner_id: str) -> list[dict]:
        """读取指定 owner 的规则列表。"""
        return self.store.get_firewall_owner(owner_id)

    def _save_rules(self, owner_id: str, rules: list[dict]) -> None:
        """原子替换指定 owner 的规则列表。"""
        self.store.replace_firewall_owner(owner_id, rules)

    def _find_rule_owner(self, rule_id: str) -> tuple[Optional[str], list[dict]]:
        """按规则主键定位 owner，并返回该 owner 的完整规则集。"""
        return self.store.find_firewall_rule_owner(rule_id)

    def _collect_center_peers(self) -> list[tuple[str, list[str], int, dict]]:
        """对端内网非空时参与 ``VPN_FORWARD`` 合并，priority 与 JSON 规则同一数值空间。

        第四元为与对端 ``center_forward_*`` 一致的匹配扩展（目标/端口/协议/自定义注释）。"""
        from app.services.peer_instance.service import PeerService

        out: list[tuple[str, list[str], int, dict]] = []
        for row in PeerService().list_all():
            pid = str(row.get("id") or "").strip()
            if not pid:
                continue
            if not bool(row.get("center_forward_enabled", True)):
                continue
            cidrs = [str(c).strip() for c in (row.get("lan_cidrs") or []) if str(c).strip()]
            if not cidrs:
                continue
            pri = int(row.get("center_forward_priority", 500_000))
            proto = str(row.get("center_forward_protocol") or "all").strip().lower()
            extra = {
                "dest_ip": str(row.get("center_forward_dest_ip") or "").strip(),
                "dest_port": str(row.get("center_forward_dest_port") or "").strip(),
                "protocol": proto,
                "rule_description": str(row.get("center_forward_rule_description") or "").strip(),
            }
            out.append((pid, cidrs, pri, extra))
        return out

    def _resolve_tag_rules(self, rows: list[dict]) -> list[FirewallRule]:
        """标签规则不持久化 IP 快照；每次写内核时动态解析当前地址。"""
        from app.services.firewall.tag_service import TagService

        tags = TagService(self.store)
        ccd_ips = None
        resolved: dict[str, tuple[str, ...]] = {}
        result: list[FirewallRule] = []
        for row in rows:
            if not row.get("enabled", True):
                continue
            if row.get("owner_type") != "tag":
                result.append(FirewallRule(**row))
                continue
            tag_id = str(row.get("owner_id") or "")
            if tag_id not in resolved:
                if ccd_ips is None:
                    from app.services.user.crud import UserService
                    ccd_ips = UserService(store=self.store).list_ccd_virtual_ipv4_by_username()
                resolved[tag_id] = tags.resolve_addresses(tag_id, ccd_ips=ccd_ips)
            addresses = resolved[tag_id]
            if not addresses:
                # 空标签不能生成没有 src 匹配的宽松规则。
                logger.info("跳过空成员标签规则 tag=%s rule=%s", tag_id, row.get("id"))
                continue
            effective = dict(row)
            effective["source_subnet"] = None
            effective["source_ips"] = list(addresses)
            result.append(FirewallRule(**effective))
        result.sort(key=lambda rule: rule.priority)
        return result

    def _rebuild_all_rules(self) -> None:
        """合并全部 owner 的已启用规则、对端中心放行，一次性重建 iptables / ipset。"""
        merged_raw = self.store.list_firewall_rules()
        enabled_rules = self._resolve_tag_rules(merged_raw)
        center_peers = self._collect_center_peers()
        if not self.iptables_mgr.rebuild_rules(enabled_rules, center_peers=center_peers):
            msg = (
                "iptables/ipset 重建失败：常见原因包括 iptables-restore 报错、FORWARD/INPUT 钩子写入失败、"
                "无 CAP_NET_ADMIN/root、conntrack 模块缺失等。应用日志中搜索 iptables-restore、无法写入 FORWARD 钩子。"
            )
            logger.error(msg)
            raise RuntimeError(msg)

    def _refresh_vpn_forward_only(self) -> None:
        """规则管理专用：只刷新 ``VPN_FORWARD`` 链，不触碰 INPUT、FORWARD 钩子、ip_forward、MASQUERADE。"""
        merged_raw = self.store.list_firewall_rules()
        enabled_rules = self._resolve_tag_rules(merged_raw)
        center_peers = self._collect_center_peers()
        if not self.iptables_mgr.refresh_vpn_forward_only(enabled_rules, center_peers=center_peers):
            msg = (
                "VPN_FORWARD 刷新失败：规则管理页仅允许修改 VPN_FORWARD 链；"
                "请检查 iptables-restore 与现有 ipset 是否正常。"
            )
            logger.error(msg)
            raise RuntimeError(msg)

    def refresh_vpn_forward_only(self) -> None:
        """公开给对端实例等页面调用：只刷新中心 ``VPN_FORWARD`` 链。"""
        self._refresh_vpn_forward_only()

    def rebuild_iptables(self) -> None:
        """从磁盘规则重新加载 iptables（含 FORWARD 钩子、ip_forward、NAT MASQUERADE）。"""
        self._rebuild_all_rules()
