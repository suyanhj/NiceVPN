# -*- coding: utf-8 -*-
"""VPN 用户对外 HTTP API（HTTP Basic）：创建用户+下载链、批量创建、注销用户、重置设备绑定。"""
from __future__ import annotations

import logging
import re
import secrets
from pathlib import Path

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from app.core.config import load_config
from app.services.download.link_mgr import create_link
from app.services.download.bundle_zip import build_ovpn_zip
from app.services.group.crud import GroupService
from app.services.user.crud import UserService
from app.services.user.device_bind import DeviceBindingService
from app.utils.api_basic_credentials import load_api_basic_credentials
from app.utils.audit_log import AuditLogger
from app.utils.public_base_url import resolve_download_base_url

logger = logging.getLogger(__name__)

# 批量创建：条目数与各 count 之和上限，防止误用或滥用
_BATCH_MAX_ITEMS = 64
_BATCH_MAX_TOTAL_ACCOUNTS = 2000
# 批量合并 zip 文件名前缀（build_ovpn_zip 会再拼 UTC 时间戳）
_BATCH_ZIP_BUNDLE_PREFIX = "vpns"

http_basic = HTTPBasic(auto_error=False)
router = APIRouter(prefix="/vpn", tags=["vpn-public-api"])


def _verify_api_basic(
    credentials: HTTPBasicCredentials | None = Depends(http_basic),
) -> str:
    """校验 HTTP Basic，成功返回登录用户名。"""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要 HTTP Basic 认证",
            headers={"WWW-Authenticate": "Basic"},
        )
    try:
        expect_user, expect_pwd = load_api_basic_credentials()
    except FileNotFoundError as e:
        logger.error("VPN API Basic 凭据文件不存在: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API 凭据未就绪，请确认系统已完成初始化",
        ) from e
    except ValueError as e:
        logger.error("VPN API Basic 凭据无效: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API 凭据损坏，请检查 data 目录",
        ) from e

    if not (
        secrets.compare_digest(credentials.username, expect_user)
        and secrets.compare_digest(credentials.password, expect_pwd)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _require_initialized() -> None:
    if not load_config().initialized:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="系统尚未初始化，VPN API 不可用",
        )


def _pick_default_group_id() -> str:
    groups = GroupService().list_all()
    if not groups:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="无任何用户组，请先在控制台创建用户组",
        )
    for g in groups:
        if g.get("name") == "默认用户组":
            return str(g["id"])
    return str(groups[0]["id"])


def _resolve_group_id_by_name(group_name: str | None) -> str:
    """
    按组名称解析 group_id；名称为空则走默认组。
    """
    name = (group_name or "").strip()
    if not name:
        return _pick_default_group_id()
    for g in GroupService().list_all():
        if str(g.get("name") or "").strip() == name:
            return str(g["id"])
    raise HTTPException(status_code=400, detail=f"用户组不存在: {name}")


def _prefix_username_pattern(prefix: str) -> re.Pattern[str]:
    """与控制台批量命名一致：仅匹配 prefix 本身或 prefix_数字（如 lisi、lisi_1）。"""
    return re.compile(r"^" + re.escape(prefix) + r"(?:_\d+)?$")


def _usernames_matching_prefix(prefix: str, user_service: UserService) -> list[str]:
    """
    列出所有非删除用户中，符合前缀规则的用户名（升序：无前缀序号在前，其余按 _ 后数字排序）。
    """
    raw = prefix.strip()
    if not raw or "/" in raw or "\\" in raw:
        raise HTTPException(status_code=400, detail="用户前缀非法或为空")
    pat = _prefix_username_pattern(raw)
    matched: list[tuple[tuple[int, int], str]] = []
    for row in user_service.list_all():
        un = str(row.get("username") or "")
        if not pat.fullmatch(un):
            continue
        if un == raw:
            key = (-1, 0)
        else:
            suf = un.split("_")[-1]
            key = (0, int(suf)) if suf.isdigit() else (1, 0)
        matched.append((key, un))
    matched.sort(key=lambda x: (x[0][0], x[0][1], x[1]))
    return [m[1] for m in matched]


