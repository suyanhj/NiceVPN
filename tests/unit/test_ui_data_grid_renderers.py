# -*- coding: utf-8 -*-
"""AG Grid 共享展示 renderer 契约。"""

from app.ui.components.data_grid_renderers import (
    ACTION_BUTTON_RENDERER,
    EXPIRY_WITH_DETAIL_RENDERER,
    STATUS_PILL_RENDERER,
)


def test_shared_renderers_are_visual_only_and_use_safe_dom_text() -> None:
    renderers = (
        STATUS_PILL_RENDERER,
        EXPIRY_WITH_DETAIL_RENDERER,
        ACTION_BUTTON_RENDERER,
    )

    for renderer in renderers:
        assert "document.createElement" in renderer
        assert "textContent" in renderer
        assert "innerHTML" not in renderer
        assert "fetch(" not in renderer
        assert "axios" not in renderer
        assert "/api/" not in renderer


def test_shared_renderers_expose_expected_design_hooks() -> None:
    assert "vpn-grid-status-pill" in STATUS_PILL_RENDERER
    assert "vpn-grid-stacked-value" in EXPIRY_WITH_DETAIL_RENDERER
    assert "vpn-grid-inline-action" in ACTION_BUTTON_RENDERER
