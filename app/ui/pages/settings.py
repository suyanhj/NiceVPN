# -*- coding: utf-8 -*-
"""系统设置页面。"""

from __future__ import annotations

import logging
import requests
from nicegui import ui

logger = logging.getLogger(__name__)

from app.core.config import load_config, save_config
from app.core.constants import OPENVPN_DEFAULT_MAX_CLIENTS
from app.models.config import SystemConfig
from app.services.notify.dingtalk import send_dingtalk_text
from app.services.notify.wework import send_wework_text
from app.services.openvpn.detector import validate_custom_path
from app.services.openvpn.installer import _build_github_candidate_urls

_EASYRSA_TEST_URL = "https://github.com/OpenVPN/easy-rsa/releases/download/v3.2.4/EasyRSA-3.2.4.tgz"


class SettingsPage:
    """系统设置页面。"""

    def __init__(self):
        self.config = load_config()

    def render(self) -> None:
        """渲染系统设置页：顶栏 + 紧凑页签（含全局 SSH、下载、通知等）。"""
        self.config = load_config()
        with ui.column().classes("page-shell mgmt-page w-full min-h-0 page-shell--settings"):
            with ui.element("div").classes("mgmt-header-row"):
                with ui.element("div").classes("mgmt-header-copy"):
                    ui.label("系统设置").classes("mgmt-title")
                    ui.label(
                        "分 Tab：OpenVPN 路径、服务端、设备绑定、全局 SSH、下载与通知。"
                    ).classes("mgmt-desc")
            with ui.element("div").classes("firewall-control-header firewall-tab-bar"):
                with ui.tabs(
                    value="openvpn", on_change=self._on_settings_tab_change
                ).classes("firewall-compact-tabs") as st:
                    ui.tab("openvpn", label="OpenVPN")
                    ui.tab("server", label="服务端")
                    ui.tab("device", label="设备绑定")
                    ui.tab("ssh", label="SSH")
                    ui.tab("download", label="下载配置")
                    ui.tab("notify", label="通知")
            with ui.tab_panels(
                st, value="openvpn", keep_alive=False
            ).classes("w-full min-h-0 flex-1 firewall-tabpanels"):
                with ui.tab_panel("openvpn"):
                    with ui.column().classes("w-full min-h-0 flex-1 gap-0"):
                        self._render_openvpn_panel()
                with ui.tab_panel("server"):
                    with ui.column().classes("w-full min-h-0 flex-1 gap-0"):
                        self._render_server_connection_panel()
                with ui.tab_panel("device"):
                    with ui.column().classes("w-full min-h-0 flex-1 gap-0"):
                        self._render_device_bind_panel()
                with ui.tab_panel("ssh"):
                    with ui.column().classes("w-full min-h-0 flex-1 gap-0"):
                        self._render_global_ssh_panel()
                with ui.tab_panel("download"):
                    with ui.column().classes("w-full min-h-0 flex-1 gap-0"):
                        self._render_github_proxy_panel()
                        self._render_download_panel()
                with ui.tab_panel("notify"):
                    with ui.column().classes("w-full min-h-0 flex-1 gap-0"):
                        self._render_notify_panel()

    @staticmethod
    def _on_settings_tab_change(e) -> None:
        v = str(getattr(e, "value", None) or "")
        logger.debug("系统设置页签切换: %s", v)

    def _render_openvpn_panel(self):
        """渲染 OpenVPN 配置面板。"""
        with ui.element("section").classes("settings-panel"):
            with ui.element("div").classes("settings-panel-head"):
                with ui.element("div").classes("settings-panel-copy"):
                    ui.label("OpenVPN").classes("settings-kicker")
                    ui.label("OpenVPN 配置").classes("settings-panel-title")
                    ui.label("可执行文件与 EasyRSA 目录。").classes("settings-panel-desc")

            with ui.element("div").classes("settings-stack"):
                self.openvpn_input = ui.input(
                    "OpenVPN 可执行文件路径",
                    value=self.config.openvpn_bin or "",
                ).classes("w-full")
                self.easyrsa_input = ui.input(
                    "EasyRSA 目录路径",
                    value=self.config.easyrsa_dir or "",
                ).classes("w-full")
                self.path_status = ui.label("").classes("settings-status-text")

            with ui.element("div").classes("settings-toolbar"):
                ui.button("验证路径", on_click=self._validate_path).props("outline no-caps no-ripple").classes(
                    "settings-btn is-outline"
                )
                ui.button("保存", on_click=self._save_openvpn).props("unelevated no-caps no-ripple").classes(
                    "settings-btn is-primary"
                )

    def _render_server_connection_panel(self):
        """渲染服务端连接地址配置面板。"""
        with ui.element("section").classes("settings-panel"):
            with ui.element("div").classes("settings-panel-head"):
                with ui.element("div").classes("settings-panel-copy"):
                    ui.label("Server").classes("settings-kicker")
                    ui.label("服务端连接配置").classes("settings-panel-title")
                    ui.label(
                        "写入 .ovpn 的 remote；保存后新生成的配置用新值。"
                    ).classes("settings-panel-desc")

            with ui.element("div").classes("settings-stack"):
                self.server_ip_input = ui.input(
                    "服务端公网 IP 或域名",
                    value=self.config.server_ip or "",
                    placeholder="如 1.2.3.4 或 vpn.example.com",
                ).classes("w-full")
                with ui.row().classes("w-full gap-sm"):
                    self.server_port_input = ui.number(
                        "端口",
                        value=self.config.port or 1194,
                        min=1, max=65535,
                    ).classes("flex-1")
                    self.server_proto_input = ui.select(
                        {"udp": "UDP", "tcp": "TCP"},
                        label="协议",
                        value=self.config.proto or "udp",
                    ).classes("flex-1")
                self.push_lan_routes_input = ui.textarea(
                    "内网 push route（每行 IPv4 CIDR）",
                    value="\n".join(self.config.get("push_lan_routes") or []),
                    placeholder="每行一条，如 172.16.22.0/24\n勿填 VPN 地址池。",
                ).classes("w-full")
                ui.label(
                    "访问服务端物理网时，中心侧按 VPN 源网段做 MASQUERADE，无需指定出网网卡。"
                ).classes("settings-panel-desc")
                self.max_clients_input = ui.number(
                    "单实例最大并发连接数（max-clients）",
                    value=int(self.config.get("max_clients") or OPENVPN_DEFAULT_MAX_CLIENTS),
                    min=16,
                    max=65534,
                    step=1,
                    format="%.0f",
                ).classes("w-full")
                ui.label(
                    "保存后重写 server.conf；须到服务管理重启 OpenVPN。"
                    " root 可点「写入 sysctl.d 并加载」安装网关 sysctl 模板并 sysctl -p。"
                ).classes("settings-panel-desc")

            with ui.element("div").classes("settings-toolbar"):
                ui.button(
                    "写入 sysctl.d 并加载",
                    on_click=self._install_sysctl_dropin,
                ).props("outline no-caps no-ripple").classes("settings-btn is-outline")
                ui.button("保存", on_click=self._save_server_connection).props(
                    "unelevated no-caps no-ripple"
                ).classes("settings-btn is-primary")

    def _render_device_bind_panel(self):
        """渲染 client-connect 设备绑定策略（与 /etc/openvpn/mgmt/device_bind_mode 同步）。"""
        with ui.element("section").classes("settings-panel"):
            with ui.element("div").classes("settings-panel-head"):
                with ui.element("div").classes("settings-panel-copy"):
                    ui.label("Device bind").classes("settings-kicker")
                    ui.label("设备绑定策略").classes("settings-panel-title")
                    ui.label(
                        "client-connect 指纹策略：A 仅审计；B 分级指纹（HW / UUID / 平台信息）；C 必须 IV_HWADDR。"
                        " 改策略后旧设备可能需重置绑定；保存后对新连接生效。"
                    ).classes("settings-panel-desc")

            with ui.element("div").classes("settings-stack"):
                self.device_bind_mode_input = ui.select(
                    {
                        "weak_log": "A 弱绑定 · 仅记录",
                        "weak_fingerprint": "B 弱指纹 · 分级匹配",
                        "strict_hwaddr": "C 强绑定 · 须 IV_HWADDR",
                    },
                    label="策略",
                    value=self.config.get("device_bind_mode") or "weak_fingerprint",
                ).classes("w-full")

            with ui.element("div").classes("settings-toolbar"):
                ui.button("保存策略", on_click=self._save_device_bind).props(
                    "unelevated no-caps no-ripple"
                ).classes("settings-btn is-primary")

    def _render_global_ssh_panel(self) -> None:
        """全局 SSH 私钥：对端实例未填写私钥时使用。"""
        with ui.element("section").classes("settings-panel"):
            with ui.element("div").classes("settings-panel-head"):
                with ui.element("div").classes("settings-panel-copy"):
                    ui.label("SSH").classes("settings-kicker")
                    ui.label("全局默认私钥").classes("settings-panel-title")
                    ui.label(
                        "对端未填私钥时用此处 PEM；对端已填则优先生效。配置在 data 下，注意文件权限。"
                    ).classes("settings-panel-desc")

            with ui.element("div").classes("settings-stack"):
                self.global_ssh_key_input = ui.textarea(
                    "PEM 私钥全文",
                    value=self.config.get("global_ssh_private_key") or "",
                    placeholder="-----BEGIN OPENSSH PRIVATE KEY----- 或 BEGIN RSA / EC PRIVATE KEY-----",
                ).classes("w-full")
                self.global_ssh_passphrase_input = ui.input(
                    "私钥口令（若密钥有加密）",
                    password=True,
                    password_toggle_button=True,
                    value=self.config.get("global_ssh_private_key_passphrase") or "",
                ).classes("w-full")

            with ui.element("div").classes("settings-toolbar"):
                ui.button("保存", on_click=self._save_global_ssh).props("unelevated no-caps no-ripple").classes(
                    "settings-btn is-primary"
                )

    def _save_global_ssh(self) -> None:
        """持久化全局 SSH 私钥与口令。"""
        data = load_config().to_dict()
        pem = (self.global_ssh_key_input.value or "").strip() or None
        pp = (self.global_ssh_passphrase_input.value or "").strip() or None
        data["global_ssh_private_key"] = pem
        data["global_ssh_private_key_passphrase"] = pp
        try:
            merged = SystemConfig(**data)
        except Exception as exc:
            logger.error("全局 SSH 配置校验失败: %s", exc)
            ui.notify(f"配置校验失败: {exc}", type="negative")
            return
        save_config(merged)
        self.config = load_config()
        logger.info("已保存全局 SSH 私钥配置")
        ui.notify("全局 SSH 已保存", type="positive")

    def _render_github_proxy_panel(self):
        """渲染 GitHub 代理配置面板。"""
        with ui.element("section").classes("settings-panel"):
            with ui.element("div").classes("settings-panel-head"):
                with ui.element("div").classes("settings-panel-copy"):
                    ui.label("Proxy").classes("settings-kicker")
                    ui.label("GitHub 下载代理").classes("settings-panel-title")
                    ui.label("每行一个前缀；装 OpenVPN / EasyRSA 时按序试连。").classes(
                        "settings-panel-desc"
                    )

            with ui.element("div").classes("settings-stack"):
                self.github_proxy_input = ui.textarea(
                    "代理列表",
                    value="\n".join(self.config.github_proxy_urls or []),
                    placeholder="https://gh-proxy.org/\nhttps://cdn.gh-proxy.org/\nhttps://gh.llkk.cc/",
                ).classes("w-full")
                self.proxy_status = ui.label("").classes("settings-status-text")

            with ui.element("div").classes("settings-toolbar"):
                ui.button("测试 EasyRSA 下载", on_click=self._test_easyrsa_download).props(
                    "outline no-caps no-ripple"
                ).classes("settings-btn is-outline is-warn")
                ui.button("保存", on_click=self._save_github_proxies).props("unelevated no-caps no-ripple").classes(
                    "settings-btn is-primary"
                )

    def _render_notify_panel(self):
        """渲染通知：启用与通道选择；选中钉钉或企业微信时显示对应机器人参数表单。"""
        with ui.element("section").classes("settings-panel"):
            with ui.element("div").classes("settings-panel-head"):
                with ui.element("div").classes("settings-panel-copy"):
                    ui.label("Notify").classes("settings-kicker")
                    ui.label("通知推送").classes("settings-panel-title")
                    ui.label(
                        "勾选启用并选择通道后，用户列表「推送下载链接」才会发到对应机器人。"
                    ).classes("settings-panel-desc")

            prov = self.config.get("notify_provider") or "none"
            if prov not in ("none", "dingtalk", "wework"):
                prov = "none"

            with ui.element("div").classes("settings-stack"):
                self.notify_enabled_input = ui.checkbox(
                    "启用推送下载链接",
                    value=bool(self.config.get("notify_enabled")),
                ).classes("w-full")
                self.notify_provider_select = ui.select(
                    {
                        "none": "不使用",
                        "dingtalk": "钉钉机器人",
                        "wework": "企业微信机器人",
                    },
                    label="通知通道",
                    value=prov,
                ).classes("w-full")

            # bind_visibility_from 第二参数须为 "value"，默认 "visible" 会绑错属性导致选中后仍不显示
            with ui.column().classes("w-full gap-0") as dingtalk_fields:
                ui.label("钉钉机器人参数").classes("settings-panel-title q-mt-md q-mb-none")
                ui.label(
                    "钉钉群 → 群设置 → 智能群助手 → 添加机器人 → 自定义，复制 Webhook；"
                    "安全设置勾选「加签」时再填写 SEC。"
                ).classes("settings-panel-desc q-mt-xs q-mb-sm")

                self.webhook_input = ui.input(
                    "Webhook URL",
                    value=self.config.dingtalk_webhook or "",
                    placeholder="https://oapi.dingtalk.com/robot/send?access_token=...",
                ).classes("w-full")
                self.dingtalk_secret_input = ui.input(
                    "加签密钥 (SEC…)",
                    value=self.config.dingtalk_secret or "",
                    password=True,
                    password_toggle_button=True,
                    placeholder="未启用加签可留空",
                ).classes("w-full")

            dingtalk_fields.bind_visibility_from(
                self.notify_provider_select,
                "value",
                value="dingtalk",
            )

            with ui.column().classes("w-full gap-0") as wework_fields:
                ui.label("企业微信机器人参数").classes("settings-panel-title q-mt-md q-mb-none")
                ui.label(
                    "企业微信群 → 群机器人 → 添加 → 复制 Webhook 地址（qyapi.weixin.qq.com/cgi-bin/webhook/send）。"
                ).classes("settings-panel-desc q-mt-xs q-mb-sm")

                self.wework_webhook_input = ui.input(
                    "Webhook URL（企业微信）",
                    value=self.config.get("wework_webhook") or "",
                    placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...",
                ).classes("w-full")

            wework_fields.bind_visibility_from(
                self.notify_provider_select,
                "value",
                value="wework",
            )

            with ui.row().classes("settings-toolbar") as dingtalk_actions:
                ui.button("发送测试消息（钉钉）", on_click=self._test_webhook).props(
                    "outline no-caps no-ripple"
                ).classes("settings-btn is-outline")

            dingtalk_actions.bind_visibility_from(
                self.notify_provider_select,
                "value",
                value="dingtalk",
            )

            with ui.row().classes("settings-toolbar") as wework_actions:
                ui.button("发送测试消息（企业微信）", on_click=self._test_wework_webhook).props(
                    "outline no-caps no-ripple"
                ).classes("settings-btn is-outline")

            wework_actions.bind_visibility_from(
                self.notify_provider_select,
                "value",
                value="wework",
            )

            with ui.element("div").classes("settings-toolbar"):
                ui.button("保存通知配置", on_click=self._save_notify).props(
                    "unelevated no-caps no-ripple"
                ).classes("settings-btn is-primary")

    def _render_download_panel(self):
        """渲染下载链接配置面板。"""
        with ui.element("section").classes("settings-panel"):
            with ui.element("div").classes("settings-panel-head"):
                with ui.element("div").classes("settings-panel-copy"):
                    ui.label("Download").classes("settings-kicker")
                    ui.label("下载链接").classes("settings-panel-title")
                    ui.label(
                        "留空按当前访问自动生成；localhost 时用启动解析的本机 IP。反代或固定域名在此覆盖。"
                    ).classes("settings-panel-desc")

            with ui.element("div").classes("settings-stack"):
                self.base_url_input = ui.input(
                    "基础 URL（可选）",
                    value=self.config.download_base_url or "",
                    placeholder="留空自动；例 https://vpn.example.com",
                ).classes("w-full")

            with ui.element("div").classes("settings-toolbar"):
                ui.button("保存", on_click=self._save_base_url).props("unelevated no-caps no-ripple").classes(
                    "settings-btn is-primary"
                )

    def _validate_path(self):
        """验证 OpenVPN 自定义路径。"""
        path = self.openvpn_input.value
        if not path:
            self.path_status.text = "请输入路径"
            return
        result = validate_custom_path(path)
        if result["valid"]:
            self.path_status.text = f"验证通过，版本 {result['version']}"
            ui.notify("路径验证通过", type="positive")
        else:
            self.path_status.text = result.get("error", "路径无效")
            ui.notify(f"验证失败: {result.get('error')}", type="negative")

    def _save_openvpn(self):
        """保存 OpenVPN 路径配置。"""
        config = load_config()
        config.openvpn_bin = self.openvpn_input.value or None
        config.easyrsa_dir = self.easyrsa_input.value or None
        save_config(config)
        ui.notify("OpenVPN 配置已保存", type="positive")

    def _test_easyrsa_download(self):
        """测试通过代理链路下载 EasyRSA 资源。"""
        lines = [line.strip() for line in (self.github_proxy_input.value or "").splitlines() if line.strip()]
        config = load_config()
        config.github_proxy_urls = lines
        save_config(config)

        candidates = _build_github_candidate_urls(_EASYRSA_TEST_URL)
        last_error: str | None = None

        for candidate in candidates:
            try:
                response = requests.get(
                    candidate,
                    headers={"Range": "bytes=0-0", "User-Agent": "openvpn-mgmt-settings-test"},
                    stream=True,
                    timeout=20,
                    allow_redirects=True,
                )
                if response.status_code not in (200, 206):
                    last_error = f"HTTP {response.status_code}"
                    continue

                content_length = response.headers.get("Content-Length") or response.headers.get("content-length") or "未知"
                self.proxy_status.text = f"EasyRSA 下载成功：{candidate}，长度 {content_length}"
                ui.notify("EasyRSA 下载成功", type="positive")
                return
            except Exception as exc:
                last_error = str(exc)

        self.proxy_status.text = f"EasyRSA 下载失败: {last_error or '未知'}"
        ui.notify(f"下载失败: {last_error or '未知'}", type="negative")

    def _save_github_proxies(self):
        """保存 GitHub 下载代理。"""
        config = load_config()
        lines = [line.strip() for line in (self.github_proxy_input.value or "").splitlines()]
        config.github_proxy_urls = [line for line in lines if line]
        save_config(config)
        ui.notify("代理列表已保存", type="positive")

    def _save_notify(self):
        """保存通知开关、通道与各机器人 Webhook。"""
        data = load_config().to_dict()
        data["notify_enabled"] = bool(self.notify_enabled_input.value)
        prov = self.notify_provider_select.value or "none"
        data["notify_provider"] = prov if prov in ("none", "dingtalk", "wework") else "none"
        data["dingtalk_webhook"] = (self.webhook_input.value or "").strip() or None
        data["dingtalk_secret"] = (self.dingtalk_secret_input.value or "").strip() or None
        data["wework_webhook"] = (self.wework_webhook_input.value or "").strip() or None
        try:
            merged = SystemConfig(**data)
        except Exception as exc:
            logger.error("通知配置校验失败: %s", exc)
            ui.notify(f"配置校验失败: {exc}", type="negative")
            return
        save_config(merged)
        self.config = load_config()
        ui.notify("通知配置已保存", type="positive")

    def _test_webhook(self):
        """发送测试消息到钉钉（与正式推送相同，走 dingtalkchatbot）。"""
        url = (self.webhook_input.value or "").strip()
        if not url:
            ui.notify("请填写 Webhook", type="warning")
            return
        secret = (self.dingtalk_secret_input.value or "").strip() or None
        try:
            data = send_dingtalk_text(
                url,
                secret,
                "【VPN 管理】钉钉测试成功",
            )
            if data.get("errcode") == 0:
                ui.notify("测试已发送", type="positive")
            else:
                ui.notify(f"发送失败: {data.get('errmsg')}", type="negative")
        except ValueError as exc:
            ui.notify(f"参数无效: {exc}", type="negative")
        except Exception as exc:
            ui.notify(f"发送失败: {exc}", type="negative")

    def _test_wework_webhook(self) -> None:
        """发送测试消息到企业微信群机器人。"""
        url = (self.wework_webhook_input.value or "").strip()
        if not url:
            ui.notify("请填写企业微信 Webhook", type="warning")
            return
        try:
            data = send_wework_text(url, "【VPN 管理】企业微信机器人测试成功")
            if int(data.get("errcode", -1)) == 0:
                ui.notify("测试已发送", type="positive")
            else:
                ui.notify(f"发送失败: {data.get('errmsg', data)}", type="negative")
        except ValueError as exc:
            ui.notify(f"参数无效: {exc}", type="negative")
        except Exception as exc:
            ui.notify(f"发送失败: {exc}", type="negative")

    def _save_base_url(self):
        """保存下载链接基础 URL。"""
        config = load_config()
        config.download_base_url = self.base_url_input.value or None
        save_config(config)
        ui.notify("下载基础 URL 已保存", type="positive")

    def _install_sysctl_dropin(self):
        """将仓库内 sysctl 模板安装到 /etc/sysctl.d/ 并 sysctl -p 立即加载。"""
        try:
            from app.utils.sysctl_tune import install_vpn_sysctl_dropin

            install_vpn_sysctl_dropin()
        except Exception as exc:
            logger.exception("安装 sysctl.d 失败")
            ui.notify(f"sysctl.d 安装失败: {exc}", type="negative")
            return
        ui.notify("sysctl.d 已安装并已加载", type="positive")

    def _save_server_connection(self):
        """保存服务端连接与内网 push 路由，并重写 server.conf、刷新 iptables（NAT 随 global_subnet 自动 MASQUERADE）。"""
        from app.services.firewall.rule_service import FirewallRuleService
        from app.services.openvpn.instance import regenerate_all_server_confs

        server_ip = (self.server_ip_input.value or "").strip()
        if not server_ip:
            ui.notify("服务端地址必填", type="negative")
            return
        lines_push = [
            x.strip() for x in (self.push_lan_routes_input.value or "").splitlines() if x.strip()
        ]
        data = load_config().to_dict()
        data["server_ip"] = server_ip
        data["port"] = int(self.server_port_input.value or 1194)
        data["proto"] = self.server_proto_input.value or "udp"
        data["push_lan_routes"] = lines_push
        data["max_clients"] = int(self.max_clients_input.value or OPENVPN_DEFAULT_MAX_CLIENTS)
        try:
            merged = SystemConfig(**data)
        except Exception as exc:
            ui.notify(f"配置校验失败: {exc}", type="negative")
            return
        save_config(merged)
        try:
            regenerate_all_server_confs()
        except Exception as exc:
            ui.notify(f"重写 server.conf 失败: {exc}", type="negative")
            return
        try:
            FirewallRuleService().refresh_vpn_forward_only()
        except Exception as exc:
            ui.notify(f"VPN_FORWARD 刷新失败: {exc}", type="negative")
            return
        ui.notify(
            "已保存；server.conf 与 VPN_FORWARD 已更新。请到服务管理重启实例并让客户端重连。",
            type="positive",
        )

    def _save_device_bind(self):
        """保存设备绑定策略并写入 OpenVPN 运行时文件。"""
        from app.services.user.device_bind_policy import sync_device_bind_mode_file

        data = load_config().to_dict()
        data["device_bind_mode"] = self.device_bind_mode_input.value or "weak_fingerprint"
        try:
            merged = SystemConfig(**data)
        except Exception as exc:
            ui.notify(f"配置校验失败: {exc}", type="negative")
            return
        save_config(merged)
        try:
            sync_device_bind_mode_file(merged.device_bind_mode)
        except OSError as exc:
            ui.notify(f"写入 device_bind_mode 失败（需 Linux、可写 /etc/openvpn/mgmt）: {exc}", type="negative")
            return
        ui.notify("设备绑定已保存，新连接按新模式", type="positive")
