# -*- coding: utf-8 -*-
"""API 专用的随机令牌一次性下载路由；管理端不挂载。"""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app.services.download.link_mgr import consume_link
from app.utils.audit_log import AuditLogger

router = APIRouter()

# .ovpn 内可能携带客户端私钥，禁止浏览器与中间代理缓存。
_DOWNLOAD_HEADERS = {
    "Cache-Control": "no-store, private",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


@router.get("/download/{token}")
async def download_ovpn(token: str):
    """无需 Basic，仅允许有效的一次性令牌；日志不可写入完整令牌。"""
    audit = AuditLogger()
    token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    result = await run_in_threadpool(consume_link, token)

    if result is None:
        audit.log(
            "download_ovpn", "download_link", token_digest,
            {"result_status": "not_found"}, "failure",
        )
        return JSONResponse(
            status_code=404,
            content={"detail": "下载链接无效或已过期"},
            headers=_DOWNLOAD_HEADERS,
        )

    error = result.get("error")
    if error:
        detail_map = {
            "expired": "下载链接已过期，请联系管理员重新生成",
            "already_used": "下载链接已被使用，每个链接仅限下载一次",
            "file_missing": "文件读取失败，请联系管理员",
        }
        status_code = 500 if error == "file_missing" else 410
        audit.log(
            "download_ovpn", "download_link", token_digest,
            {"result_status": error, "username": result.get("username")},
            "failure",
        )
        return JSONResponse(
            status_code=status_code,
            content={"detail": detail_map.get(error, "下载链接无效")},
            headers=_DOWNLOAD_HEADERS,
        )

    file_path = Path(result["file_path"])
    username = result.get("username") or "client"
    name = str(result.get("download_filename") or f"{username}.ovpn").strip()
    media = (
        "application/zip" if name.lower().endswith(".zip")
        else "application/octet-stream"
    )
    audit.log(
        "download_ovpn", "download_link", token_digest,
        {"result_status": "success", "username": username}, "success",
    )
    return FileResponse(
        path=file_path,
        filename=name,
        media_type=media,
        headers=_DOWNLOAD_HEADERS,
    )
