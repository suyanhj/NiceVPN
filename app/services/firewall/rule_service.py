# -*- coding: utf-8 -*-
"""防火墙规则业务逻辑服务

提供规则的 CRUD、拖拽排序、备份恢复等业务操作。
规则以 JSON 文件持久化，按 owner（组/用户）分文件存储在 FIREWALL_DIR 下。

严格语义：在覆盖某 owner 的 ``*.json`` 后，若 :meth:`_rebuild_all_rules` **任意**失败
（``RuntimeError`` 或其它异常），用当次改库前的 ``<owner>.prepush.bak`` 写回主文件，或
删除本操作新建之文件，与对端 ``remote_chain_cache`` 的写回前快照语义对齐。
"""

import json
import logging
import os
import shutil
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.core.constants import FIREWALL_DIR
from app.models.firewall import FirewallRule
from app.services.firewall.iptables_mgr import IptablesManager
from app.utils.audit_log import AuditLogger
from app.utils.cidr import validate_cidr, validate_iptables_addr_or_cidr
from app.utils.file_lock import read_json, write_json_atomic

logger = logging.getLogger(__name__)

CE_PEER_RULE_PREFIX = "ce-peer-"


class FirewallRuleService:
    """防火墙规则业务逻辑层。

    规则按 owner_id 分文件存储：FIREWALL_DIR/<owner_id>.json
    每个文件内容为规则字典列表。
    """

    def __init__(self):
        self.iptables_mgr = IptablesManager()
        self.audit = AuditLogger()
        # 确保存储目录存在
        FIREWALL_DIR.mkdir(parents=True, exist_ok=True)

    def _center_rules_prepush_bak_path(self, owner_id: str) -> Path:
        p = self._get_rules_path(owner_id)
        return p.parent / f"{p.stem}.prepush.bak"

    def _backup_center_rules_before_mutation(self, owner_id: str) -> None:
        """主 JSON 已存在时复制为 prepush.bak，供 iptables 重建失败时回滚。"""
        main = self._get_rules_path(owner_id)
        if not main.is_file():
            return
        bak = self._center_rules_prepush_bak_path(owner_id)
        try:
            shutil.copy2(main, bak)
            logger.info("中心规则已生成写前备份 owner=%s", owner_id)
        except OSError as exc:
            logger.error("中心规则写前备份失败 owner=%s: %s", owner_id, exc)
            raise RuntimeError(f"无法备份中心规则文件，已中止写入: {exc}") from exc

    def _clear_center_prepush_backup(self, owner_id: str) -> None:
        p = self._center_rules_prepush_bak_path(owner_id)
        try:
            if p.is_file():
                p.unlink()
        except OSError as exc:
            logger.warning("删除中心规则 prepush 备份失败 owner=%s: %s", owner_id, exc)

    def _rollback_center_json_after_rebuild_fail(
        self, owner_id: str, had_main_before: bool
    ) -> None:
        """iptables 重建失败时恢复该 owner 主文件，避免库与改前可对照状态分离。"""
        main = self._get_rules_path(owner_id)
        bak = self._center_rules_prepush_bak_path(owner_id)
        if bak.is_file():
            try:
                shutil.copy2(bak, main)
                bak.unlink()
            except OSError as exc:
                logger.error("中心规则从 prepush 回滚主文件失败 owner=%s: %s", owner_id, exc)
                raise RuntimeError(f"回滚中心规则文件失败: {exc}") from exc
            logger.info("中心规则重建失败，已从 prepush 恢复 owner=%s", owner_id)
            return
        if had_main_before:
            logger.error("中心规则回滚缺少 prepush 备份 owner=%s", owner_id)
            raise RuntimeError(f"回滚中心规则需要 prepush 备份但缺失: {owner_id}")
        if main.is_file():
            try:
                main.unlink()
            except OSError as exc:
                logger.error("中心规则回滚时删除误建主文件失败 owner=%s: %s", owner_id, exc)
                raise RuntimeError(f"回滚新规则文件失败: {exc}") from exc
            logger.info("中心规则重建失败，已删除本操作新建之 owner 文件 owner=%s", owner_id)

    def _rollback_center_json_owners_after_rebuild_fail(
        self, owner_ids: list[str], had_main: dict[str, bool]
    ) -> None:
        for oid in owner_ids:
            self._rollback_center_json_after_rebuild_fail(oid, had_main[oid])

    @staticmethod
    def _validate_json_rule_addr_fields(rule: FirewallRule) -> None:
        """落库前校验目标/源 IP 字串，与对端链 ``-s``/``-d`` 及 :func:`validate_iptables_addr_or_cidr` 同语义。"""
        d = (rule.dest_ip or "").strip()
        if d:
            for p in d.split(","):
                t = p.strip()
                if t:
                    validate_iptables_addr_or_cidr(t)
        ips = rule.source_ips
        if ips:
            for ip in ips:
                t = str(ip).strip()
                if t:
                    validate_iptables_addr_or_cidr(t)

    # ---- 公开接口 ----

    def create(self, rule_data: dict) -> dict:
        """创建防火墙规则，执行三重校验后持久化并仅刷新 ``VPN_FORWARD``。

        三重校验：
        1. 端口格式校验（由 Pydantic 模型 field_validator 完成）
        2. 源子网 CIDR 格式校验；目标/源 IP 字串（与对端链同语义，见 :meth:`_validate_json_rule_addr_fields`）
        3. 同一 owner 下优先级唯一性校验

        参数:
            rule_data: 规则数据字典，字段参见 FirewallRule 模型

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
        had_main = self._get_rules_path(owner_id).is_file()
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
        had_main = self._get_rules_path(owner_id).is_file()
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

        had_main = self._get_rules_path(owner_id).is_file()
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
        """全局排序：JSON 规则与对端 ``center_forward_priority`` 同步为 (n*10)，仅刷新 ``VPN_FORWARD``。"""
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
        had_main = {oid: self._get_rules_path(oid).is_file() for oid in oids}
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
        had_main = self._get_rules_path(owner_id).is_file()
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
        had_main = self._get_rules_path(owner_id).is_file()
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

        除 ``FIREWALL_DIR`` 下按归属分文件的 JSON 规则外，包含对端在中心侧
        ``VPN_FORWARD`` 合并所需的 LAN / 优先级 / 启停，与界面「全部归属」中对端
        卡片一致，避免仅配置对端放行时导出看似「无规则」。

        返回:
            包含 ``rules_by_owner`` 与 ``peers_center_forward`` 的 JSON 字符串
        """
        all_rules: dict[str, list[dict]] = {}
        for rule_file in FIREWALL_DIR.glob("*.json"):
            owner_id = rule_file.stem
            rules = self._load_rules(owner_id)
            all_rules[owner_id] = rules

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
        had_main = {oid: self._get_rules_path(oid).is_file() for oid in oids}
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
                    "从备份恢复时 iptables 重建失败，已回滚 rules JSON；对端中心策略的 Peer 已写入，未自动回滚"
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
        for rule_file in sorted(FIREWALL_DIR.glob("*.json")):
            oid = rule_file.stem
            for r in self._load_rules(oid):
                row = dict(r)
                row["_owner_id"] = oid
                merged.append(row)
        return sorted(merged, key=lambda x: (x.get("priority", 0), x.get("_owner_id", "")))

    def list_unified_flat(self) -> list[dict]:
        """防火墙首页「全部归属」：JSON 规则与**中心侧**对端放行合并列表。

        每条对端卡片只代表本机 ``VPN_FORWARD``（允许对端内网源）；对端机器上放行中心源须 SSH 下发，
        见 ``remote_peer_iptables``，不在此列表展示。
        """
        from app.services.peer_instance.service import PeerService

        out: list[dict] = []
        for r in self.list_all_flat():
            row = dict(r)
            row["_row_kind"] = "json"
            out.append(row)
        for p in PeerService().list_all():
            pid = str(p.get("id") or "").strip()
            if not pid:
                continue
            lan = [str(c).strip() for c in (p.get("lan_cidrs") or []) if str(c).strip()]
            if not lan:
                continue
            pri = int(p.get("center_forward_priority", 500_000))
            cen_en = bool(p.get("center_forward_enabled", True))
            # 与 ``IptablesManager`` 中默认 tag 同文，供未填自定义说明时第二行与 iptables 一致
            c_raw = f"{IptablesManager.CENTER_PEER_COMMENT_TAG} peer={pid}"
            c_peer = c_raw[:256].replace('"', "'")
            d_ip = str(p.get("center_forward_dest_ip") or "").strip() or None
            d_port = str(p.get("center_forward_dest_port") or "").strip() or None
            proto = str(p.get("center_forward_protocol") or "all").strip() or "all"
            udesc = str(p.get("center_forward_rule_description") or "").strip()
            out.append(
                {
                    "id": f"{CE_PEER_RULE_PREFIX}{pid}",
                    "_row_kind": "center_peer",
                    "_peer_id": pid,
                    "owner_type": "peer",
                    "owner_id": pid,
                    "priority": pri,
                    "description": udesc,
                    "_center_tag_line": c_peer,
                    "enabled": cen_en,
                    "protocol": proto,
                    "action": "accept",
                    "deployment_target": "center",
                    "source_subnet": ",".join(lan),
                    "source_ips": None,
                    "dest_ip": d_ip,
                    "dest_port": d_port,
                    "instance": "server",
                    "_owner_id": pid,
                }
            )
        return sorted(out, key=lambda x: (x.get("priority", 0), str(x.get("id", ""))))

    # ---- 内部方法 ----

    def _get_rules_path(self, owner_id: str) -> Path:
        """获取指定 owner 的规则文件路径。"""
        return FIREWALL_DIR / f"{owner_id}.json"

    def _load_rules(self, owner_id: str) -> list[dict]:
        """读取指定 owner 的规则列表。"""
        path = self._get_rules_path(owner_id)
        data = read_json(path)
        if not data:
            return []
        if isinstance(data, list):
            return data
        return data.get("rules", [])

    def _save_rules(self, owner_id: str, rules: list[dict]) -> None:
        """保存规则列表到文件。"""
        path = self._get_rules_path(owner_id)
        write_json_atomic(path, {"rules": rules})

    def _find_rule_owner(self, rule_id: str) -> tuple[Optional[str], list[dict]]:
        """遍历所有规则文件，查找包含指定 rule_id 的 owner。

        返回:
            (owner_id, rules) 元组。未找到时返回 (None, [])。
        """
        for rule_file in FIREWALL_DIR.glob("*.json"):
            owner_id = rule_file.stem
            rules = self._load_rules(owner_id)
            if any(r["id"] == rule_id for r in rules):
                return owner_id, rules
        return None, []

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
        merged_raw: list[dict] = []
        for rule_file in FIREWALL_DIR.glob("*.json"):
            owner_id = rule_file.stem
            merged_raw.extend(self._load_rules(owner_id))
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
        merged_raw: list[dict] = []
        for rule_file in FIREWALL_DIR.glob("*.json"):
            owner_id = rule_file.stem
            merged_raw.extend(self._load_rules(owner_id))
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
