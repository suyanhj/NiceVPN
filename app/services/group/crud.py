"""用户组 CRUD 服务

提供组的增删改查、批量操作、启用/禁用等全部管理功能。
每个组以独立 JSON 文件存储在 data/groups/ 目录下，文件名为 {group_id}.json。
"""

import logging
import uuid
from datetime import datetime, timezone

from app.core.config import load_config, save_config
from app.core.constants import GROUPS_DIR, MGMT_PORT_START, OPENVPN_DEFAULT_MAX_CLIENTS
from app.models.group import Group
from app.services.group.subnet import check_subnet_conflict
from app.utils.audit_log import AuditLogger
from app.utils.file_lock import read_json, write_json_atomic

logger = logging.getLogger(__name__)


class GroupService:
    """用户组全生命周期管理服务"""

    def __init__(self):
        self.audit = AuditLogger()

    # ---- 创建 ----

    def create(self, name: str, subnet: str) -> dict:
        """创建组：校验名称唯一性，调用子网冲突检测，通过后保存。

        规则：
        - 如果当前没有任何组，新建的第一个组成为根组（默认组）
        - 后续所有组的子网必须是根组子网的子网

        参数:
            name:   组名称（不可重复）
            subnet: 组子网 CIDR

        返回:
            新建组的完整字典

        异常:
            ValueError: 名称重复或子网冲突
        """
        # 校验名称唯一性
        if self._name_exists(name):
            raise ValueError(f"组名已存在: {name}")

        existing = self.list_all()

        # 如果已有组，后续组的子网必须是根组（第一个组）的子网
        if existing:
            root_group = existing[0]
            root_subnet = root_group.get("subnet", "")
            if root_subnet:
                from app.utils.cidr import is_subnet_of, validate_cidr
                if not validate_cidr(subnet):
                    raise ValueError(f"子网格式不合法: {subnet}")
                if not is_subnet_of(subnet, root_subnet):
                    raise ValueError(
                        f"子网 {subnet} 必须是根组「{root_group['name']}」"
                        f"子网 {root_subnet} 的子网"
                    )

        # 子网冲突检测
        config = load_config()
        global_subnet = config.global_subnet or ""
        conflicts = check_subnet_conflict(subnet, global_subnet, existing)
        if conflicts:
            raise ValueError("子网冲突: " + "; ".join(conflicts))

        # 构造组对象
        now = datetime.now(timezone.utc).isoformat()
        group = Group(
            id=str(uuid.uuid4()),
            name=name,
            subnet=subnet,
            status="active",
            user_count=0,
            created_at=now,
            updated_at=now,
        )
        data = group.model_dump()

        # 持久化
        GROUPS_DIR.mkdir(parents=True, exist_ok=True)
        write_json_atomic(GROUPS_DIR / f"{data['id']}.json", data)

        self.audit.log(
            "create_group", "group", data["id"],
            f"创建组「{name}」, 子网: {subnet}",
            "success",
        )
        return data

    def bulk_create(self, groups: list[dict]) -> list[dict]:
        """批量创建组：逐一检测冲突后依次创建。

        参数:
            groups: 包含 name / subnet 字段的字典列表

        返回:
            成功创建的组列表（含完整字段）；
            创建失败的条目会被跳过并记录审计日志。
        """
        results: list[dict] = []
        for item in groups:
            name = item.get("name", "")
            subnet = item.get("subnet", "")
            if not name or not subnet:
                continue
            try:
                created = self.create(name, subnet)
                results.append(created)
            except ValueError as e:
                self.audit.log(
                    "create_group", "group", name,
                    f"批量创建失败: {e}",
                    "failure", str(e),
                )
        return results

    # ---- 修改 ----

    def update_subnet(self, group_id: str, new_subnet: str) -> dict:
        """修改组子网。

        通用前提：组内无用户。
        根组（全局组）额外前提：不存在其他子组。
        根组修改后自动同步：config.global_subnet + 默认防火墙规则的 source_subnet。

        参数:
            group_id:   组 ID
            new_subnet: 新子网 CIDR

        返回:
            更新后的组字典

        异常:
            ValueError: 组不存在、有活跃用户、有子组或子网冲突
        """
        data = self._load_group_or_raise(group_id)

        if data.get("user_count", 0) > 0:
            raise ValueError(f"组内有 {data['user_count']} 个用户，无法修改子网")

        config = load_config()
        global_subnet = config.global_subnet or ""
        existing = self.list_all()

        # 判断是否在编辑根组
        is_root = len(existing) > 0 and existing[0]["id"] == group_id
        if is_root:
            # 根组修改前提：没有其他子组
            if len(existing) > 1:
                raise ValueError("根组下存在子组，修改根组子网前请先删除所有子组")
            # 根组修改不校验"是否在全局子网范围内"，因为它本身就是全局子网
            from app.utils.cidr import validate_cidr
            if not validate_cidr(new_subnet):
                raise ValueError(f"子网格式不合法: {new_subnet}")
        else:
            conflicts = check_subnet_conflict(
                new_subnet, global_subnet, existing, exclude_group_id=group_id
            )
            if conflicts:
                raise ValueError("子网冲突: " + "; ".join(conflicts))

        old_subnet = data["subnet"]
        data["subnet"] = new_subnet
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_json_atomic(GROUPS_DIR / f"{group_id}.json", data)

        # 根组修改后同步全局配置和默认防火墙规则
        if is_root:
            self._sync_global_subnet(group_id, old_subnet, new_subnet)

        self.audit.log(
            "update_group_subnet", "group", group_id,
            f"子网从 {old_subnet} 变更为 {new_subnet}",
            "success",
        )
        return data

    def _sync_global_subnet(self, root_group_id: str, old_subnet: str, new_subnet: str):
        """根组子网变更后，同步全局配置、实例元数据、server.conf 和默认防火墙规则。"""
        from ipaddress import IPv4Network
        from app.models.config import SystemConfig

        config = load_config()
        merged = SystemConfig(**config.to_dict())
        merged.global_subnet = new_subnet

        # 同步实例元数据中的 subnet
        for inst_name, inst_meta in (merged.instances or {}).items():
            if isinstance(inst_meta, dict) and inst_meta.get("subnet") == old_subnet:
                inst_meta["subnet"] = new_subnet
                logger.info("实例 %s 元数据 subnet 已同步: %s -> %s", inst_name, old_subnet, new_subnet)

        save_config(merged)
        logger.info("全局子网已同步: %s -> %s", old_subnet, new_subnet)

        # 重新生成 server.conf
        try:
            from app.services.openvpn.instance import write_server_conf
            network = IPv4Network(new_subnet, strict=False)
            # 重新加载最新配置以获取所有字段
            fresh_config = load_config()
            names_sorted = sorted((fresh_config.get("instances") or {}).keys())
            for idx, inst_name in enumerate(names_sorted):
                ovpn_base = str(fresh_config.get("openvpn_conf_dir") or "/etc/openvpn")
                conf_config = {
                    "server_network": str(network.network_address),
                    "server_mask": str(network.netmask),
                    "port": fresh_config.get("port", 1194),
                    "proto": fresh_config.get("proto", "udp"),
                    "pki_dir": str(fresh_config.get("pki_dir") or ""),
                    "openvpn_conf_dir": ovpn_base,
                    "push_lan_routes": list(fresh_config.get("push_lan_routes") or []),
                    "mgmt_port": MGMT_PORT_START + idx,
                    "max_clients": int(fresh_config.get("max_clients") or OPENVPN_DEFAULT_MAX_CLIENTS),
                }
                write_server_conf(inst_name, conf_config, conf_dir=ovpn_base)
                logger.info("实例 %s server.conf 已重新生成，subnet: %s", inst_name, new_subnet)
        except Exception as exc:
            logger.error("重新生成 server.conf 失败: %s", exc)

        # 同步默认防火墙规则的 source_subnet
        try:
            from app.services.firewall.rule_service import FirewallRuleService
            fw = FirewallRuleService()
            rules = fw.list_by_owner(root_group_id)
            for rule in rules:
                if rule.get("description") == "初始化默认放行" and rule.get("source_subnet") == old_subnet:
                    rule["source_subnet"] = new_subnet
                    rule["updated_at"] = datetime.now(timezone.utc).isoformat()
                    fw._save_rules(root_group_id, rules)
                    fw.refresh_vpn_forward_only()
                    logger.info("默认防火墙规则 source_subnet 已同步: %s -> %s", old_subnet, new_subnet)
                    break
        except Exception as exc:
            logger.error("同步默认防火墙规则失败: %s", exc)

    # ---- 删除 ----

    def delete(self, group_id: str) -> bool:
        """删除组：仅在组内无用户时允许删除。

        根组（初始化创建的全局组）不允许删除，只允许修改子网。

        参数:
            group_id: 组 ID

        返回:
            True 表示删除成功

        异常:
            ValueError: 组不存在、有活跃用户或为根组
        """
        data = self._load_group_or_raise(group_id)

        if data.get("user_count", 0) > 0:
            raise ValueError(f"组内有 {data['user_count']} 个用户，无法删除")

        # 根组（第一个组）永远不允许删除，只允许修改子网
        all_groups = self.list_all()
        if all_groups and all_groups[0]["id"] == group_id:
            raise ValueError(
                f"根组「{data.get('name', '')}」是全局组，不允许删除，只允许修改子网"
            )

        group_file = GROUPS_DIR / f"{group_id}.json"
        group_file.unlink(missing_ok=True)

        self.audit.log(
            "delete_group", "group", group_id,
            f"删除组「{data.get('name', '')}」",
            "success",
        )
        return True

    def bulk_delete(self, ids: list[str]) -> list[dict]:
        """批量删除组。

        返回:
            每项操作的结果列表，格式 {"id": ..., "success": bool, "error": str|None}
        """
        results: list[dict] = []
        for gid in ids:
            try:
                self.delete(gid)
                results.append({"id": gid, "success": True, "error": None})
            except ValueError as e:
                results.append({"id": gid, "success": False, "error": str(e)})
        return results

    # ---- 启用 / 禁用 ----

    def enable(self, group_id: str) -> dict:
        """启用组"""
        return self._set_status(group_id, "active")

    def disable(self, group_id: str) -> dict:
        """禁用组"""
        return self._set_status(group_id, "disabled")

    def bulk_enable(self, ids: list[str]) -> list[dict]:
        """批量启用组。

        返回:
            每项操作的结果列表
        """
        return [self._safe_set_status(gid, "active") for gid in ids]

    def bulk_disable(self, ids: list[str]) -> list[dict]:
        """批量禁用组。

        返回:
            每项操作的结果列表
        """
        return [self._safe_set_status(gid, "disabled") for gid in ids]

    # ---- 查询 ----

    def list_all(self) -> list[dict]:
        """列出所有组"""
        groups: list[dict] = []
        if not GROUPS_DIR.exists():
            return groups
        for f in GROUPS_DIR.glob("*.json"):
            data = read_json(f)
            if data:
                groups.append(data)
        # 按创建时间排序
        groups.sort(key=lambda g: g.get("created_at", ""))
        return groups

    def get(self, group_id: str) -> dict | None:
        """获取单个组"""
        group_file = GROUPS_DIR / f"{group_id}.json"
        if not group_file.exists():
            return None
        return read_json(group_file)

    # ---- 内部辅助方法 ----

    def _load_group_or_raise(self, group_id: str) -> dict:
        """加载组数据，不存在时抛出异常"""
        data = self.get(group_id)
        if not data:
            raise ValueError(f"组不存在: {group_id}")
        return data

    def _name_exists(self, name: str) -> bool:
        """检查组名是否已存在"""
        for group in self.list_all():
            if group.get("name") == name:
                return True
        return False

    def _set_status(self, group_id: str, status: str) -> dict:
        """设置组状态"""
        data = self._load_group_or_raise(group_id)
        old_status = data.get("status")
        data["status"] = status
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_json_atomic(GROUPS_DIR / f"{group_id}.json", data)

        self.audit.log(
            f"{'enable' if status == 'active' else 'disable'}_group",
            "group", group_id,
            f"组状态从 {old_status} 变更为 {status}",
            "success",
        )
        return data

    def _safe_set_status(self, group_id: str, status: str) -> dict:
        """安全设置组状态，失败时返回错误信息而非抛异常"""
        try:
            data = self._set_status(group_id, status)
            return {"id": group_id, "success": True, "error": None, "data": data}
        except ValueError as e:
            return {"id": group_id, "success": False, "error": str(e)}
