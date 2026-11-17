# -*- coding: utf-8 -*-
"""Guard the remaining NiceVPN V2 visual surfaces against style drift."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_setup_wizard_uses_shared_v2_tokens() -> None:
    css = _read("app/ui/static/css/setup-v2.css")
    for token in (
        "--vpn-bg",
        "--vpn-panel",
        "--vpn-border",
        "--vpn-text",
        "--vpn-color-brand",
        "--vpn-color-success",
    ):
        assert token in css
    assert "#15bde8" not in css
    assert "#0c0c0d" not in css


def test_peer_manual_surfaces_use_shared_v2_tokens() -> None:
    css = _read("app/ui/static/css/legacy.css")
    start = css.index("/* 对端部署说明与远程日志：")
    end = css.index(".peer-install-log-frame {", start)
    peer_css = css[start:end]
    for token in (
        "--vpn-panel",
        "--vpn-bg-soft",
        "--vpn-border",
        "--vpn-text-heading",
        "--vpn-font-mono",
    ):
        assert token in peer_css
    assert "rgba(30, 41, 59" not in peer_css
    assert "rgba(15, 23, 42" not in peer_css


def test_sso_helper_text_uses_shared_component_styles() -> None:
    source = _read("app/ui/pages/settings_sso.py")
    assert 'classes("vpn-help-hint")' in source
    assert 'classes("section-caption")' in source
    assert "text-gray-400" not in source
    assert "text-gray-500" not in source


def test_alert_card_uses_semantic_classes_instead_of_inline_styles() -> None:
    source = _read("app/ui/components/alert_card.py")
    css = _read("app/ui/static/css/shell.css")
    assert "alert-card__icon-wrap--info" in source
    assert "alert-card__icon-wrap--warning" in source
    assert "alert-card__icon-wrap--error" in source
    assert ".alert-card__icon-wrap--info" in css
    assert ".alert-card__icon-wrap--warning" in css
    assert ".alert-card__icon-wrap--error" in css
    assert ".style(" not in source
