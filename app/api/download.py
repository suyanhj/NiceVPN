# -*- coding: utf-8 -*-
"""一次性配置文件下载 API 路由"""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from app.utils.audit_log import AuditLogger

router = APIRouter()


@router.get("/download/{token}")
async def download_ovpn(token: str):
    """
    一次性下载 .ovpn 或批量 zip（由令牌关联路径决定）。
    链接为一次性且在 1 小时内过期。

    成功：返回文件流（200 OK + Content-Disposition）
    失败：404（不存在）/ 410（已使用或已过期）/ 500（文件读取失败）
    """
    audit = AuditLogger()

    # 消费令牌（原子性：先置 used=true 再返回文件）
    from app.services.download.link_mgr import consume_link
    result = consume_link(token)

    if result is None:
        # 令牌不存在
        audit.log("download_ovpn", "download_link", token,
                  {"result_status": "not_found"}, "failure")
        return JSONResponse(
            status_code=404,
            content={"detail": "下载链接无效或已过期"}
        )

    error = result.get("error")
    if error:
        # 令牌已使用或已过期
        status_code = 410
        detail_map = {
            "expired": "下载链接已过期，请联系管理员重新生成",
            "already_used": "下载链接已被使用，每个链接仅限下载一次",
        }
        audit.log("download_ovpn", "download_link", token,
                  {"result_status": error, "username": result.get("username")},
                  "failure")
        return JSONResponse(
            status_code=status_code,
            content={"detail": detail_map.get(error, "下载链接无效")}
        )

    # 校验文件存在性
    file_path = Path(result["file_path"])
    if not file_path.exists():
        audit.log("download_ovpn", "download_link", token,
                  {"result_status": "file_missing",
                   "username": result.get("username")}, "failure")
        return JSONResponse(
            status_code=500,
            content={"detail": "文件读取失败，请联系管理员"}
        )

    username = result.get("username", "client")
    disp = result.get("download_filename")
    if disp is None or str(disp).strip() == "":
        disp = f"{username}.ovpn"
    else:
        disp = str(disp).strip()
    media = "application/zip" if disp.lower().endswith(".zip") else "application/octet-stream"
    audit.log("download_ovpn", "download_link", token,
              {"result_status": "success", "username": username}, "success")

    return FileResponse(
        path=file_path,
        filename=disp,
        media_type=media,
    )
