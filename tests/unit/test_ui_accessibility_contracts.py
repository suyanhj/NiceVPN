# -*- coding: utf-8 -*-
"""最终集成：键盘、对比度、非颜色状态和危险确认可访问性契约。"""

from __future__ import annotations

import asyncio
from pathlib import Path
import re

from nicegui.testing import user_simulation

from app.ui.components.cards import metric_card, quick_link_card
from app.ui.components.confirm_dialog import build as build_confirm_dialog
from app.ui.components.status import status_badge


ROOT = Path(__file__).resolve().parents[2]
CSS_DIR = ROOT / "app" / "ui" / "static" / "css"


def _hex_to_rgb(value: str) -> tuple[float, float, float]:
    text = value.lstrip("#")
    return tuple(
        int(text[index : index + 2], 16) / 255.0
        for index in (0, 2, 4)
    )


def _relative_luminance(value: str) -> float:
    def channel(raw: float) -> float:
        if raw <= 0.04045:
            return raw / 12.92
        return ((raw + 0.055) / 1.055) ** 2.4

    red, green, blue = (
        channel(component) for component in _hex_to_rgb(value)
    )
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(foreground: str, background: str) -> float:
    first = _relative_luminance(foreground)
    second = _relative_luminance(background)
    high, low = max(first, second), min(first, second)
    return (high + 0.05) / (low + 0.05)


def _token(name: str) -> str:
    text = (CSS_DIR / "tokens.css").read_text(encoding="utf-8")
    match = re.search(
        rf"--{re.escape(name)}:\s*(#[0-9a-fA-F]{{6}})\s*;",
        text,
    )
    assert match is not None, f"missing token: {name}"
    return match.group(1)


def test_normal_text_tokens_meet_wcag_aa_on_admin_surfaces() -> None:
    """普通字号文本在所有主深色表面上至少达到 WCAG AA 4.5:1。"""

    foregrounds = (
        "vpn-text",
        "vpn-text-heading",
        "vpn-text-display",
        "vpn-text-muted",
        "vpn-text-soft",
    )
    backgrounds = (
        "vpn-bg",
        "vpn-bg-soft",
        "vpn-panel",
        "vpn-panel-2",
    )

    failures: list[str] = []
    for foreground_name in foregrounds:
        foreground = _token(foreground_name)
        for background_name in backgrounds:
            background = _token(background_name)
            ratio = _contrast(foreground, background)
            if ratio < 4.5:
                failures.append(
                    f"{foreground_name} {foreground} / "
                    f"{background_name} {background} = {ratio:.2f}:1"
                )

    assert failures == []


def test_semantic_status_colors_remain_readable_on_admin_surfaces() -> None:
    foregrounds = (
        "vpn-color-success",
        "vpn-color-warning",
        "vpn-color-danger",
        "vpn-color-info",
    )
    backgrounds = ("vpn-bg", "vpn-panel", "vpn-panel-2")

    for foreground_name in foregrounds:
        for background_name in backgrounds:
            assert _contrast(
                _token(foreground_name),
                _token(background_name),
            ) >= 4.5


def test_shared_focus_ring_covers_keyboard_interactive_components() -> None:
    shell = (CSS_DIR / "shell.css").read_text(encoding="utf-8")

    for selector in (
        ".vpn-action:focus-visible",
        ".vpn-action-menu__item:focus-visible",
        ".vpn-data-value__copy:focus-visible",
        ".vpn-card--clickable:focus-visible",
        ".vpn-confirm-dialog .q-btn:focus-visible",
        ".vpn-form-dialog .q-btn:focus-visible",
    ):
        assert selector in shell

    assert "outline: 2px solid var(--vpn-color-brand-soft)" in shell


def test_clickable_cards_expose_button_semantics_and_keyboard_handlers() -> None:
    state: dict[str, object] = {}

    def page() -> None:
        state["metric"] = metric_card(
            "在线设备",
            3,
            on_click=lambda: None,
        )
        state["quick"] = quick_link_card(
            "用户管理",
            route="/users",
        )

    async def verify() -> None:
        async with user_simulation(page) as user:
            response = await user.open("/")
            assert response.status_code == 200
            await user.should_see("在线设备")
            await user.should_see("用户管理")

    asyncio.run(verify())

    for card in (state["metric"], state["quick"]):
        container = card.container
        assert str(container._props.get("tabindex")) == "0"
        assert container._props.get("role") == "button"
        # click + Enter + Space 均已注册；避免只能鼠标点击。
        assert len(container._event_listeners) >= 3


def test_status_badges_never_rely_on_color_alone() -> None:
    state: dict[str, object] = {}

    def page() -> None:
        for tone in (
            "running",
            "disabled",
            "warning",
            "failed",
            "unknown",
            "success",
            "info",
        ):
            state[tone] = status_badge(tone)

    async def verify() -> None:
        async with user_simulation(page) as user:
            response = await user.open("/")
            assert response.status_code == 200

    asyncio.run(verify())

    for badge in state.values():
        assert str(badge.label.text).strip()
        assert badge.icon is not None


def test_danger_confirmation_has_textual_impact_and_negative_action() -> None:
    state: dict[str, object] = {}

    def page() -> None:
        state["dialog"] = build_confirm_dialog(
            "确认删除所选对象？",
            lambda: None,
            title="危险操作",
            impact="删除后不可恢复，并会影响现有连接。",
            object_count=3,
            confirm_text="DELETE",
            danger_level="danger",
        )

    async def verify() -> None:
        async with user_simulation(page) as user:
            response = await user.open("/")
            assert response.status_code == 200
            handle = state["dialog"]
            handle.dialog.open()
            await user.should_see("删除后不可恢复")
            await user.should_see("影响对象：3 项")
            await user.should_see("请输入 DELETE 确认")

    asyncio.run(verify())

    handle = state["dialog"]
    assert handle.confirm_button._props.get("color") == "negative"
    assert handle.confirm_input is not None
    assert handle.confirm_button._props.get("disable") is True
