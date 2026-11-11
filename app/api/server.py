# -*- coding: utf-8 -*-
"""同一 NiceVPN 进程内的第二个端口：只服务公网 VPN API。"""

from __future__ import annotations

import logging
import threading
import time

import uvicorn
from fastapi import FastAPI

from app.api.download import router as download_router
from app.api.vpn_ops import router as vpn_ops_router
from app.api.sso import router as sso_router

logger = logging.getLogger(__name__)


def create_api_app() -> FastAPI:
    """只注册业务接口及其一次性下载端点，不暴露任何管理 UI 路由。"""
    api_app = FastAPI(
        title="NiceVPN Public API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    api_app.include_router(vpn_ops_router, prefix="/api")
    api_app.include_router(download_router)
    api_app.include_router(sso_router)
    return api_app


class PublicApiListener:
    """复用 Uvicorn，在守护线程上为 API 增加独立监听端口。

    Web Uvicorn 仍在主线程处理 SIGINT/SIGTERM；API 线程只接收显式退出通知，
    不重复注册信号处理器，也不启动 NiceGUI 调度器或 conntrack 审计。
    """

    def __init__(self, *, host: str, port: int) -> None:
        self.server = uvicorn.Server(
            uvicorn.Config(
                create_api_app(),
                host=host,
                port=port,
                log_level="info",
                log_config=None,
                access_log=False,  # 禁止 URL 中一次性令牌进入访问日志。
                timeout_graceful_shutdown=5,
            )
        )
        self.thread: threading.Thread | None = None

    def start(self, *, timeout: float = 5.0) -> None:
        """等待 API 完成监听；端口占用等启动错误须中止主服务启动。"""
        if self.thread is not None:
            raise RuntimeError("公网 API 监听器不能重复启动")
        self.thread = threading.Thread(
            target=self.server.run,
            name="nicevpn-public-api",
            daemon=True,
        )
        self.thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.server.started:
                logger.info("VPN 公网 API 已监听 %s:%s", self.server.config.host, self.server.config.port)
                return
            if not self.thread.is_alive():
                raise RuntimeError("公网 API 启动失败，请检查监听端口是否被占用")
            time.sleep(0.02)
        self.stop()
        raise RuntimeError("公网 API 监听启动超时")

    def stop(self) -> None:
        if self.thread is None:
            return
        self.server.should_exit = True
        self.thread.join(timeout=8)
        if self.thread.is_alive():
            logger.warning("公网 API 未在预期时间内停止")
