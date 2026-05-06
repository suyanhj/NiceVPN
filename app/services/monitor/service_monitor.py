"""服务状态监控与自动重启服务

定期检查 OpenVPN 各实例运行状态，采集在线用户数和流量指标。
检测到实例异常时自动执行重启并写入审计日志。
"""

import logging
from dataclasses import dataclass

from app.services.openvpn.instance import get_status, is_instance_active, restart_instance
from app.utils.audit_log import AuditLogger

logger = logging.getLogger(__name__)


@dataclass
class InstanceStatus:
    """单个 OpenVPN 实例的运行状态快照"""

    name: str
    active: bool
    client_count: int
    bytes_received: int
    bytes_sent: int


class ServiceMonitor:
    """服务监控器 — 提供实例巡检与自动恢复能力"""

    def __init__(self):
        self._audit = AuditLogger()

    def check_all_instances(self, instance_names: list[str]) -> list[InstanceStatus]:
        """检查所有实例状态：读取 status 文件获取在线用户数和流量。

        Args:
            instance_names: 需要检查的实例名称列表

        Returns:
            每个实例的 InstanceStatus 快照列表
        """
        results: list[InstanceStatus] = []

        for name in instance_names:
            active = is_instance_active(name)

            # 即使实例不活跃也尝试读取最后一次 status 文件
            status = get_status(name)
            client_count = len(status["clients"])
            bytes_received = status["total_bytes_received"]
            bytes_sent = status["total_bytes_sent"]

            results.append(InstanceStatus(
                name=name,
                active=active,
                client_count=client_count,
                bytes_received=bytes_received,
                bytes_sent=bytes_sent,
            ))

            if not active:
                logger.warning("实例 %s 未在运行", name)

        return results

    def auto_restart_if_down(self, instance_name: str) -> bool:
        """检测到实例异常时执行重启并写入审计日志。

        Args:
            instance_name: 实例名称

        Returns:
            True — 实例本身正常或重启成功
            False — 重启失败
        """
        if is_instance_active(instance_name):
            return True

        logger.info("实例 %s 已停止，尝试自动重启...", instance_name)

        success = restart_instance(instance_name)

        if success:
            logger.info("实例 %s 自动重启成功", instance_name)
            self._audit.log(
                action="auto_restart",
                target_type="openvpn_instance",
                target_id=instance_name,
                detail=f"检测到实例 {instance_name} 异常停止，已自动重启",
                result="success",
            )
        else:
            logger.error("实例 %s 自动重启失败", instance_name)
            self._audit.log(
                action="auto_restart",
                target_type="openvpn_instance",
                target_id=instance_name,
                detail=f"检测到实例 {instance_name} 异常停止，自动重启失败",
                result="failure",
                error_message="systemctl restart 返回非零状态",
            )

        return success
