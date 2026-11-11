# -*- coding: utf-8 -*-
"""Enterprise SSO settings for NiceGUI admin, never mounted on Public API."""
from __future__ import annotations

from urllib.parse import urlsplit

from nicegui import ui

from app.core.config import load_config, save_config
from app.services.auth.provider_store import (
    SsoProviderStore, UNIFIED_DINGTALK_ID, UNIFIED_WECOM_ID,
)
from app.utils.public_domains import resolve_sso_origin, with_default_ip_api_port
from app.utils.sso_enterprises import parse_allowed_enterprises
from app.ui.components.actions import primary_action
from app.ui.components.feedback import safe_notify
from app.ui.components.form_section import render_form_section
from app.ui.components.help import field_with_help


def _http_origin(value: str) -> bool:
    """Support local IP:port and HTTP/HTTPS domains without extra TLS setup."""
    try:
        p = urlsplit(value)
        return bool(
            p.scheme in {"http", "https"} and p.hostname
            and p.username is None and p.password is None
            and not p.path and not p.query and not p.fragment
            and not any(ch.isspace() or ord(ch) < 32 for ch in value)
            and (p.port is None or 1 <= p.port <= 65535)
        )
    except ValueError:
        return False


class SsoSettingsPanel:
    """Reusable admin-only SSO panel consistent with V2 settings sections."""

    def __init__(self, parent):
        self.parent = parent
        self.cfg = load_config()
        self.providers = SsoProviderStore()

    def render(self) -> None:
        def global_body():
            self.enable = field_with_help(
                lambda: ui.switch(
                    "开启认证",
                    value=bool(self.cfg.get("enterprise_sso_enabled", False)),
                ),
                "修改后需要重启 NiceVPN 和 OpenVPN。",
                aria_label="开启认证说明",
                control_grow=False,
            )
            self.all_users = field_with_help(
                lambda: ui.switch(
                    "所有人生效",
                    value=bool(self.cfg.get("enterprise_sso_all_users", False)),
                ),
                "开启后全部 VPN 证书连接均要求认证；关闭时只按组或标签要求认证。",
                aria_label="全员认证说明",
                control_grow=False,
            )
            self.base_url = field_with_help(
                lambda: ui.input(
                    "认证地址",
                    value=str(self.cfg.get("enterprise_sso_public_base_url") or ""),
                    placeholder=resolve_sso_origin(self.cfg) or "http://192.168.1.10:8881",
                ).classes("w-full"),
                "留空时使用系统访问地址；支持 HTTP/HTTPS 和 IP:端口。",
                aria_label="企业认证地址说明",
            )
        render_form_section(
            "认证开关",
            body=global_body,
            actions=lambda: primary_action(
                "保存", icon="save", on_click=self._save_global,
            ),
        )

        def unified_body() -> None:
            rows = self.providers.list_metadata()
            dingtalk = next((row for row in rows if row["id"] == UNIFIED_DINGTALK_ID), None)
            wecom = next((row for row in rows if row["id"] == UNIFIED_WECOM_ID), None)
            current = "wecom" if wecom and wecom["enabled"] else "dingtalk"
            self.auth_method = ui.select(
                {"dingtalk": "钉钉", "wecom": "企业微信"},
                value=current, label="认证方式",
            ).classes("w-full")

            with ui.column().classes("w-full gap-3").bind_visibility_from(
                self.auth_method, "value", backward=lambda value: value == "dingtalk",
            ):
                self.unified_app_id = ui.input(
                    "Client ID（AppKey / SuiteKey）",
                    value=str(dingtalk["app_id"]) if dingtalk else "",
                ).classes("w-full")
                self.unified_secret = ui.input(
                    "Client Secret（留空则不修改）",
                    password=True, password_toggle_button=True,
                ).classes("w-full")
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label("允许企业")
                    ui.icon("help_outline").classes("text-gray-400 cursor-help").tooltip(
                        "CorpId 或企业名称至少填一项；两项都填时以 CorpId 为准。"
                        "仅填名称需要钉钉“通讯录组织基础信息读权限”。"
                    )
                self.allowed_company_rows = []
                self.allowed_company_container = ui.column().classes("w-full gap-2")
                configured = self.cfg.get("enterprise_sso_allowed_corps") or []
                for company in configured or [{"corp_id": "", "name": ""}]:
                    self._add_allowed_company(
                        str(company.get("corp_id") or ""),
                        str(company.get("name") or ""),
                    )
                ui.button("添加企业", icon="add", on_click=lambda: self._add_allowed_company()).props(
                    "outline no-caps"
                )
                if dingtalk is None and self.providers.legacy_dingtalk_companies():
                    ui.button(
                        "导入旧钉钉配置", icon="upload", on_click=self._import_legacy,
                    ).props("outline").tooltip("导入前请备份数据库。")

            with ui.column().classes("w-full gap-3").bind_visibility_from(
                self.auth_method, "value", backward=lambda value: value == "wecom",
            ):
                self.wecom_corp_id = ui.input(
                    "企业 CorpId", value=str(wecom["enterprise_id"]) if wecom else "",
                ).classes("w-full")
                self.wecom_agent_id = ui.input(
                    "应用 AgentId", value=str(wecom["app_id"]) if wecom else "",
                ).classes("w-full")
                self.wecom_secret = ui.input(
                    "应用 Secret（留空则不修改）",
                    password=True, password_toggle_button=True,
                ).classes("w-full")
        render_form_section(
            "统一认证",
            body=unified_body,
            actions=lambda: primary_action(
                "保存", icon="save", on_click=self._save_unified,
            ),
        )

    def _add_allowed_company(self, corp_id: str = "", name: str = "") -> None:
        with self.allowed_company_container:
            with ui.row().classes("w-full items-start gap-2") as container:
                corp_input = ui.input(
                    "CorpId（可选）", value=corp_id,
                ).classes("flex-1 min-w-0")
                name_input = ui.input(
                    "企业名称（可选）", value=name,
                ).classes("flex-1 min-w-0")
                ui.button(
                    icon="delete_outline",
                    on_click=lambda: self._remove_allowed_company(container),
                ).props("flat round color=negative").classes("mt-2")
        self.allowed_company_rows.append((container, corp_input, name_input))

    def _remove_allowed_company(self, container) -> None:
        self.allowed_company_rows = [
            row for row in self.allowed_company_rows if row[0] is not container
        ]
        container.delete()

    def _save_global(self) -> bool:
        origin = str(self.base_url.value or "").strip().rstrip("/")
        enabled = bool(self.enable.value)
        effective_origin = with_default_ip_api_port(origin) if origin else resolve_sso_origin(load_config())
        if enabled and not _http_origin(effective_origin):
            safe_notify("开启 SSO 前请填写有效的自定义认证地址，或先配置全局访问地址池",
                        type="negative")
            return False
        if origin and not _http_origin(origin):
            safe_notify("公网认证地址格式不正确", type="negative")
            return False
        if enabled:
            try:
                provider = self.providers.first_enabled()
            except Exception:
                safe_notify("提供商密钥无法读取；不能启用企业认证", type="negative")
                return False
            if provider is None:
                safe_notify("请先配置并启用至少一个办公系统认证提供商",
                            type="negative")
                return False
        previous = load_config()
        config = previous.to_dict()
        config.update({
            "enterprise_sso_enabled": enabled,
            "enterprise_sso_all_users": bool(self.all_users.value),
            "enterprise_sso_public_base_url": with_default_ip_api_port(origin) if origin else None,
        })
        try:
            save_config(config)
        except Exception as exc:
            safe_notify(f"企业认证配置保存失败: {exc}", type="negative")
            return False
        safe_notify("认证设置已保存；需重启 NiceVPN 和 OpenVPN 实例才能生效",
                    type="positive", multi_line=True)
        return True

    def _save_unified(self) -> bool:
        """Save the selected authentication method; keep the other credentials."""
        selected = str(self.auth_method.value or "")
        try:
            if selected == "dingtalk":
                corps = parse_allowed_enterprises([
                    {"corp_id": str(corp.value or "").strip(),
                     "name": str(name.value or "").strip()}
                    for _, corp, name in self.allowed_company_rows
                    if str(corp.value or "").strip() or str(name.value or "").strip()
                ])
                if not corps:
                    raise ValueError("至少配置一个企业 CorpId 或企业名称")
                app_id = str(self.unified_app_id.value or "").strip()
                if not app_id:
                    raise ValueError("请填写钉钉 Client ID")
                previous = load_config()
                new_config = previous.to_dict()
                new_config["enterprise_sso_allowed_corps"] = corps
                save_config(new_config)
                try:
                    self.providers.save_unified(
                        app_id=app_id,
                        secret=str(self.unified_secret.value or "") or None,
                    )
                    self.providers.activate_only(UNIFIED_DINGTALK_ID)
                except Exception:
                    save_config(previous.to_dict())
                    raise
                self.unified_secret.value = ""
                self.cfg = load_config()
            elif selected == "wecom":
                corp_id = str(self.wecom_corp_id.value or "").strip()
                agent_id = str(self.wecom_agent_id.value or "").strip()
                if not corp_id or not agent_id:
                    raise ValueError("请填写企业微信 CorpId 和 AgentId")
                self.providers.save_wecom_unified(
                    corp_id=corp_id, agent_id=agent_id,
                    secret=str(self.wecom_secret.value or "") or None,
                )
                self.providers.activate_only(UNIFIED_WECOM_ID)
                self.wecom_secret.value = ""
            else:
                raise ValueError("请选择认证方式")
        except Exception as exc:
            safe_notify(f"统一认证配置保存失败: {exc}", type="negative")
            return False
        if selected == "dingtalk":
            try:
                removed = self.providers.cleanup_unified_legacy()
            except Exception as exc:
                safe_notify(
                    f"配置已保存，但旧凭证清理未完成：{exc}。",
                    type="warning", multi_line=True,
                )
                return True
            safe_notify(
                f"统一认证已保存，已清理 {removed} 条旧应用凭证；重启 NiceVPN 后生效",
                type="positive", multi_line=True,
            )
        else:
            safe_notify("企业微信认证已保存；重启 NiceVPN 后生效", type="positive")
        return True

    def _import_legacy(self) -> bool:
        """Explicit, lossless migration from per-company DingTalk providers."""
        try:
            candidates = self.providers.legacy_dingtalk_companies()
            if not candidates:
                raise ValueError("没有可导入的钉钉企业配置")
            old_cfg = load_config()
            from app.utils.sso_enterprises import parse_allowed_enterprises
            existing = old_cfg.get("enterprise_sso_allowed_corps") or []
            merged = list(existing)
            seen = {row["corp_id"] for row in merged}
            for row in candidates:
                if row["corp_id"] not in seen:
                    merged.append(row)
                    seen.add(row["corp_id"])
            merged = parse_allowed_enterprises(merged)
            cfg = old_cfg.to_dict()
            cfg["enterprise_sso_allowed_corps"] = merged
            save_config(cfg)
            try:
                self.providers.import_legacy_dingtalk()
                self.providers.activate_only(UNIFIED_DINGTALK_ID)
            except Exception:
                save_config(old_cfg.to_dict())
                raise
        except Exception as exc:
            safe_notify(f"旧企业配置导入失败: {exc}", type="negative")
            return False
        try:
            removed = self.providers.cleanup_unified_legacy()
        except Exception as exc:
            safe_notify(
                f"统一凭证及白名单已导入，但旧凭证清理失败：{exc}。"
                "请先检查并重新保存统一认证配置。",
                type="warning", multi_line=True,
            )
            return True
        safe_notify(
            f"已导入统一钉钉凭证及企业白名单，并清理 {removed} 条旧企业凭证；"
            "请刷新页面核对配置，然后重启 NiceVPN",
            type="positive", multi_line=True,
        )
        return True
