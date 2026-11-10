# -*- coding: utf-8 -*-
"""Enterprise SSO settings for NiceGUI admin, never mounted on Public API."""
from __future__ import annotations

from urllib.parse import urlsplit

from nicegui import ui

from app.core.config import load_config, save_config
from app.services.auth.enterprise_sso_policy import SsoPolicyService
from app.services.auth.provider_store import SsoProviderStore
from app.ui.components.actions import primary_action
from app.ui.components.feedback import safe_notify
from app.ui.components.form_section import render_form_section


def _https_origin(value: str) -> bool:
    p = urlsplit(value)
    return bool(
        p.scheme == "https" and p.netloc and not p.username and not p.password
        and not p.path and not p.query and not p.fragment
    )


class SsoSettingsPanel:
    """Reusable admin-only SSO panel consistent with V2 settings sections."""

    def __init__(self, parent):
        self.parent = parent
        self.cfg = load_config()
        self.providers = SsoProviderStore()
        self.policy = SsoPolicyService()

    def render(self) -> None:
        def global_body():
            self.enable = ui.switch(
                "开启企业身份认证（全局总开关）",
                value=bool(self.cfg.get("enterprise_sso_enabled", False)),
            )
            self.base_url = ui.input(
                "公网 HTTPS 认证地址",
                value=str(self.cfg.get("enterprise_sso_public_base_url") or ""),
                placeholder="https://vpn.example.com",
            ).classes("w-full")
            ui.label(
                "默认关闭；保存后需要重启 NiceVPN 和 OpenVPN 实例。"
                "正式启用前必须通过 POC 和客户端兼容测试。"
                "Linux CLI 如需免 SSO，请在下面明确配置证书豁免。"
            ).classes("text-caption text-grey-6")
        render_form_section(
            "企业认证总开关",
            kicker="Enterprise SSO",
            description="通过办公系统核验企业成员，VPN 网络权限仍由证书、组和标签控制。",
            body=global_body,
            actions=lambda: primary_action(
                "保存认证设置", icon="save", on_click=self._save_global,
            ),
        )

        def provider_body():
            records = self.providers.list_metadata()
            for item in records:
                with ui.row().classes("w-full items-center gap-3"):
                    ui.label(f"{item['id']} · {item['kind']} · {item['enterprise_id']}")
                    ui.badge("启用" if item["enabled"] else "关闭")
            self.provider_id = ui.input(
                "提供商标识（新增或更新）", placeholder="ding-main",
            ).classes("w-full")
            self.provider_kind = ui.select(
                {"dingtalk": "钉钉", "wecom": "企业微信"},
                value="dingtalk", label="身份提供商",
            ).classes("w-full")
            self.enterprise_id = ui.input(
                "允许登录的企业 CorpId", placeholder="企业标识",
            ).classes("w-full")
            self.app_id = ui.input(
                "应用 AppKey / AgentId", placeholder="办公系统应用 ID",
            ).classes("w-full")
            self.app_secret = ui.input(
                "AppSecret（更新时可留空）",
                password=True, password_toggle_button=True,
            ).classes("w-full")
            self.provider_enabled = ui.switch("启用当前身份提供商", value=False)
            ui.label(
                "Secret 只加密保存，不显示在列表；钉钉通知 Webhook 与 SSO AppSecret 完全独立。"
            ).classes("text-caption text-grey-6")
        render_form_section(
            "身份提供商",
            kicker="Identity providers",
            description="钉钉和企业微信支持多个企业应用。填写前需在办公系统配置 OAuth 回调地址。",
            body=provider_body,
            actions=lambda: primary_action(
                "保存提供商", icon="save", on_click=self._save_provider,
            ),
        )

        def policy_body():
            groups = self.policy.store.list_groups()
            tags = self.policy.store.list_tags()
            group_choices = {
                str(group["id"]): str(group.get("name") or group["id"])
                for group in groups
            }
            tag_choices = {
                str(tag["id"]): str(tag.get("name") or tag["id"])
                for tag in tags
            }
            self.group = ui.select(group_choices, label="选择用户组").classes("w-full")
            self.group_required = ui.switch("该组强制企业认证", value=False)
            self.group.on_value_change(
                lambda e: self._sync_group_switch(str(e.value or ""))
            )
            self.tag = ui.select(tag_choices, label="选择标签").classes("w-full")
            self.tag_required = ui.switch("该标签强制企业认证", value=False)
            self.tag.on_value_change(
                lambda e: self._sync_tag_switch(str(e.value or ""))
            )
            ui.label(
                "组和标签任一强制认证即生效；证书豁免优先。"
            ).classes("text-caption text-grey-6")
        render_form_section(
            "组和标签认证策略",
            kicker="Auth policies",
            description="只决定当前证书是否必须完成 SSO，VPN 防火墙规则不受影响。",
            body=policy_body,
            actions=lambda: primary_action(
                "保存组/标签策略", icon="save", on_click=self._save_policies,
            ),
        )

        def exemption_body():
            self.cert = ui.input(
                "证书 CN（必须存在于 NiceVPN）", placeholder="server-linux-01",
            ).classes("w-full")
            self.exempt = ui.switch("免企业认证", value=True)
            self.exempt_reason = ui.input(
                "豁免原因（开启豁免时必填）", placeholder="Linux OpenVPN 2.x 命令行",
            ).classes("w-full")
            ui.label(
                "免 SSO 只对该证书生效，与操作系统无关；"
                "对同一证书在其他系统上使用也将免认证。"
            ).classes("text-caption text-grey-6")
        render_form_section(
            "证书例外",
            kicker="Certificate exceptions",
            description="为 Linux 命令行等无法 WebAuth 的证书明确豁免，禁止按 IV_PLAT 自动放行。",
            body=exemption_body,
            actions=lambda: primary_action(
                "保存证书例外", icon="save", on_click=self._save_exemption,
            ),
        )

        def audit_body():
            from app.services.auth.sso_audit import SsoAudit
            events = SsoAudit(self.policy.store).recent(limit=30)
            if not events:
                ui.label("暂无企业身份认证记录").classes("text-caption text-grey-6")
                return
            columns = [
                {"name": "event_time", "label": "时间", "field": "event_time", "align": "left"},
                {"name": "common_name", "label": "证书 CN", "field": "common_name", "align": "left"},
                {"name": "provider_kind", "label": "身份系统", "field": "provider_kind"},
                {"name": "enterprise_id", "label": "企业 ID", "field": "enterprise_id"},
                {"name": "event_type", "label": "事件", "field": "event_type"},
                {"name": "outcome", "label": "结果", "field": "outcome"},
            ]
            ui.table(columns=columns, rows=events, row_key="event_time", pagination=10).classes("w-full")
        render_form_section(
            "最近认证记录",
            kicker="SSO Audit",
            description="记录证书与企业授权结果；不存储访问令牌、授权码或办公系统密码。",
            body=audit_body,
        )

    def _save_global(self) -> bool:
        origin = str(self.base_url.value or "").strip().rstrip("/")
        enabled = bool(self.enable.value)
        if enabled and not _https_origin(origin):
            safe_notify("开启企业 SSO 前必须配置 HTTPS 公网认证地址，且不包含路径或查询参数",
                        type="negative")
            return False
        if origin and not _https_origin(origin):
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
            "enterprise_sso_public_base_url": origin,
        })
        try:
            save_config(config)
        except Exception as exc:
            safe_notify(f"企业认证配置保存失败: {exc}", type="negative")
            return False
        safe_notify("认证设置已保存；需重启 NiceVPN 和 OpenVPN 实例才能生效",
                    type="positive", multi_line=True)
        return True

    def _save_provider(self) -> bool:
        try:
            self.providers.save(
                provider_id=str(self.provider_id.value or "").strip(),
                kind=str(self.provider_kind.value or "dingtalk"),
                enterprise_id=str(self.enterprise_id.value or "").strip(),
                app_id=str(self.app_id.value or "").strip(),
                secret=str(self.app_secret.value or "") or None,
                enabled=bool(self.provider_enabled.value),
            )
        except Exception as exc:
            safe_notify(f"提供商配置保存失败: {exc}", type="negative")
            return False
        self.app_secret.value = ""
        safe_notify("身份提供商已保存，刷新设置页可查看最新列表", type="positive")
        return True

    def _sync_group_switch(self, group_id: str) -> None:
        if not group_id:
            return
        group = self.policy.store.get_group(group_id)
        if group is not None:
            self.group_required.value = bool(group.get("sso_required", False))

    def _sync_tag_switch(self, tag_id: str) -> None:
        if not tag_id:
            return
        tag = self.policy.store.get_tag(tag_id)
        if tag is not None:
            self.tag_required.value = bool(tag.get("sso_required", False))

    def _save_policies(self) -> bool:
        try:
            if self.group.value:
                self.policy.set_group_required(
                    str(self.group.value), required=bool(self.group_required.value),
                )
            if self.tag.value:
                self.policy.set_tag_required(
                    str(self.tag.value), required=bool(self.tag_required.value),
                )
        except Exception as exc:
            safe_notify(f"认证策略保存失败: {exc}", type="negative")
            return False
        safe_notify("认证策略已更新", type="positive")
        return True

    def _save_exemption(self) -> bool:
        try:
            self.policy.set_certificate_exemption(
                str(self.cert.value or ""), exempt=bool(self.exempt.value),
                reason=str(self.exempt_reason.value or ""),
            )
        except Exception as exc:
            safe_notify(f"证书豁免保存失败: {exc}", type="negative")
            return False
        safe_notify("证书认证例外已更新", type="positive")
        return True
