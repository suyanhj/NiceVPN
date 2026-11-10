# -*- coding: utf-8 -*-
"""Enterprise SSO settings for NiceGUI admin, never mounted on Public API."""
from __future__ import annotations

from urllib.parse import urlsplit

from nicegui import ui

from app.core.config import load_config, save_config
from app.services.auth.provider_store import SsoProviderStore, UNIFIED_DINGTALK_ID
from app.utils.public_domains import resolve_sso_origin, with_default_ip_api_port
from app.utils.sso_enterprises import parse_allowed_enterprises, format_allowed_enterprises
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
                    "开启企业身份认证（全局总开关）",
                    value=bool(self.cfg.get("enterprise_sso_enabled", False)),
                ),
                "只有启用企业认证的组或标签才进行 SSO；其他连接继续原有流程。"
                "企业认证成功后仅记录设备，不执行设备绑定校验。"
                "启用或关闭需重启 NiceVPN 及 OpenVPN 实例。",
                aria_label="企业认证总开关说明",
                control_grow=False,
            )
            self.base_url = field_with_help(
                lambda: ui.input(
                    "认证地址（HTTP / HTTPS）",
                    value=str(self.cfg.get("enterprise_sso_public_base_url") or ""),
                    placeholder=resolve_sso_origin(self.cfg) or "http://192.168.1.10:8881",
                ).classes("w-full"),
                "地址优先级：自动获取 < 全局访问地址 < 自定义。"
                "域名默认使用 80/443；IP 未指定端口自动使用 API 端口。"
                "正式启用前需验证 OAuth 回调和客户端 WebAuth 兼容性。",
                aria_label="企业认证地址说明",
            )
        render_form_section(
            "企业认证总开关",
            kicker="Enterprise SSO",
            description="通过办公系统核验企业成员，VPN 网络权限仍由证书、组和标签控制。",
            body=global_body,
            actions=lambda: primary_action(
                "保存认证设置", icon="save", on_click=self._save_global,
            ),
        )

        def unified_body() -> None:
            metadata = next(
                (row for row in self.providers.list_metadata()
                 if row["id"] == UNIFIED_DINGTALK_ID), None,
            )
            self.unified_app_id = field_with_help(
                lambda: ui.input(
                    "全局钉钉 Client ID（AppKey / SuiteKey）",
                    value=str(metadata["app_id"]) if metadata else "",
                    placeholder="只需要配置一套应用 Client ID",
                ).classes("w-full"),
                "企业内部应用使用 AppKey；第三方企业应用使用 SuiteKey。"
                "所有允许的企业共用这套 OAuth 应用凭证。",
                aria_label="统一钉钉应用标识说明",
            )
            self.unified_secret = field_with_help(
                lambda: ui.input(
                    "全局 Client Secret（已保存时留空不修改）",
                    password=True, password_toggle_button=True,
                ).classes("w-full"),
                "只会保存在本地加密存储中，不显示在企业白名单或认证记录中。",
                aria_label="统一钉钉应用密钥说明",
            )
            self.unified_profile = field_with_help(
                lambda: ui.switch(
                    "尝试读取个人资料（可选）",
                    value=bool(metadata["read_user_profile"]) if metadata else False,
                ),
                "需要在钉钉后台开通 Contact.User.Read 个人资料接口权限。"
                "登录 Scope 保持钉钉官方支持的 openid corpid，"
                "仅在开启此项时尝试读取 users/me；失败不影响企业 CorpId 验证。",
                aria_label="个人资料读取说明",
                control_grow=False,
            )
            self.allowed_corps = field_with_help(
                lambda: ui.textarea(
                    "允许登录的企业（每行：CorpId | 企业名称）",
                    value=format_allowed_enterprises(
                        self.cfg.get("enterprise_sso_allowed_corps") or []
                    ),
                    placeholder="dingxxxxxxxxxx | 广州公司\\ndingyyyyyyyyyy | 深圳公司",
                ).classes("w-full").props("autogrow"),
                "必须填写钉钉真实 CorpId；企业名称可省略，仅用于界面展示。"
                "只认钉钉服务端返回的 CorpId，不支持按企业名称模糊放行。"
                "配置保存后生效，无须为每家公司单独添加 AppKey/AppSecret。",
                aria_label="允许的企业白名单说明",
                align="start",
            )
            if metadata is None and self.providers.legacy_dingtalk_companies():
                ui.button(
                    "从旧多企业配置导入", icon="upload",
                    on_click=self._import_legacy,
                ).props("outline").tooltip(
                    "只复制第一套已启用钉钉应用凭证，并导入旧企业 CorpId 白名单；"
                    "保留所有旧配置，导入完成后刷新页面查看。"
                )
        render_form_section(
            "统一钉钉身份认证",
            kicker="Unified DingTalk OAuth",
            description=(
                "全局只需配置一套钉钉 OAuth Client ID/Secret；"
                "企业管理仅维护 CorpId 白名单。登录方式由钉钉官方页面提供。"
            ),
            body=unified_body,
            actions=lambda: primary_action(
                "保存统一认证配置", icon="save", on_click=self._save_unified,
            ),
        )

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
        """Enable the unified provider only after the company allow-list is saved."""
        app_id = str(self.unified_app_id.value or "").strip()
        secret = str(self.unified_secret.value or "") or None
        try:
            corps = parse_allowed_enterprises(str(self.allowed_corps.value or ""))
            if not corps:
                raise ValueError("至少配置一个允许登录的企业 CorpId")
            if not app_id:
                raise ValueError("请填写钉钉全局 Client ID")
            previous = load_config()
            new_config = previous.to_dict()
            new_config["enterprise_sso_allowed_corps"] = corps
            save_config(new_config)
            try:
                self.providers.save_unified(
                    app_id=app_id, secret=secret,
                    read_user_profile=bool(self.unified_profile.value),
                )
            except Exception:
                save_config(previous.to_dict())
                raise
            self.cfg = load_config()
            self.unified_secret.value = ""
        except Exception as exc:
            safe_notify(f"统一钉钉配置保存失败: {exc}", type="negative")
            return False
        safe_notify(
            "统一认证配置已保存；请重启 NiceVPN，使新的 WebAuth 连接使用统一钉钉登录",
            type="positive", multi_line=True,
        )
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
            except Exception:
                save_config(old_cfg.to_dict())
                raise
        except Exception as exc:
            safe_notify(f"旧企业配置导入失败: {exc}", type="negative")
            return False
        safe_notify(
            "已导入第一套钉钉凭证及旧企业白名单，原多企业凭证保留备份；"
            "请刷新页面核对配置，然后重启 NiceVPN",
            type="positive", multi_line=True,
        )
        return True
