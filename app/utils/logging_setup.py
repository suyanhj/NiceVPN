# -*- coding: utf-8 -*-
"""项目运行日志配置工具。

与 OpenVPN 相关文件日志的布局见 ``app.core.constants``：
``LOGS_DIR``（data/logs）下含 app.log、openvpn-install.log、peer-remote.log（按天轮转，默认保留 LOG_RETENTION_DAYS 天）；
设备绑定日志与守护进程 log-append/status 在 ``/etc/openvpn/log/`` 下，由定时任务按保留期清理。
审计 JSONL 在 ``AUDIT_DIR``，与运行日志分离。
"""
import logging
from logging.handlers import TimedRotatingFileHandler

from app.core.constants import LOG_RETENTION_DAYS, LOGS_DIR


class _DropNiceguiEventListenersRerenderWarning(logging.Filter):
    """Tab 等重复绑定导致 NiceGUI 打 WARNING，对业务无意义，默认不进入日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage() or ""
        if "Event listeners changed after initial definition" in msg:
            return False
        return True


def setup_logging() -> None:
    """初始化全局运行日志（按天轮转 UTC 午夜，备份数=保留天数）。"""
    root_logger = logging.getLogger()
    if getattr(root_logger, "_vpn_logging_initialized", False):
        return

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    root_logger.setLevel(logging.INFO)

    # Paramiko 在 INFO 下会输出 transport 握手与认证细节；业务层在连接成功时单独打一条即可。
    logging.getLogger("paramiko").setLevel(logging.WARNING)

    # NiceGUI：见 _DropNiceguiEventListenersRerenderWarning
    logging.getLogger("nicegui").addFilter(_DropNiceguiEventListenersRerenderWarning())

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 按天轮转，与 LOG_RETENTION_DAYS 对齐；suffix 形如 app.log.2026-04-08
    app_file_handler = TimedRotatingFileHandler(
        LOGS_DIR / "app.log",
        when="midnight",
        interval=1,
        backupCount=LOG_RETENTION_DAYS,
        encoding="utf-8",
        utc=True,
    )
    app_file_handler.setFormatter(formatter)
    root_logger.addHandler(app_file_handler)

    # 安装日志单独保留，避免依赖安装和源码编译过程刷屏控制台。
    install_logger = logging.getLogger("app.services.openvpn.installer")
    install_logger.setLevel(logging.INFO)
    install_logger.propagate = False

    install_file_handler = TimedRotatingFileHandler(
        LOGS_DIR / "openvpn-install.log",
        when="midnight",
        interval=1,
        backupCount=LOG_RETENTION_DAYS,
        encoding="utf-8",
        utc=True,
    )
    install_file_handler.setFormatter(formatter)
    for handler in list(install_logger.handlers):
        install_logger.removeHandler(handler)
    install_logger.addHandler(install_file_handler)

    # 对端 SSH（探测、安装 OpenVPN、SFTP .ovpn 等）：只写文件，不刷屏控制台
    peer_remote_logger = logging.getLogger("peer.remote")
    peer_remote_logger.setLevel(logging.INFO)
    peer_remote_logger.propagate = False
    peer_remote_file_handler = TimedRotatingFileHandler(
        LOGS_DIR / "peer-remote.log",
        when="midnight",
        interval=1,
        backupCount=LOG_RETENTION_DAYS,
        encoding="utf-8",
        utc=True,
    )
    peer_remote_file_handler.setFormatter(formatter)
    for handler in list(peer_remote_logger.handlers):
        peer_remote_logger.removeHandler(handler)
    peer_remote_logger.addHandler(peer_remote_file_handler)

    root_logger._vpn_logging_initialized = True