def _build_create_usernames(base: str, count: int) -> list[str]:
    """与 UsersPage._do_create 一致的展开：count==1 仅 base，否则 base + base_1 … base_{count-1}。"""
    b = base.strip()
    if count == 1:
        return [b]
    return [b] + [f"{b}_{i}" for i in range(1, count)]


def _already_active_usernames(user_service: UserService, names: list[str]) -> list[str]:
    """列出 names 中已是非删除状态的用户（存在则整单不重复创建）。"""
    found: list[str] = []
    for un in names:
        row = user_service.get(un)
        if row and str(row.get("status") or "").strip() != "deleted":
            found.append(un)
    return found


class CreateVpnUserBody(BaseModel):
    """创建 VPN 用户请求体"""

    username: str = Field(..., min_length=1, max_length=64, description="VPN 用户名前缀；数量>1 时生成 base、base_1…")
    group_name: str | None = Field(
        default=None,
        description="所属组名称，须与控制台一致；留空则「默认用户组」或列表第一个组",
    )
    count: int = Field(default=1, ge=1, le=500, description="创建账号数量，与控制台批量规则一致")


class CreateVpnUserResponse(BaseModel):
    """创建结果：若目标用户已存在则 created=false，不生成新链接、不重复建号。"""

    created: bool = Field(description="本次是否实际执行了创建并生成下载链接")
    usernames: list[str]
    download_url: str | None = Field(default=None, description="一次性下载链接；已存在用户时为 null")
    message: str | None = Field(default=None, description="例如已存在时的提示文案")


class DeleteUserResponse(BaseModel):
    ok: bool = True
    username_prefix: str
    deleted: list[str]
    message: str | None = Field(default=None, description="无匹配用户时的说明")


class ResetDeviceBindingResponse(BaseModel):
    username_prefix: str
    reset_users: list[str]
    bindings_cleared: int = Field(description="成功清除的绑定条目数（含多用户上曾有绑定）")


def _execute_create_vpn_user(body: CreateVpnUserBody, request: Request) -> CreateVpnUserResponse:
    """
    执行单次创建逻辑（POST /users）。
    抛 HTTPException 表示不可恢复错误。
    """
    audit = AuditLogger()
    raw_name = body.username.strip()
    if not raw_name or "/" in raw_name or "\\" in raw_name:
        raise HTTPException(status_code=400, detail="用户名非法或为空")

    group_id = _resolve_group_id_by_name(body.group_name)
    names = _build_create_usernames(raw_name, body.count)

    user_service = UserService()
    existing = _already_active_usernames(user_service, names)
    if existing:
        msg = "以下用户已存在，未重复创建: " + ", ".join(existing)
        audit.log(
            "api_create_vpn_user",
            "user",
            raw_name,
            {"skipped_existing": existing, "count": body.count},
            "success",
        )
        logger.info("API 创建跳过（已存在）prefix=%s existing=%s", raw_name, existing)
        return CreateVpnUserResponse(
            created=False,
            usernames=names,
            download_url=None,
            message=msg,
        )

    cfg = load_config()
    base_url = resolve_download_base_url(request, cfg.download_base_url)
    if not base_url:
        raise HTTPException(
            status_code=400,
            detail="无法生成下载链接：请在系统设置填写 download_base_url，或正确配置反向代理 Host/X-Forwarded-*",
        )

    created_paths: list[tuple[str, Path]] = []
    for un in names:
        try:
            user_service.create(
                username=un,
                group_id=group_id,
                password_enabled=False,
                password=None,
            )
        except ValueError as e:
            audit.log("api_create_vpn_user", "user", un, {"error": str(e)}, "failure")
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            logger.exception("API 创建用户失败 user=%s", un)
            audit.log("api_create_vpn_user", "user", un, {"error": str(e)}, "failure")
            raise HTTPException(status_code=500, detail="创建用户失败，详见服务日志") from e

        user_data = user_service.get(un)
        ovpn_path = (user_data or {}).get("ovpn_file_path")
        if not ovpn_path:
            audit.log("api_create_vpn_user", "user", un, {"error": "无 ovpn 路径"}, "failure")
            raise HTTPException(status_code=500, detail=f"用户 {un} 已创建但未找到配置文件路径")
        created_paths.append((un, Path(ovpn_path)))

    try:
        if len(created_paths) == 1:
            un, op = created_paths[0]
            download_url = create_link(username=un, ovpn_path=str(op), base_url=base_url)
        else:
            zip_path, dl_name = build_ovpn_zip(created_paths, raw_name)
            download_url = create_link(
                username=raw_name,
                ovpn_path=str(zip_path),
                base_url=base_url,
                download_filename=dl_name,
            )
    except FileNotFoundError as e:
        logger.error("API 打包或生成下载链接失败（文件缺失）: %s", e)
        audit.log("api_create_vpn_user", "user", raw_name, {"error": str(e)}, "failure")
        raise HTTPException(status_code=500, detail="打包或生成下载链接失败") from e
    except OSError as e:
        logger.exception("API 写 zip 失败 prefix=%s", raw_name)
        audit.log("api_create_vpn_user", "user", raw_name, {"error": str(e)}, "failure")
        raise HTTPException(status_code=500, detail="写入压缩包失败") from e
    except Exception as e:
        logger.exception("API 生成下载链接失败 prefix=%s", raw_name)
        audit.log("api_create_vpn_user", "user", raw_name, {"error": str(e)}, "failure")
        raise HTTPException(status_code=500, detail="生成下载链接失败") from e

    audit.log(
        "api_create_vpn_user",
        "user",
        raw_name,
        {"group_id": group_id, "count": body.count, "base_url": base_url, "bundle": len(created_paths) > 1},
        "success",
    )
    logger.info(
        "API 已创建用户 prefix=%s count=%s bundle_zip=%s",
        raw_name,
        len(created_paths),
        len(created_paths) > 1,
    )
    return CreateVpnUserResponse(
        created=True,
        usernames=names,
        download_url=download_url,
        message=None,
    )


