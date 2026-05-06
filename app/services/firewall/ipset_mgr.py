"""ipset 集合管理服务

通过 subprocess 调用 ipset 命令管理 hash:net 类型的集合。
每个组/用户对应一个 ipset 集合，集合名称由上层服务决定。
"""

import logging
import subprocess

logger = logging.getLogger(__name__)


class IpsetManager:
    """ipset 集合的创建、更新和删除。"""

    def create_set(self, name: str, subnet: str) -> bool:
        """创建 ipset 集合（hash:net 类型），并添加子网。

        如果���名集合已存在则跳过创建，直接添加子网条目。

        参数:
            name:   集合名称，建议格式 vpn_<owner_type>_<owner_id>
            subnet: 子网 CIDR，如 10.8.1.0/24

        返回:
            操作是否成功
        """
        try:
            # 创建集合（已存在时 -exist 标志使命令不报错）
            self._run(["ipset", "create", name, "hash:net", "-exist"])
            # 添加子网条目
            self._run(["ipset", "add", name, subnet, "-exist"])
            logger.info("ipset 集合 %s 已创建，子网 %s 已添加", name, subnet)
            return True
        except subprocess.CalledProcessError as e:
            logger.error("创建 ipset 集合 %s 失败: %s", name, e.stderr)
            return False

    def update_set(self, name: str, subnet: str) -> bool:
        """更新 ipset 集合（先清空再添加新子网）。

        用于组/用户子网变更时更新对应集合内容。

        参数:
            name:   集合名称
            subnet: 新的子网 CIDR

        返回:
            操作是否成功
        """
        try:
            # 清空集合内现有条目
            self._run(["ipset", "flush", name])
            # 添加新子网
            self._run(["ipset", "add", name, subnet])
            logger.info("ipset 集合 %s 已更新，新子网 %s", name, subnet)
            return True
        except subprocess.CalledProcessError as e:
            logger.error("更新 ipset 集合 %s 失败: %s", name, e.stderr)
            return False

    def delete_set(self, name: str) -> bool:
        """删除 ipset 集合。

        删除前需确保没有 iptables 规则引用该集合，否则命令会失败。

        参数:
            name: 集合名称

        返回:
            操作是否成功
        """
        try:
            self._run(["ipset", "destroy", name])
            logger.info("ipset 集合 %s 已删除", name)
            return True
        except subprocess.CalledProcessError as e:
            # 集合不存在时也视为成功
            if "does not exist" in (e.stderr or ""):
                logger.warning("ipset 集合 %s 不存在，跳过删除", name)
                return True
            logger.error("删除 ipset 集合 %s 失败: %s", name, e.stderr)
            return False

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        """执行 ipset 命令（不使用 shell=True，防止命令注入）。

        参数:
            args: 完整的命令参数列表，如 ["ipset", "create", "myset", "hash:net"]

        返回:
            CompletedProcess 实例

        异常:
            subprocess.CalledProcessError: 命令返回非零退出码时抛出
        """
        logger.debug("执行命令: %s", " ".join(args))
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
