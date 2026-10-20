# -*- coding: utf-8 -*-
"""用户组页面纯展示数据适配。"""

from __future__ import annotations

from typing import Any, Mapping


def adapt_group_view(group: Mapping[str, Any], *, is_root: bool = False) -> dict[str, Any]:
    """标准化根组、状态、用户数与网段展示语义。"""

    group_id = str(group.get("id") or "")
    status = str(group.get("status") or "active")
    active = status == "active"
    user_count = int(group.get("user_count") or 0)
    name = str(group.get("name") or "未命名用户组")
    subnet = str(group.get("subnet") or "未配置子网")
    return {
        "id": group_id,
        "name": name,
        "subnet": subnet,
        "status": status,
        "status_label": "启用" if active else "停用",
        "status_tone": "running" if active else "disabled",
        "active": active,
        "user_count": user_count,
        "is_root": bool(is_root),
        "can_delete": not is_root and user_count == 0,
        "delete_reason": (
            "根组定义全局 VPN 子网，不允许删除"
            if is_root
            else (f"组内有 {user_count} 个用户，无法删除" if user_count else "")
        ),
    }
