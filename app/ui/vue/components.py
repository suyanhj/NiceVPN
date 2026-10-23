# -*- coding: utf-8 -*-
"""NiceGUI V2 视觉组件：正式构建产物优先，源码 SFC 作为开发回退。"""

from pathlib import Path

from nicegui import ui


_COMPONENT_ROOT = Path(__file__).resolve().parent
_SPOTLIGHT_DIST = _COMPONENT_ROOT / "dist" / "VpnSpotlightSurface.js"
_SPOTLIGHT_COMPONENT = (
    "dist/VpnSpotlightSurface.js"
    if _SPOTLIGHT_DIST.is_file()
    else "VpnSpotlightSurface.vue"
)


class VpnSpotlightSurface(
    ui.element,
    component=_SPOTLIGHT_COMPONENT,
):
    """只负责视觉反馈，不承载业务状态或敏感数据。"""

    def __init__(self) -> None:
        super().__init__()
        self.classes("vpn-v2-spotlight-host")
