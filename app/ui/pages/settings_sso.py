# -*- coding: utf-8 -*-
"""Enterprise SSO settings for NiceGUI admin, never mounted on Public API."""
from __future__ import annotations

from urllib.parse import urlsplit

from nicegui import ui

from app.core.config import load_config, save_config
from app.services.auth.provider_store import SsoProviderStore
from app.utils.public_domains import first_domain_origin, resolve_sso_origin, with_default_ip_api_port
from app.ui.components.actions import primary_action
from app.ui.components.feedback import safe_notify
from app.ui.components.form_section import render_form_section


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
            self.enable = ui.switch(
                "开启企业身份认证（全局总开关）",
                value=bool(self.cfg.get("enterprise_sso_enabled", False)),
            )
            self.base_url = ui.input(
                "认证地址（HTTP / HTTPS）",
                value=str(self.cfg.get("enterprise_sso_public_base_url") or ""),
                placeholder=resolve_sso_origin(self.cfg) or "http://192.168.1.10:8881",
            ).classes("w-full")
            ui.label(
                "默认关闭；保存后需要重启 NiceVPN 和 OpenVPN 实例。"
                "正式启用前必须通过 POC 和客户端兼容测试。"
                "只有组或标签开启企业认证的连接才需要 SSO，其余证书行为不变。"
                "地址优先级：自动获取 < 全局访问地址 < 自定义；域名走标准 80/443 端口，IP 未指定端口会补 API 端口。"
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
            self._provider_meta = {str(item["id"]): item for item in records}
            if records:
                options = {
                    str(item["id"]): (
                        f'{item["display_name"]} · '
                        f'{"钉钉" if item["kind"] == "dingtalk" else "企业微信"} · '
                        f'{"启用" if item["enabled"] else "停用"}'
                    )
                    for item in records
                }
                self.existing_provider = ui.select(
                    options, label="已配置企业（选择后编辑）",
                ).classes("w-full")
                self.existing_provider.on_value_change(
                    lambda e: self._load_existing_provider(str(e.value or ""))
                )
            else:
                ui.label("尚未添加企业，可为集团内每个公司独立配置身份应用。").classes(
                    "text-caption text-grey-6"
                )

            ui.button("新增企业", icon="add", on_click=self._reset_provider_form).props("outline")
            self.provider_id = ui.input(
                "企业配置标识（唯一）", placeholder="gz_branch",
            ).classes("w-full")
            self.display_name = ui.input(
                "企业名称（员工登录时显示）", placeholder="广州分公司",
            ).classes("w-full")
            self.provider_kind = ui.select(
                {"dingtalk": "钉钉", "wecom": "企业微信"},
                value="dingtalk", label="身份提供商",
            ).classes("w-full")
            self.enterprise_id = ui.input(
                "企业 CorpId", placeholder="当前公司的企业标识",
            ).classes("w-full")
            self.app_id = ui.input(
                "应用 AppKey / AgentId", placeholder="该公司的办公系统应用 ID",
            ).classes("w-full")
            self.app_secret = ui.input(
                "AppSecret（编辑时留空表示不修改）",
                password=True, password_toggle_button=True,
            ).classes("w-full")
            self.provider_enabled = ui.switch("启用该企业身份认证", value=False)
            self.read_user_profile = ui.switch(
                "读取钉钉员工个人资料（需先开通 Contact.User.Read）", value=False,
            )
            ui.label(
                "未开通权限前保持关闭，仅验证企业 CorpId；开启后登录时额外请求 "
                "Contact.User.Read，可记录返回的 UnionId/OpenId。"
            ).classes("text-caption text-grey-6")
            ui.label(
                "集团可添加多个企业；每家企业的 CorpId、AppKey/AgentId 和 Secret 独立管理。"
                "仅通过启用的企业验证员工身份，不会映射 VPN 用户。"
            ).classes("text-caption text-grey-6")
        render_form_section(
            "集团企业身份配置",
            kicker="Identity providers",
            description="每个子公司或分公司各建一条独立认证应用；员工可从所有启用的企业中选择登录。",
            body=provider_body,
            actions=lambda: primary_action(
                "保存提供商", icon="save", on_click=self._save_provider,
            ),
        )

        def audit_body():
            from app.services.auth.sso_audit import SsoAudit
            events = SsoAudit().recent(limit=30)
            if not events:
                ui.label("暂无企业身份认证记录").classes("text-caption text-grey-6")
                return
            columns = [
                {"name": "event_time", "label": "时间", "field": "event_time", "align": "left"},
                {"name": "common_name", "label": "证书 CN", "field": "common_name", "align": "left"},
                {"name": "provider_kind", "label": "身份系统", "field": "provider_kind"},
                {"name": "enterprise_id", "label": "企业 ID", "field": "enterprise_id"},
                {"name": "external_user_id", "label": "外部用户 ID", "field": "external_user_id"},
                {"name": "reason_code", "label": "原因代码", "field": "reason_code"},
                {"name": "event_type", "label": "事件", "field": "event_type"},
                {"name": "outcome", "label": "结果", "field": "outcome"},
            ]
            ui.table(columns=columns, rows=events, row_key="event_time", pagination=10).classes("w-full")
        render_form_section(
            "最近认证记录",
            kicker="SSO Audit",
            description="记录证书 CN、企业 ID、外部用户 ID 及认证结果。若钉钉用户资料接口 403，外部用户 ID 可能为空，但已核对的企业 ID 仍可查看。不会记录 Token 和密钥。",
            body=audit_body,
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

    def _load_existing_provider(self, provider_id: str) -> None:
        record = self._provider_meta.get(provider_id)
        if record is None:
            return
        self.provider_id.value = record["id"]
        self.display_name.value = record.get("display_name") or record["id"]
        self.provider_kind.value = record["kind"]
        self.enterprise_id.value = record["enterprise_id"]
        self.app_id.value = record["app_id"]
        self.app_secret.value = ""
        self.provider_enabled.value = bool(record["enabled"])
        self.read_user_profile.value = bool(record.get("read_user_profile", False))

    def _reset_provider_form(self) -> None:
        self.provider_id.value = ""
        self.display_name.value = ""
        self.provider_kind.value = "dingtalk"
        self.enterprise_id.value = ""
        self.app_id.value = ""
        self.app_secret.value = ""
        self.provider_enabled.value = False
        self.read_user_profile.value = False
        if hasattr(self, "existing_provider"):
            self.existing_provider.value = None

    def _save_provider(self) -> bool:
        try:
            self.providers.save(
                provider_id=str(self.provider_id.value or "").strip(),
                kind=str(self.provider_kind.value or "dingtalk"),
                enterprise_id=str(self.enterprise_id.value or "").strip(),
                app_id=str(self.app_id.value or "").strip(),
                secret=str(self.app_secret.value or "") or None,
                enabled=bool(self.provider_enabled.value),
                display_name=str(self.display_name.value or "").strip(),
                read_user_profile=(bool(self.read_user_profile.value)
                                   if str(self.provider_kind.value) == "dingtalk" else False),
            )
        except Exception as exc:
            safe_notify(f"提供商配置保存失败: {exc}", type="negative")
            return False
        self.app_secret.value = ""
        safe_notify("企业身份配置已保存；刷新页面后可在列表中查看或编辑", type="positive")
        return True