@router.post(
    "/users",
    response_model=CreateVpnUserResponse,
    dependencies=[Depends(_verify_api_basic), Depends(_require_initialized)],
)
def api_create_vpn_user(
    body: CreateVpnUserBody,
    request: Request,
) -> CreateVpnUserResponse:
    """
    创建 VPN 用户并返回一次性下载链接。

    - 计划创建的任一站内账号已存在：整单跳过，created=false，不生成新链接。
    - 仅当全部为新用户时才创建；单用户为 .ovpn，多用户为 zip。

    须在「系统设置」配置 download_base_url，或由反向代理传入可被解析的 Host（仅新建时需要）。
    """
    return _execute_create_vpn_user(body, request)


def _execute_batch_create_vpn_users(items: list[CreateVpnUserBody], request: Request) -> CreateVpnUserResponse:
    """
    批量创建：逐项幂等；成功新建的用户全部打入同一个 zip（vpns_UTC时间.zip）。

    usernames 为请求顺序下各条目展开后的「计划用户名」完整列表；
    压缩包内仅含本次实际新建的账号对应 ovpn。
    """
    audit = AuditLogger()
    user_service = UserService()
    all_planned: list[str] = []
    created_paths: list[tuple[str, Path]] = []
    skip_messages: list[str] = []

    for body in items:
        raw_name = body.username.strip()
        if not raw_name or "/" in raw_name or "\\" in raw_name:
            raise HTTPException(status_code=400, detail="用户名非法或为空")
        group_id = _resolve_group_id_by_name(body.group_name)
        names = _build_create_usernames(raw_name, body.count)
        all_planned.extend(names)

        existing = _already_active_usernames(user_service, names)
        if existing:
            msg = f"前缀「{raw_name}」：以下用户已存在，未重复创建 — {', '.join(existing)}"
            skip_messages.append(msg)
            audit.log(
                "api_create_vpn_user",
                "user",
                raw_name,
                {"skipped_existing": existing, "count": body.count, "batch": True},
                "success",
            )
            logger.info("API 批量创建跳过（已存在）prefix=%s existing=%s", raw_name, existing)
            continue

        for un in names:
            try:
                user_service.create(
                    username=un,
                    group_id=group_id,
                    password_enabled=False,
                    password=None,
                )
            except ValueError as e:
                audit.log("api_create_vpn_user", "user", un, {"error": str(e), "batch": True}, "failure")
                raise HTTPException(status_code=400, detail=str(e)) from e
            except Exception as e:
                logger.exception("API 批量创建用户失败 user=%s", un)
                audit.log("api_create_vpn_user", "user", un, {"error": str(e), "batch": True}, "failure")
                raise HTTPException(status_code=500, detail="创建用户失败，详见服务日志") from e

            user_data = user_service.get(un)
            ovpn_path = (user_data or {}).get("ovpn_file_path")
            if not ovpn_path:
                audit.log("api_create_vpn_user", "user", un, {"error": "无 ovpn 路径", "batch": True}, "failure")
                raise HTTPException(status_code=500, detail=f"用户 {un} 已创建但未找到配置文件路径")
            created_paths.append((un, Path(ovpn_path)))

    if not created_paths:
        merged_msg = "；".join(skip_messages) if skip_messages else "未创建任何用户"
        audit.log(
            "api_create_vpn_user_batch",
            "user",
            "batch",
            {"planned": all_planned, "skipped_messages": skip_messages},
            "success",
        )
        return CreateVpnUserResponse(
            created=False,
            usernames=all_planned,
            download_url=None,
            message=merged_msg,
        )

    cfg = load_config()
    base_url = resolve_download_base_url(request, cfg.download_base_url)
    if not base_url:
        raise HTTPException(
            status_code=400,
            detail="无法生成下载链接：请在系统设置填写 download_base_url，或正确配置反向代理 Host/X-Forwarded-*",
        )

    try:
        zip_path, dl_name = build_ovpn_zip(created_paths, _BATCH_ZIP_BUNDLE_PREFIX)
        download_url = create_link(
            username=_BATCH_ZIP_BUNDLE_PREFIX,
            ovpn_path=str(zip_path),
            base_url=base_url,
            download_filename=dl_name,
        )
    except FileNotFoundError as e:
        logger.error("API 批量打包或生成下载链接失败（文件缺失）: %s", e)
        audit.log("api_create_vpn_user_batch", "user", "batch", {"error": str(e)}, "failure")
        raise HTTPException(status_code=500, detail="打包或生成下载链接失败") from e
    except OSError as e:
        logger.exception("API 批量写 zip 失败")
        audit.log("api_create_vpn_user_batch", "user", "batch", {"error": str(e)}, "failure")
        raise HTTPException(status_code=500, detail="写入压缩包失败") from e
    except Exception as e:
        logger.exception("API 批量生成下载链接失败")
        audit.log("api_create_vpn_user_batch", "user", "batch", {"error": str(e)}, "failure")
        raise HTTPException(status_code=500, detail="生成下载链接失败") from e

    note = "；".join(skip_messages) if skip_messages else None
    audit.log(
        "api_create_vpn_user_batch",
        "user",
        "batch",
        {
            "planned": all_planned,
            "created_usernames": [p[0] for p in created_paths],
            "base_url": base_url,
            "bundle_file": dl_name,
            "skipped_parts": skip_messages,
        },
        "success",
    )
    logger.info(
        "API 批量创建完成 zip=%s entries=%s skipped_parts=%s",
        dl_name,
        len(created_paths),
        len(skip_messages),
    )
    return CreateVpnUserResponse(
        created=True,
        usernames=all_planned,
        download_url=download_url,
        message=note,
    )


