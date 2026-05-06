# -*- coding: utf-8 -*-
"""定时任务注册模块 — 后台线程运行 schedule 调度器"""
import logging
import os
import threading
import time

import schedule

logger = logging.getLogger(__name__)


def start_scheduler():
    """启动后台定时任务线程。

    当前注册的任务：
    - 证书到期检查：每小时执行一次，更新 data/alerts.json
    - 日志过期清理：每天 03:30 执行（OpenVPN 日志、审计 JSONL、遗留轮转文件）
    - 已初始化且非 Windows：同步 device_bind_mode 文件、从 data/firewall/*.json 重建 iptables/ipset（与重启前逻辑一致；重建成功后随 `FirewallRuleService` 同步对端 `VPN_PEER`；并尝试将 mesh `push route` 写入各用户 CCD；sysctl 由 sysctl.d 持久化，不在此重复写入）
    """
    from app.core.config import load_config
    from app.services.cert.cert_service import CertService
    from app.utils.log_cleanup import cleanup_expired_logs

    cert_svc = CertService()

    # 证书到期检查：每小时执行
    schedule.every(1).hours.do(cert_svc.check_and_update_alerts)

    # 日志保留：每日一次（默认保留 LOG_RETENTION_DAYS 天）
    schedule.every().day.at("03:30").do(cleanup_expired_logs)

    # 启动时立即执行一次，确保告警数据可用
    try:
        cert_svc.check_and_update_alerts()
    except Exception as e:
        logger.warning("启动时执行证书到期检查失败: %s", e)

    try:
        cleanup_expired_logs()
    except Exception as e:
        logger.warning("启动时执行日志清理失败: %s", e)

    # 内核 iptables/ipset 重启后清空；业务规则在磁盘，需启动时重建才能与界面一致
    if os.name != "nt":
        try:
            cfg = load_config()
            if cfg.get("initialized"):
                from app.services.user.device_bind_policy import sync_device_bind_mode_file
                try:
                    sync_device_bind_mode_file(str(cfg.get("device_bind_mode") or "weak_fingerprint"))
                except OSError as exc:
                    logger.warning("启动时同步 device_bind_mode 失败: %s", exc)

                from app.services.firewall.rule_service import FirewallRuleService
                from app.services.openvpn.instance import regenerate_all_server_confs

                FirewallRuleService().rebuild_iptables()
                logger.info("启动时已从磁盘规则重建 iptables/ipset（含 FORWARD 钩子与 MASQUERADE）")
                try:
                    from app.services.peer_instance.service import PeerService

                    PeerService().sync_all_mesh_push_routes_in_ccd()
                except Exception as exc:
                    logger.error("启动时同步 mesh push CCD（对端内网路由）失败: %s", exc)
                try:
                    # 有实质变更才写盘，详见 instance 内日志
                    regenerate_all_server_confs()
                except Exception as exc:
                    logger.error("启动时重写 server.conf 失败: %s", exc)
        except Exception as e:
            logger.error("启动时重建防火墙失败（请检查本机 iptables/ipset 权限）: %s", e)

    def _run():
        """后台线程主循环：持续执行待调度任务"""
        logger.info("定时任务调度器已启动")
        while True:
            try:
                schedule.run_pending()
            except Exception as e:
                logger.error("定时任务执行异常: %s", e)
            time.sleep(1)

    t = threading.Thread(target=_run, daemon=True, name="scheduler")
    t.start()
    return t
