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
        snapshot = self._prepush_snapshots.pop(str(owner_id), None)
        if snapshot is None:
            snapshot = [] if not had_main_before else self._load_rules(owner_id)
        self._save_rules(owner_id, snapshot)
        logger.warning("中心规则已回滚 SQLite 快照 owner=%s", owner_id)

    def _rollback_center_json_owners_after_rebuild_fail(
        self, owner_ids: list[str], had_main: dict[str, bool]
    ) -> None:
        for oid in owner_ids:
            self._rollback_center_json_after_rebuild_fail(oid, had_main.get(oid, False))

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
        """创建防火墙规则，执行三重校验后持久化并可选刷新 ``VPN_FORWARD``。

        三重校验：
        1. 端口格式校验（由 Pydantic 模型 field_validator 完成）
        2. 源子网 CIDR 格式校验；目标/源 IP 字串（与对端链同语义，见 :meth:`_validate_json_rule_addr_fields`）
        3. 同一 owner 下优先级唯一性校验

        参数:
            rule_data: 规则数据字典，字段参见 FirewallRule 模型
            refresh_kernel: 为 False 时只落库 SQLite，不写内核（初始化向导在随后 ``rebuild_iptables`` 时使用）

        返回:
            创建成功的规则字典

        异常:
            ValueError: 校验失败时抛出
        """
        # CIDR 格式校验
        source_subnet = rule_data.get("source_subnet")
        if source_subnet and not validate_cidr(source_subnet):
            raise ValueError(f"无效的源子网 CIDR 格式: {source_subnet}")

        # 生成唯一 ID
        if "id" not in rule_data:
            rule_data["id"] = str(uuid.uuid4())

        # 通过 Pydantic 模型校验（端口格式等）
        rule = FirewallRule(**rule_data)
        self._validate_json_rule_addr_fields(rule)

        # 读取该 owner 的现有规则
        owner_id = rule.owner_id
        existing_rules = self._load_rules(owner_id)
        had_main = self.store.firewall_owner_exists(owner_id)
        if had_main:
            self._backup_center_rules_before_mutation(owner_id)

        # 全局优先级唯一性校验（跨所有 owner）
        all_rules = self.list_all_flat()
        all_priorities = {r.get("priority", 0) for r in all_rules}
        if rule.priority in all_priorities:
            raise ValueError(
                f"优先级 {rule.priority} 已被占用，"
                f"请选择其他优先级值（已用: {sorted(all_priorities)}）"
            )

        # 持久化
        rule_dict = rule.model_dump()
        existing_rules.append(rule_dict)
        self._save_rules(owner_id, existing_rules)

        # 规则管理页只允许修改 VPN_FORWARD，项目级 INPUT/MASQUERADE 等由启动/停止流程维护
        if refresh_kernel:
            try:
                self._refresh_vpn_forward_only()
            except Exception:
                self._rollback_center_json_after_rebuild_fail(owner_id, had_main)
                raise
            if had_main:
                self._clear_center_prepush_backup(owner_id)

        # 审计日志
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

        all_rules = self.list_all_flat()
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
        all_rules = self.list_all_flat()
        all_priorities = {r.get("priority", 0) for r in all_rules if str(r.get("id")) != rid}
        if rule.priority in all_priorities:
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
        """拖拽排序后重新计算 priority，并且只刷新 ``VPN_FORWARD`` 链。

        按传入的 ID 顺序重新分配优先级值，步长为 10（方便后续插入）。
        如果 owner_id 为空，则进行全局重排。

        参数:
            owner_id:     规则归属对象 ID，为空时全局排序
            new_id_order: 拖拽排序后的规则 ID 列表（按新顺序排列）

        返回:
            操作是否成功
        """
        if not (owner_id or "").strip():
            return self._reorder_unified_global(new_id_order)

        rules = self._load_rules(owner_id)

        # 构建 ID -> 规则字典 的映射
        rule_map = {r["id"]: r for r in rules}

        # 按新顺序重新分配优先级
        reordered: list[dict] = []
        for idx, rule_id in enumerate(new_id_order):
            if rule_id not in rule_map:
                logger.warning("排序时发现未知规则 ID: %s，跳过", rule_id)
                continue
            rule = rule_map[rule_id]
            rule["priority"] = (idx + 1) * 10
            rule["updated_at"] = datetime.now().isoformat()
            reordered.append(rule)

        # 保留不在排序列表中的规则（追加到末尾）
        reordered_ids = set(new_id_order)
        for rule in rules:
            if rule["id"] not in reordered_ids:
                reordered.append(rule)

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
            detail=f"规则重新排序，新顺序: {new_id_order}",
            result="success",
        )

        logger.info("owner %s 的防火墙规则已重新排序", owner_id)
        return True

    def _reorder_unified_global(self, new_id_order: list[str]) -> bool:
        """全局排序：中心规则与对端 ``center_forward_priority`` 同步为 (n*10)，仅刷新 ``VPN_FORWARD``。"""
        from app.services.peer_instance.service import PeerService

        unified = self.list_unified_flat()
        rule_map = {r["id"]: r for r in unified}
        pris = {rid: (i + 1) * 10 for i, rid in enumerate(new_id_order) if rid in rule_map}
        ps = PeerService()
        for rid, pri in pris.items():
            row = rule_map[rid]
            if row.get("_row_kind") == "center_peer":
                ps.patch_center_forward_priority(str(row["_peer_id"]), pri)

        reordered_ids = set(new_id_order)
        reordered_by_owner: dict[str, list[dict]] = defaultdict(list)
        for rid in new_id_order:
            row = rule_map.get(rid)
            if not row or row.get("_row_kind") != "json":
                continue
            r = dict(row)
            r["priority"] = pris[rid]
            r["updated_at"] = datetime.now().isoformat()
            r.pop("_row_kind", None)
            r.pop("_peer_id", None)
            oid = r.pop("_owner_id", "")
            reordered_by_owner[oid].append(r)

        for row in unified:
            rid = row["id"]
            if rid in reordered_ids:
                continue
            if row.get("_row_kind") == "center_peer":
                continue
            r = dict(row)
            r.pop("_row_kind", None)
            r.pop("_peer_id", None)
            oid = r.pop("_owner_id", "")
            reordered_by_owner[oid].append(r)

        oids = list(dict.fromkeys(oid for oid in reordered_by_owner if oid))
        had_main = {oid: self.store.firewall_owner_exists(oid) for oid in oids}
        for oid in oids:
            if had_main[oid]:
                self._backup_center_rules_before_mutation(oid)
        for oid, rules in reordered_by_owner.items():
            if oid:
                self._save_rules(oid, rules)

        try:
            self._refresh_vpn_forward_only()
        except Exception:
            self._rollback_center_json_owners_after_rebuild_fail(oids, had_main)
            raise
        for oid in oids:
            if had_main[oid]:
                self._clear_center_prepush_backup(oid)
        self.audit.log(
            action="reorder_firewall_rules",
            target_type="firewall_rule",
            target_id="global",
            detail=f"全局规则重新排序（含对端中心放行），新顺序: {new_id_order}",
            result="success",
        )
        logger.info("全局防火墙规则已重新排序（含对端，仅刷新 VPN_FORWARD）")
        return True

    def delete(self, rule_id: str) -> bool:
        """删除指定规则并重建 iptables。

        参数:
            rule_id: 要删除的规则 ID

        返回:
            是否成功删除

        异常:
            ValueError: 规则不存在时抛出
        """
        rid = str(rule_id)
        if rid.startswith(CE_PEER_RULE_PREFIX):
            from app.services.peer_instance.service import PeerService

            pid = rid[len(CE_PEER_RULE_PREFIX) :]
            PeerService().update(pid, {"lan_cidrs": []}, refresh_center_iptables=False)
            self._refresh_vpn_forward_only()
            self.audit.log(
                action="delete_firewall_rule",
                target_type="firewall_rule",
                target_id=rid,
                detail=f"清空对端 LAN 以移除中心放行（对端实例 {pid}）",
                result="success",
            )
            logger.info("已通过对端 %s 清空 LAN 删除防火墙列表项", pid)
            return True

        # 遍历所有 owner 文件查找目标规则
        owner_id, rules = self._find_rule_owner(rule_id)
        if owner_id is None:
            raise ValueError(f"规则 {rule_id} 不存在")

        # 移除目标规则
        updated_rules = [r for r in rules if r["id"] != rule_id]
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
            target_id=rule_id,
            detail=f"删除防火墙规则 {rule_id}",
            result="success",
        )

        logger.info("防火墙规则 %s 已删除", rule_id)
        return True

    def set_enabled(self, rule_id: str, enabled: bool) -> bool:
        """设置规则启用/停用状态并仅刷新 ``VPN_FORWARD``。"""
        rid = str(rule_id)
        if rid.startswith(CE_PEER_RULE_PREFIX):
            from app.services.peer_instance.service import PeerService

            pid = rid[len(CE_PEER_RULE_PREFIX) :]
            PeerService().patch_center_forward_enabled(pid, enabled)
            status_text = "启用" if enabled else "停用"
            self.audit.log(
                action="toggle_firewall_rule",
                target_type="firewall_rule",
                target_id=rid,
                detail=f"中心侧对端 LAN 放行已{status_text}（对端 {pid}）",
                result="success",
            )
            logger.info("中心侧对端 LAN 放行已%s peer=%s", status_text, pid[:8] if len(pid) > 8 else pid)
            return True

        owner_id, rules = self._find_rule_owner(rule_id)
        if owner_id is None:
            raise ValueError(f"规则 {rule_id} 不存在")

        for r in rules:
            if r["id"] == rule_id:
                r["enabled"] = enabled
                r["updated_at"] = datetime.now().isoformat()
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
            target_id=rule_id,
            detail=f"防火墙规则 {rule_id} 已{status_text}",
            result="success",
        )
        logger.info("防火墙规则 %s 已%s", rule_id, status_text)
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
        """从 JSON 恢复规则集（覆盖现有规则）。

        参数:
            json_str: backup() 导出的 JSON 字符串

        返回:
            操作是否成功
        """
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error("恢复规则 JSON 解析失败: %s", e)
            return False

        if "rules_by_owner" not in data:
            logger.error("恢复数据中缺少 rules_by_owner 键")
            return False
        rules_by_owner: dict = data.get("rules_by_owner") or {}
        peers_cf = data.get("peers_center_forward")
        if not rules_by_owner and not (isinstance(peers_cf, list) and len(peers_cf) > 0):
            logger.error("恢复数据为空：rules_by_owner 无内容且未包含对端中心策略")
            return False

        oids = list(
            dict.fromkeys(
                str(oid) for oid, rlist in rules_by_owner.items() if isinstance(rlist, list)
            )
        )
        had_main = {oid: self.store.firewall_owner_exists(oid) for oid in oids}
        for oid in oids:
            if had_main[oid]:
                self._backup_center_rules_before_mutation(oid)

        # 逐 owner 写入
        for owner_id, rules in rules_by_owner.items():
            if not isinstance(rules, list):
                logger.warning("owner %s 的 rules 非列表，已跳过", owner_id)
                continue
            validated_rules: list[dict] = []
            for r in rules:
                if not isinstance(r, dict):
                    continue
                try:
                    rule = FirewallRule(**r)
                    validated_rules.append(rule.model_dump())
                except Exception as e:
                    logger.warning("跳过无效规则: %s, 原因: %s", r.get("id", "?"), e)
                    continue

            self._save_rules(owner_id, validated_rules)

        if isinstance(peers_cf, list) and peers_cf:
            from app.services.peer_instance.service import PeerService

            ps = PeerService()
            for item in peers_cf:
                if not isinstance(item, dict):
                    continue
                pid = str(item.get("id") or "").strip()
                if not pid:
                    continue
                cur = ps.get(pid)
                if not cur:
                    logger.warning("备份中 peers_center_forward 对端已不存在，跳过: %s", pid)
                    continue
                patch: dict = {
                    "lan_cidrs": [
                        str(c).strip() for c in (item.get("lan_cidrs") or []) if str(c).strip()
                    ],
                    "center_forward_priority": int(
                        item.get("center_forward_priority")
                        or cur.get("center_forward_priority")
                        or 500_000
                    ),
                    "center_forward_enabled": bool(
                        item.get("center_forward_enabled")
                        if "center_forward_enabled" in item
                        else cur.get("center_forward_enabled", True)
                    ),
                }
                for k in (
                    "center_forward_dest_ip",
                    "center_forward_dest_port",
                    "center_forward_protocol",
                    "center_forward_rule_description",
                ):
                    if k in item and item.get(k) is not None:
                        patch[k] = str(item.get(k) or "").strip()
                if "center_forward_protocol" in patch and not patch.get("center_forward_protocol"):
                    patch["center_forward_protocol"] = "all"
                nm = str(item.get("name") or "").strip()
                if nm:
                    patch["name"] = nm
                try:
                    ps.update(pid, patch, refresh_center_iptables=False)
                except Exception as exc:
                    logger.error("从备份恢复对端 %s 中心策略失败: %s", pid, exc)
                    return False

        try:
            self._refresh_vpn_forward_only()
        except Exception as exc:
            logger.error("从备份恢复规则后刷新 VPN_FORWARD 失败: %s", exc)
            self._rollback_center_json_owners_after_rebuild_fail(oids, had_main)
            if isinstance(peers_cf, list) and peers_cf:
                logger.warning(
                    "从备份恢复时 iptables 重建失败，已回滚中心规则 SQLite 快照；对端中心策略的 Peer 已写入，未自动回滚"
                )
            return False

        for oid in oids:
            if had_main[oid]:
                self._clear_center_prepush_backup(oid)

        n_peers = len(peers_cf) if isinstance(peers_cf, list) else 0
        self.audit.log(
            action="restore_firewall_rules",
            target_type="firewall",
            target_id="all",
            detail=f"从备份恢复：{len(rules_by_owner)} 个 owner，对端中心策略 {n_peers} 条",
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

    def _rebuild_all_rules(self) -> None:
        """合并全部 owner 的已启用规则、对端中心放行，一次性重建 iptables / ipset。"""
        merged_raw = self.store.list_firewall_rules()
        enabled_rules = [
            FirewallRule(**r) for r in merged_raw if r.get("enabled", True)
        ]
        enabled_rules.sort(key=lambda x: x.priority)
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
        enabled_rules = [
            FirewallRule(**r) for r in merged_raw if r.get("enabled", True)
        ]
        enabled_rules.sort(key=lambda x: x.priority)
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