VpnUserBatchBody = Annotated[
    list[CreateVpnUserBody],
    Field(
        min_length=1,
        max_length=_BATCH_MAX_ITEMS,
        description="多条创建项；每项 username/count 独立，语义同单次 POST /users",
    ),
]


@router.post(
    "/users/batch",
    response_model=CreateVpnUserResponse,
    dependencies=[Depends(_verify_api_basic), Depends(_require_initialized)],
)
def api_create_vpn_users_batch(
    request: Request,
    items: VpnUserBatchBody = Body(...),
) -> CreateVpnUserResponse:
    f"""批量创建：请求体为 JSON 数组，每项同 POST /api/vpn/users。

    - 响应字段与 POST /users **相同**（无需单独适配）；任一无新建时 `created=false` 且无链接。
    - 只要有新建账号：全部打入 **同一个** zip，文件名形如 `vpns_YYYYMMDD_HHMMSS.zip`（UTC），与其它条目无关。
    - `usernames` 为全部条目按请求顺序展开后的计划用户名列表；zip 内 **仅含本次实际新建** 的 `用户名.ovpn`。
    - 单请求最多 {_BATCH_MAX_ITEMS} 条，`count` 之和不超过 {_BATCH_MAX_TOTAL_ACCOUNTS}。
    """
    total_n = sum(b.count for b in items)
    if total_n > _BATCH_MAX_TOTAL_ACCOUNTS:
        raise HTTPException(
            status_code=400,
            detail=f"批量总账号数（各 count 之和）不得超过 {_BATCH_MAX_TOTAL_ACCOUNTS}，当前为 {total_n}",
        )
    return _execute_batch_create_vpn_users(items, request)


