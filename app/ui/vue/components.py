# -*- coding: utf-8 -*-
"""NiceGUI V2 视觉组件：使用原生 Vue SFC 验证视觉方向。"""

from nicegui import ui


class VpnSpotlightSurface(
    ui.element,
    component="VpnSpotlightSurface.vue",
):
    """只负责视觉反馈，不承载业务状态或敏感数据。"""

    def __init__(self) -> None:
        super().__init__()
        self.classes("vpn-v2-spotlight-host")
