# -*- coding: utf-8 -*-
"""独立公网 VPN API 服务入口：与 NiceGUI 管理站点隔离监听。"""

from __future__ import annotations

import logging
import os

import uvicorn
from fastapi import FastAPI

from app.api.download import router as download_router
from app.api.vpn_ops import router as vpn_ops_router
from app.core.constants import DEFAULT_API_HOST, DEFAULT_API_PORT


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
    return api_app


def run_api() -> None:
    """使用 Uvicorn 原生信号与 lifespan 机制运行独立 API 进程。"""
    # 与管理进程的 TimedRotatingFileHandler 分离，避免多进程同时轮转 app.log。
    # systemd / Docker 均会收集标准输出上的 API 运行日志。
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    host = os.environ.get("NICEVPN_API_HOST", DEFAULT_API_HOST)
    port = int(os.environ.get("NICEVPN_API_PORT", str(DEFAULT_API_PORT)))
    uvicorn.run(create_api_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_api()