@router.delete(
    "/users/{username}",
    response_model=DeleteUserResponse,
    dependencies=[Depends(_verify_api_basic), Depends(_require_initialized)],
)
def api_delete_vpn_user(
    username: str,
) -> DeleteUserResponse:
    """按用户名前缀批量注销：匹配 base 与 base_数字（如 lisi、lisi_1），与控制台批量命名一致。"""
    audit = AuditLogger()
    prefix = username.strip()
    if not prefix:
        raise HTTPException(status_code=400, detail="用户名为空")

    user_service = UserService()
    targets = _usernames_matching_prefix(prefix, user_service)
    if not targets:
        audit.log(
            "api_delete_vpn_user",
            "user",
            prefix,
            {"deleted": [], "note": "no_matching_users"},
            "success",
        )
        logger.info("API 按前缀删除：无匹配用户 prefix=%s", prefix)
        return DeleteUserResponse(
            username_prefix=prefix,
            deleted=[],
            message="没有符合前缀规则的活跃用户，无需删除",
        )

    deleted: list[str] = []
    for name in targets:
        try:
            user_service.delete(name)
            deleted.append(name)
        except ValueError as e:
            audit.log("api_delete_vpn_user", "user", name, {"error": str(e)}, "failure")
            raise HTTPException(status_code=404, detail=str(e)) from e
        except Exception as e:
            logger.exception("API 删除用户失败 user=%s", name)
            audit.log("api_delete_vpn_user", "user", name, {"error": str(e)}, "failure")
            raise HTTPException(status_code=500, detail="删除用户失败，详见服务日志") from e

    audit.log(
        "api_delete_vpn_user",
        "user",
        prefix,
        {"deleted": deleted},
        "success",
    )
    logger.info("API 已按前缀删除用户 prefix=%s deleted=%s", prefix, deleted)
    return DeleteUserResponse(username_prefix=prefix, deleted=deleted)


@router.post(
    "/users/{username}/reset-device-binding",
    response_model=ResetDeviceBindingResponse,
    dependencies=[Depends(_verify_api_basic), Depends(_require_initialized)],
)
def api_reset_device_binding(
    username: str,
) -> ResetDeviceBindingResponse:
    """按用户名前缀批量清除设备绑定（规则同删除）。"""
    audit = AuditLogger()
    prefix = username.strip()
    if not prefix:
        raise HTTPException(status_code=400, detail="用户名为空")

    user_service = UserService()
    targets = _usernames_matching_prefix(prefix, user_service)
    if not targets:
        raise HTTPException(
            status_code=404,
            detail=f"没有符合前缀「{prefix}」的活跃用户（规则：完全一致或 {prefix}_数字）",
        )

    bind_svc = DeviceBindingService()
    cleared = 0
    for name in targets:
        if bind_svc.reset_binding(name):
            cleared += 1

    audit.log(
        "api_reset_device_binding",
        "user",
        prefix,
        {"reset_users": targets, "bindings_cleared": cleared},
        "success",
    )
    logger.info(
        "API 已按前缀重置设备绑定 prefix=%s users=%s bindings_cleared=%s",
        prefix,
        targets,
        cleared,
    )
    return ResetDeviceBindingResponse(
        username_prefix=prefix,
        reset_users=targets,
        bindings_cleared=cleared,
    )
