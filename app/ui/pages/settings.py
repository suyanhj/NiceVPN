# -*- coding: utf-8 -*-
"""系统设置页面。"""

from __future__ import annotations

import logging
import requests
from nicegui import run, ui

logger = logging.getLogger(__name__)

from app.core.config import load_config, save_config
from app.core.constants import OPENVPN_DEFAULT_MAX_CLIENTS
from app.models.config import SystemConfig
from app.services.notify.dingtalk import send_dingtalk_text
from app.services.notify.rocketchat import send_rocketchat_text
from app.services.notify.wework import send_wework_text
from app.services.openvpn.detector import validate_custom_path
from app.ui.adapters.settings import (
    build_device_bind_config,
    build_download_config,
    build_github_candidate_urls,
    build_global_ssh_config,
    build_notify_config,
    build_openvpn_path_config,
    build_server_connection_config,
    parse_github_proxy_urls,
    parse_rocketchat_channels,
    validate_easyrsa_script_path,
)
from app.ui.components.actions import primary_action, secondary_action
from app.ui.components.feedback import safe_notify
from app.ui.components.form_section import render_form_section
from app.ui.components.page_header import render_page_header

_EASYRSA_TEST_URL = "https://github.com/OpenVPN/easy-rsa/releases/download/v3.2.4/EasyRSA-3.2.4.tgz"


class SettingsPage:
    """系统设置页面。"""

    def __init__(self):
        self.config = load_config()
        self._proxy_test_running = False
        self._notify_test_running = False

    def render(self) -> None:
        """渲染系统设置页：顶栏 + 紧凑页签（含全局 SSH、下载、通知等）。"""
        self.config = load_config()
        with ui.column().classes(
            "page-shell mgmt-page w-full min-h-0 page-shell--settings"
        ):
            render_page_header(
                "系统设置",
                description=(
                    "集中配置 OpenVPN、服务端、设备策略、SSH、下载与通知能力。"
                ),
            )
            with ui.element("div").classes(
                "firewall-control-header firewall-tab-bar w-full"
            ):
                with ui.tabs(
                    value="openvpn",
                    on_change=self._on_settings_tab_change,
                ).classes("firewall-compact-tabs w-full") as st:
                    self._settings_tabs = st
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
        """渲染 OpenVPN / EasyRSA 路径配置。"""

        def body() -> None:
            self.openvpn_input = ui.input(
                "OpenVPN 可执行文件路径",
                value=self.config.openvpn_bin or "",
            ).classes("w-full")
            self.easyrsa_input = ui.input(
                "EasyRSA 可执行脚本路径",
                value=self.config.easyrsa_dir or "",
            ).classes("w-full")
            self.path_status = ui.label(
                "保存前建议先验证两个路径。"
            ).classes("settings-status-text")

        def actions() -> None:
            secondary_action(
                "验证路径",
                icon="fact_check",
                on_click=self._validate_path,
            )
            primary_action(
                "保存",
                icon="save",
                on_click=self._save_openvpn,
            )

        render_form_section(
            "OpenVPN 配置",
            kicker="OpenVPN",
            description="配置 OpenVPN 可执行文件与 EasyRSA 官方脚本路径。",
            body=body,
            actions=actions,
        )

    def _render_server_connection_panel(self):
        """渲染服务端连接、物理网路由与网关参数。"""

        def body() -> None:
            self.server_ip_input = ui.input(
                "服务端公网 IP 或域名",
                value=self.config.server_ip or "",
                placeholder="如 1.2.3.4 或 vpn.example.com",
            ).classes("w-full")
            with ui.row().classes("w-full gap-sm flex-wrap"):
                self.server_port_input = ui.number(
                    "端口",
                    value=self.config.port or 1194,
                    min=1,
                    max=65535,
                ).classes("flex-1 min-w-[10rem]")
                self.server_proto_input = ui.select(
                    {"udp": "UDP", "tcp": "TCP"},
                    label="协议",
                    value=self.config.proto or "udp",
                ).classes("flex-1 min-w-[10rem]")
            self.push_lan_routes_input = ui.textarea(
                "内网 push route（每行 IPv4 CIDR）",
                value="\n".join(
                    self.config.get("push_lan_routes") or []
                ),
                placeholder=(
                    "每行一条，如 172.16.22.0/24\n"
                    "勿填 VPN 地址池。"
                ),
            ).classes("w-full")
            ui.label(
                "客户端只会把这里配置的物理网段经 VPN 路由；"
                "中心侧按 VPN 源网段自动 MASQUERADE。"
            ).classes("settings-panel-desc")
            self.max_clients_input = ui.number(
                "单实例最大并发连接数（max-clients）",
                value=int(
                    self.config.get("max_clients")
                    or OPENVPN_DEFAULT_MAX_CLIENTS
                ),
                min=16,
                max=65534,
                step=1,
                format="%.0f",
            ).classes("w-full")
            ui.label(
                "保存会重写 server.conf 并刷新 VPN_FORWARD；"
                "OpenVPN 进程仍需在服务管理中重启。"
            ).classes("settings-panel-desc")

        def actions() -> None:
            secondary_action(
                "写入 sysctl.d 并加载",
                icon="memory",
                on_click=self._install_sysctl_dropin,
            )
            primary_action(
                "保存",
                icon="save",
                on_click=self._save_server_connection,
            )

        render_form_section(
            "服务端连接配置",
            kicker="Server",
            description=(
                "配置客户端 remote、物理网 push route 与并发连接上限。"
            ),
            body=body,
            actions=actions,
        )

    def _render_device_bind_panel(self):
        """渲染 client-connect 设备绑定策略。"""

        def body() -> None:
            self.device_bind_mode_input = ui.select(
                {
                    "weak_log": "A 弱绑定 · 仅记录",
                    "weak_fingerprint": "B 弱指纹 · 分级匹配",
                    "strict_hwaddr": "C 强绑定 · 须 IV_HWADDR",
                },
                label="策略",
                value=(
                    self.config.get("device_bind_mode")
                    or "weak_fingerprint"
                ),
            ).classes("w-full")
            ui.label(
                "A 仅审计；B 按 HW/UUID/平台信息分级匹配；"
                "C 强制 IV_HWADDR。切换策略后旧设备可能需要重置绑定。"
            ).classes("settings-panel-desc")

        def actions() -> None:
            primary_action(
                "保存策略",
                icon="save",
                on_click=self._save_device_bind,
            )

        render_form_section(
            "设备绑定策略",
            kicker="Device bind",
            description="控制 client-connect 的设备指纹匹配强度。",
            body=body,
            actions=actions,
        )

    def _render_global_ssh_panel(self) -> None:
        """渲染对端站点使用的全局 SSH 私钥与口令。"""

        def body() -> None:
            self.global_ssh_key_input = ui.textarea(
                "PEM 私钥全文",
                value=(
                    self.config.get("global_ssh_private_key")
                    or ""
                ),
                placeholder=(
                    "-----BEGIN OPENSSH PRIVATE KEY----- "
                    "或 BEGIN RSA / EC PRIVATE KEY-----"
                ),
            ).classes("w-full").props("rows=8")
            self.global_ssh_passphrase_input = ui.input(
                "私钥口令（若密钥有加密）",
                password=True,
                password_toggle_button=True,
                value=(
                    self.config.get(
                        "global_ssh_private_key_passphrase"
                    )
                    or ""
                ),
            ).classes("w-full")
            ui.label(
                "对端未单独配置私钥时才使用此处默认密钥；"
                "保存反馈和日志不会回显 PEM 或口令。"
            ).classes("settings-panel-desc")

        def actions() -> None:
            primary_action(
                "保存",
                icon="save",
                on_click=self._save_global_ssh,
            )

        render_form_section(
            "全局默认私钥",
            kicker="SSH",
            description="为未单独配置 SSH 私钥的对端提供默认认证。",
            body=body,
            actions=actions,
        )

    def _save_global_ssh(self) -> bool:
        """持久化全局 SSH 私钥与口令，不在反馈或日志中回显敏感值。"""

        current = load_config()
        try:
            merged = build_global_ssh_config(
                current,
                private_key=str(
                    self.global_ssh_key_input.value or ""
                ),
                passphrase=str(
                    self.global_ssh_passphrase_input.value or ""
                ),
            )
        except Exception as exc:
            logger.error(
                "全局 SSH 配置校验失败 type=%s",
                type(exc).__name__,
            )
            safe_notify(
                "全局 SSH 配置校验失败，请检查配置",
                type="negative",
            )
            return False

        try:
            save_config(merged)
            self.config = load_config()
        except Exception as exc:
            logger.error(
                "保存全局 SSH 配置失败 type=%s",
                type(exc).__name__,
            )
            safe_notify(
                "全局 SSH 保存失败，请检查配置文件权限",
                type="negative",
            )
            return False

        logger.info("已保存全局 SSH 私钥配置")
        safe_notify("全局 SSH 已保存", type="positive")
        return True

    def _render_github_proxy_panel(self):
        """渲染 GitHub 下载代理及当前表单测试。"""

        def body() -> None:
            self.github_proxy_input = ui.textarea(
                "代理列表",
                value="\n".join(
                    self.config.github_proxy_urls or []
                ),
                placeholder=(
                    "https://gh-proxy.org/\n"
                    "https://cdn.gh-proxy.org/\n"
                    "https://gh.llkk.cc/"
                ),
            ).classes("w-full").props("rows=6")
            self.proxy_status = ui.label(
                "按配置顺序尝试代理，最后回退 GitHub 原始地址。"
            ).classes("settings-status-text")

        def actions() -> None:
            secondary_action(
                "测试 EasyRSA 下载",
                icon="network_check",
                on_click=self._test_easyrsa_download,
            )
            primary_action(
                "保存",
                icon="save",
                on_click=self._save_github_proxies,
            )

        render_form_section(
            "GitHub 下载代理",
            kicker="Proxy",
            description=(
                "每行一个 http/https 前缀；安装与下载按当前顺序尝试。"
            ),
            body=body,
            actions=actions,
        )

    def _render_notify_panel(self):
        """渲染通知总开关、通道配置与当前表单测试。"""

        provider = str(
            self.config.get("notify_provider") or "none"
        ).strip()
        if provider not in {
            "none",
            "dingtalk",
            "wework",
            "rocketchat",
        }:
            provider = "none"

        def body() -> None:
            self.notify_enabled_input = ui.switch(
                "启用推送下载链接",
                value=bool(self.config.get("notify_enabled")),
            ).classes("w-full")
            self.notify_provider_select = ui.select(
                {
                    "none": "不使用",
                    "dingtalk": "钉钉机器人",
                    "wework": "企业微信机器人",
                    "rocketchat": "Rocket.Chat",
                },
                label="通知通道",
                value=provider,
            ).classes("w-full")
            self.notify_status = ui.label(
                "测试按钮使用当前表单值，无需先保存。"
            ).classes("settings-status-text")

            with ui.column().classes(
                "w-full gap-2 q-mt-sm"
            ) as dingtalk_fields:
                self._dingtalk_fields = dingtalk_fields
                ui.label("钉钉机器人参数").classes(
                    "settings-panel-title"
                )
                self.webhook_input = ui.input(
                    "Webhook URL",
                    value=self.config.dingtalk_webhook or "",
                    placeholder=(
                        "https://oapi.dingtalk.com/robot/send?"
                        "access_token=..."
                    ),
                ).classes("w-full")
                self.dingtalk_secret_input = ui.input(
                    "加签密钥 (SEC…)",
                    value=self.config.dingtalk_secret or "",
                    password=True,
                    password_toggle_button=True,
                    placeholder="未启用加签可留空",
                ).classes("w-full")
                self._dingtalk_test_button = secondary_action(
                    "发送测试消息（钉钉）",
                    icon="send",
                    on_click=self._test_webhook,
                )
            dingtalk_fields.bind_visibility_from(
                self.notify_provider_select,
                "value",
                value="dingtalk",
            )

            with ui.column().classes(
                "w-full gap-2 q-mt-sm"
            ) as wework_fields:
                self._wework_fields = wework_fields
                ui.label("企业微信机器人参数").classes(
                    "settings-panel-title"
                )
                self.wework_webhook_input = ui.input(
                    "Webhook URL（企业微信）",
                    value=self.config.get("wework_webhook") or "",
                    placeholder=(
                        "https://qyapi.weixin.qq.com/cgi-bin/"
                        "webhook/send?key=..."
                    ),
                ).classes("w-full")
                self._wework_test_button = secondary_action(
                    "发送测试消息（企业微信）",
                    icon="send",
                    on_click=self._test_wework_webhook,
                )
            wework_fields.bind_visibility_from(
                self.notify_provider_select,
                "value",
                value="wework",
            )

            with ui.column().classes(
                "w-full gap-2 q-mt-sm"
            ) as rocketchat_fields:
                self._rocketchat_fields = rocketchat_fields
                ui.label("Rocket.Chat 参数").classes(
                    "settings-panel-title"
                )
                self.rocketchat_webhook_input = ui.input(
                    "Incoming Webhook URL",
                    value=(
                        self.config.get("rocketchat_webhook")
                        or ""
                    ),
                    placeholder="https://chat.example/hooks/...",
                ).classes("w-full")
                channel_value = self.config.get(
                    "rocketchat_channel"
                )
                if isinstance(channel_value, list):
                    channel_text = "\n".join(
                        str(item) for item in channel_value
                    )
                else:
                    channel_text = str(channel_value or "")
                self.rocketchat_channel_input = ui.textarea(
                    "频道（每行一个或逗号分隔）",
                    value=channel_text,
                    placeholder="#vpn-alerts",
                ).classes("w-full").props("rows=3")
                with ui.row().classes("w-full gap-sm flex-wrap"):
                    self.rocketchat_username_input = ui.input(
                        "显示用户名",
                        value=(
                            self.config.get("rocketchat_username")
                            or "VPN管理"
                        ),
                    ).classes("flex-1 min-w-[10rem]")
                    self.rocketchat_icon_input = ui.input(
                        "图标 Emoji",
                        value=(
                            self.config.get("rocketchat_icon_emoji")
                            or ":lock:"
                        ),
                    ).classes("flex-1 min-w-[10rem]")
                self.rocketchat_title_input = ui.input(
                    "消息标题",
                    value=(
                        self.config.get("rocketchat_title")
                        or "OpenVPN"
                    ),
                ).classes("w-full")
                self._rocketchat_test_button = secondary_action(
                    "发送测试消息（Rocket.Chat）",
                    icon="send",
                    on_click=self._test_rocketchat_webhook,
                )
            rocketchat_fields.bind_visibility_from(
                self.notify_provider_select,
                "value",
                value="rocketchat",
            )

        def actions() -> None:
            primary_action(
                "保存通知配置",
                icon="save",
                on_click=self._save_notify,
            )

        render_form_section(
            "通知推送",
            kicker="Notify",
            description=(
                "启用并选择通道后，用户页的一次性下载链接"
                "会推送到对应机器人。"
            ),
            body=body,
            actions=actions,
        )

    def _render_download_panel(self):
        """渲染固定下载基址；留空继续自动解析当前访问地址。"""

        def body() -> None:
            self.base_url_input = ui.input(
                "基础 URL（可选）",
                value=self.config.download_base_url or "",
                placeholder="留空自动；例 https://vpn.example.com",
            ).classes("w-full")
            ui.label(
                "可包含反代子路径；不能包含 query、fragment 或 URL 凭据。"
            ).classes("settings-panel-desc")

        def actions() -> None:
            primary_action(
                "保存",
                icon="save",
                on_click=self._save_base_url,
            )

        render_form_section(
            "下载链接",
            kicker="Download",
            description=(
                "留空时按当前请求与监听地址自动生成；固定域名或反代前缀可在此覆盖。"
            ),
            body=body,
            actions=actions,
        )

    def _validate_path(self) -> bool:
        """同时验证 OpenVPN 与 EasyRSA 可执行路径。"""

        openvpn_path = str(self.openvpn_input.value or "").strip()
        easyrsa_path = str(self.easyrsa_input.value or "").strip()

        if not openvpn_path:
            self.path_status.text = "请输入 OpenVPN 可执行文件路径"
            safe_notify(self.path_status.text, type="warning")
            return False
        if not easyrsa_path:
            self.path_status.text = "请输入 EasyRSA 脚本路径"
            safe_notify(self.path_status.text, type="warning")
            return False

        try:
            result = validate_custom_path(openvpn_path)
        except Exception as exc:
            logger.exception("OpenVPN 路径验证异常")
            self.path_status.text = f"OpenVPN 路径验证异常: {exc}"
            safe_notify("OpenVPN 路径验证失败", type="negative")
            return False

        if not result.get("valid"):
            error = str(result.get("error") or "路径无效")
            self.path_status.text = f"OpenVPN: {error}"
            safe_notify(f"OpenVPN 验证失败: {error}", type="negative")
            return False

        easy_ok, easy_message = validate_easyrsa_script_path(
            easyrsa_path
        )
        if not easy_ok:
            self.path_status.text = f"EasyRSA: {easy_message}"
            safe_notify(
                f"EasyRSA 验证失败: {easy_message}",
                type="negative",
            )
            return False

        version = str(result.get("version") or "未知")
        self.path_status.text = (
            f"验证通过：OpenVPN {version}；EasyRSA 脚本可执行"
        )
        safe_notify("OpenVPN / EasyRSA 路径验证通过", type="positive")
        return True

    def _save_openvpn(self) -> bool:
        """校验后保存 OpenVPN / EasyRSA 路径。"""

        openvpn_path = str(self.openvpn_input.value or "").strip()
        easyrsa_path = str(self.easyrsa_input.value or "").strip()
        if not openvpn_path or not easyrsa_path:
            safe_notify(
                "OpenVPN 与 EasyRSA 路径均为必填",
                type="negative",
            )
            return False

        try:
            openvpn_result = validate_custom_path(openvpn_path)
        except Exception:
            logger.exception("保存前验证 OpenVPN 路径异常")
            safe_notify("OpenVPN 路径验证失败", type="negative")
            return False
        if not openvpn_result.get("valid"):
            safe_notify(
                f"OpenVPN 路径无效: {openvpn_result.get('error') or '未知'}",
                type="negative",
            )
            return False

        easy_ok, easy_message = validate_easyrsa_script_path(
            easyrsa_path
        )
        if not easy_ok:
            safe_notify(
                f"EasyRSA 路径无效: {easy_message}",
                type="negative",
            )
            return False

        current = load_config()
        try:
            merged = build_openvpn_path_config(
                current,
                openvpn_bin=openvpn_path,
                easyrsa_bin=easyrsa_path,
            )
            save_config(merged)
            self.config = load_config()
        except Exception as exc:
            logger.error("保存 OpenVPN 路径配置失败: %s", exc)
            safe_notify(
                f"OpenVPN 配置保存失败: {exc}",
                type="negative",
            )
            return False

        safe_notify("OpenVPN 配置已保存", type="positive")
        return True

    async def _test_easyrsa_download(self) -> bool:
        """按当前表单代理顺序测试 EasyRSA 下载，不修改已保存配置。"""

        if self._proxy_test_running:
            safe_notify("代理测试正在进行，请稍候", type="info")
            return False

        try:
            proxies = parse_github_proxy_urls(
                str(self.github_proxy_input.value or "")
            )
            candidates = build_github_candidate_urls(
                _EASYRSA_TEST_URL,
                proxies,
            )
        except ValueError as exc:
            self.proxy_status.text = f"配置无效: {exc}"
            safe_notify(str(exc), type="negative")
            return False

        self._proxy_test_running = True
        self.proxy_status.text = (
            f"测试中：共 {len(candidates)} 个候选地址…"
        )
        attempts: list[str] = []
        try:
            for index, candidate in enumerate(candidates, start=1):
                try:
                    response = await run.io_bound(
                        lambda url=candidate: requests.get(
                            url,
                            headers={
                                "Range": "bytes=0-0",
                                "User-Agent": (
                                    "openvpn-mgmt-settings-test"
                                ),
                            },
                            stream=True,
                            timeout=20,
                            allow_redirects=True,
                        )
                    )
                    try:
                        status = int(response.status_code)
                        if status not in (200, 206):
                            attempts.append(
                                f"{index}. {candidate} -> HTTP {status}"
                            )
                            continue
                        content_length = (
                            response.headers.get("Content-Length")
                            or response.headers.get("content-length")
                            or "未知"
                        )
                        self.proxy_status.text = (
                            f"第 {index}/{len(candidates)} 个候选成功："
                            f"{candidate}，长度 {content_length}"
                        )
                        safe_notify(
                            "EasyRSA 下载测试成功",
                            type="positive",
                        )
                        return True
                    finally:
                        close = getattr(response, "close", None)
                        if callable(close):
                            close()
                except Exception as exc:
                    attempts.append(
                        f"{index}. {candidate} -> "
                        f"{type(exc).__name__}: {exc}"
                    )

            detail = "\n".join(attempts) or "无可用候选"
            self.proxy_status.text = (
                "EasyRSA 下载测试全部失败：\n" + detail
            )
            safe_notify(
                "EasyRSA 下载测试失败，详情见页面",
                type="negative",
            )
            return False
        finally:
            self._proxy_test_running = False

    def _save_github_proxies(self) -> bool:
        """校验并按当前顺序保存 GitHub 下载代理。"""

        try:
            proxies = parse_github_proxy_urls(
                str(self.github_proxy_input.value or "")
            )
        except ValueError as exc:
            self.proxy_status.text = f"配置无效: {exc}"
            safe_notify(str(exc), type="negative")
            return False

        config = load_config()
        config.github_proxy_urls = proxies
        try:
            save_config(config)
            self.config = load_config()
        except Exception as exc:
            logger.error("保存 GitHub 代理失败: %s", exc)
            safe_notify(f"代理列表保存失败: {exc}", type="negative")
            return False

        self.proxy_status.text = (
            f"已保存 {len(proxies)} 个代理，顺序保持不变"
        )
        safe_notify("代理列表已保存", type="positive")
        return True

    def _save_notify(self) -> bool:
        """保存通知总开关、通道及三类机器人参数。"""

        current = load_config()
        try:
            merged = build_notify_config(
                current,
                enabled=bool(self.notify_enabled_input.value),
                provider=str(
                    self.notify_provider_select.value or "none"
                ),
                dingtalk_webhook=str(
                    self.webhook_input.value or ""
                ),
                dingtalk_secret=str(
                    self.dingtalk_secret_input.value or ""
                ),
                wework_webhook=str(
                    self.wework_webhook_input.value or ""
                ),
                rocketchat_webhook=str(
                    self.rocketchat_webhook_input.value or ""
                ),
                rocketchat_channel=str(
                    self.rocketchat_channel_input.value or ""
                ),
                rocketchat_username=str(
                    self.rocketchat_username_input.value or ""
                ),
                rocketchat_icon_emoji=str(
                    self.rocketchat_icon_input.value or ""
                ),
                rocketchat_title=str(
                    self.rocketchat_title_input.value or ""
                ),
            )
        except ValueError as exc:
            self.notify_status.text = f"配置校验失败: {exc}"
            safe_notify(str(exc), type="negative")
            return False
        except Exception as exc:
            logger.error(
                "通知配置校验异常 type=%s",
                type(exc).__name__,
            )
            self.notify_status.text = "通知配置校验失败"
            safe_notify("通知配置校验失败", type="negative")
            return False

        try:
            save_config(merged)
            self.config = load_config()
        except Exception as exc:
            logger.error(
                "通知配置保存失败 type=%s",
                type(exc).__name__,
            )
            self.notify_status.text = "通知配置保存失败"
            safe_notify(
                "通知配置保存失败，请检查配置文件权限",
                type="negative",
            )
            return False

        self.notify_status.text = (
            f"已保存：{'启用' if merged.notify_enabled else '停用'} / "
            f"{merged.notify_provider}"
        )
        safe_notify("通知配置已保存", type="positive")
        return True

    async def _test_webhook(self) -> bool:
        """使用当前表单值发送钉钉测试消息。"""

        if self._notify_test_running:
            safe_notify("通知测试正在进行，请稍候", type="info")
            return False
        url = str(self.webhook_input.value or "").strip()
        if not url:
            safe_notify("请填写钉钉 Webhook", type="warning")
            return False
        secret = (
            str(self.dingtalk_secret_input.value or "").strip()
            or None
        )

        self._notify_test_running = True
        self.notify_status.text = "正在发送钉钉测试消息…"
        try:
            data = await run.io_bound(
                send_dingtalk_text,
                url,
                secret,
                "【VPN 管理】钉钉测试成功",
            )
            if data.get("errcode") == 0:
                self.notify_status.text = "钉钉测试已发送"
                safe_notify("测试已发送", type="positive")
                return True
            message = str(data.get("errmsg") or "未知错误")
            self.notify_status.text = f"钉钉发送失败: {message}"
            safe_notify(
                f"钉钉发送失败: {message}",
                type="negative",
            )
            return False
        except ValueError as exc:
            self.notify_status.text = f"钉钉参数无效: {exc}"
            safe_notify(f"参数无效: {exc}", type="negative")
            return False
        except Exception as exc:
            logger.error(
                "钉钉测试发送失败 type=%s",
                type(exc).__name__,
            )
            self.notify_status.text = (
                f"钉钉发送失败: {type(exc).__name__}"
            )
            safe_notify(
                "钉钉发送失败，请检查 Webhook 与网络",
                type="negative",
            )
            return False
        finally:
            self._notify_test_running = False

    async def _test_wework_webhook(self) -> bool:
        """使用当前表单值发送企业微信测试消息。"""

        if self._notify_test_running:
            safe_notify("通知测试正在进行，请稍候", type="info")
            return False
        url = str(self.wework_webhook_input.value or "").strip()
        if not url:
            safe_notify("请填写企业微信 Webhook", type="warning")
            return False

        self._notify_test_running = True
        self.notify_status.text = "正在发送企业微信测试消息…"
        try:
            data = await run.io_bound(
                send_wework_text,
                url,
                "【VPN 管理】企业微信机器人测试成功",
            )
            if int(data.get("errcode", -1)) == 0:
                self.notify_status.text = "企业微信测试已发送"
                safe_notify("测试已发送", type="positive")
                return True
            message = str(
                data.get("errmsg") or data.get("errcode") or "未知错误"
            )
            self.notify_status.text = (
                f"企业微信发送失败: {message}"
            )
            safe_notify(
                f"企业微信发送失败: {message}",
                type="negative",
            )
            return False
        except ValueError as exc:
            self.notify_status.text = f"企业微信参数无效: {exc}"
            safe_notify(f"参数无效: {exc}", type="negative")
            return False
        except Exception as exc:
            logger.error(
                "企业微信测试发送失败 type=%s",
                type(exc).__name__,
            )
            self.notify_status.text = (
                f"企业微信发送失败: {type(exc).__name__}"
            )
            safe_notify(
                "企业微信发送失败，请检查 Webhook 与网络",
                type="negative",
            )
            return False
        finally:
            self._notify_test_running = False

    async def _test_rocketchat_webhook(self) -> bool:
        """使用当前表单值发送 Rocket.Chat 测试消息。"""

        if self._notify_test_running:
            safe_notify("通知测试正在进行，请稍候", type="info")
            return False

        url = str(self.rocketchat_webhook_input.value or "").strip()
        channels = parse_rocketchat_channels(
            str(self.rocketchat_channel_input.value or "")
        )
        if not url:
            safe_notify(
                "请填写 Rocket.Chat Webhook",
                type="warning",
            )
            return False
        if not channels:
            safe_notify(
                "请填写 Rocket.Chat 频道",
                type="warning",
            )
            return False

        username = (
            str(self.rocketchat_username_input.value or "").strip()
            or "VPN管理"
        )
        icon = (
            str(self.rocketchat_icon_input.value or "").strip()
            or ":lock:"
        )
        title = (
            str(self.rocketchat_title_input.value or "").strip()
            or "OpenVPN"
        )

        self._notify_test_running = True
        self.notify_status.text = "正在发送 Rocket.Chat 测试消息…"
        try:
            result = await run.io_bound(
                lambda: send_rocketchat_text(
                    url,
                    channels,
                    "【VPN 管理】Rocket.Chat 测试成功",
                    username=username,
                    icon_emoji=icon,
                    title=title,
                )
            )
            self.notify_status.text = (
                f"Rocket.Chat 测试已发送，HTTP "
                f"{result.get('status_code', '2xx')}"
            )
            safe_notify("测试已发送", type="positive")
            return True
        except ValueError as exc:
            self.notify_status.text = (
                f"Rocket.Chat 参数无效: {exc}"
            )
            safe_notify(f"参数无效: {exc}", type="negative")
            return False
        except Exception as exc:
            logger.error(
                "Rocket.Chat 测试发送失败 type=%s",
                type(exc).__name__,
            )
            self.notify_status.text = (
                f"Rocket.Chat 发送失败: {type(exc).__name__}"
            )
            safe_notify(
                "Rocket.Chat 发送失败，请检查 Webhook、频道与网络",
                type="negative",
            )
            return False
        finally:
            self._notify_test_running = False

    def _save_base_url(self) -> bool:
        """校验并保存下载链接基础 URL；留空表示自动解析。"""

        current = load_config()
        try:
            merged = build_download_config(
                current,
                download_base_url=str(
                    self.base_url_input.value or ""
                ),
            )
            save_config(merged)
            self.config = load_config()
        except ValueError as exc:
            safe_notify(str(exc), type="negative")
            return False
        except Exception as exc:
            logger.error("保存下载基础 URL 失败: %s", exc)
            safe_notify(
                f"下载基础 URL 保存失败: {exc}",
                type="negative",
            )
            return False

        if merged.download_base_url:
            safe_notify(
                "下载基础 URL 已保存",
                type="positive",
            )
        else:
            safe_notify(
                "已清空固定基址，将按当前访问自动解析",
                type="positive",
            )
        return True

    def _install_sysctl_dropin(self) -> bool:
        """安装并加载项目 sysctl.d 模板。"""

        from app.utils.sysctl_tune import install_vpn_sysctl_dropin

        logger.info("开始安装 VPN sysctl.d 模板")
        try:
            install_vpn_sysctl_dropin()
        except PermissionError as exc:
            logger.warning("安装 sysctl.d 权限不足: %s", exc)
            safe_notify(str(exc), type="negative")
            return False
        except FileNotFoundError as exc:
            logger.error("sysctl.d 模板缺失: %s", exc)
            safe_notify(str(exc), type="negative")
            return False
        except OSError as exc:
            logger.error("当前系统不支持 sysctl.d: %s", exc)
            safe_notify(str(exc), type="negative")
            return False
        except RuntimeError as exc:
            logger.error("加载 sysctl.d 失败: %s", exc)
            safe_notify(f"sysctl.d 加载失败: {exc}", type="negative")
            return False
        except Exception as exc:
            logger.exception("安装 sysctl.d 异常")
            safe_notify(f"sysctl.d 安装失败: {exc}", type="negative")
            return False

        logger.info("VPN sysctl.d 已安装并加载")
        safe_notify("sysctl.d 已安装并已加载", type="positive")
        return True

    def _save_server_connection(self) -> bool:
        """保存服务端连接配置；运行态更新失败时尽力回滚配置。"""

        from app.services.firewall.rule_service import FirewallRuleService
        from app.services.openvpn.instance import regenerate_all_server_confs

        previous = load_config()
        try:
            merged = build_server_connection_config(
                previous,
                server_ip=str(self.server_ip_input.value or ""),
                port=self.server_port_input.value,
                proto=str(self.server_proto_input.value or ""),
                push_lan_routes=str(
                    self.push_lan_routes_input.value or ""
                ),
                max_clients=self.max_clients_input.value,
            )
        except Exception as exc:
            safe_notify(f"配置校验失败: {exc}", type="negative")
            return False

        try:
            save_config(merged)
        except Exception as exc:
            logger.error("保存服务端配置失败: %s", exc)
            safe_notify(f"配置保存失败: {exc}", type="negative")
            return False

        firewall = FirewallRuleService()

        def rollback(reason: str) -> None:
            try:
                save_config(previous)
                regenerate_all_server_confs()
                firewall.refresh_vpn_forward_only()
                logger.warning("服务端配置已回滚: %s", reason)
            except Exception:
                logger.exception(
                    "服务端配置回滚失败，需人工核对 config/server.conf/VPN_FORWARD"
                )

        try:
            regenerate_all_server_confs()
        except Exception as exc:
            logger.error("重写 server.conf 失败: %s", exc)
            rollback("server.conf 重写失败")
            safe_notify(
                f"重写 server.conf 失败，配置已尝试回滚: {exc}",
                type="negative",
            )
            return False

        try:
            firewall.refresh_vpn_forward_only()
        except Exception as exc:
            logger.error("刷新 VPN_FORWARD 失败: %s", exc)
            rollback("VPN_FORWARD 刷新失败")
            safe_notify(
                f"VPN_FORWARD 刷新失败，配置已尝试回滚: {exc}",
                type="negative",
            )
            return False

        self.config = load_config()
        safe_notify(
            "已保存；server.conf 与 VPN_FORWARD 已更新。"
            "请到服务管理重启实例并让客户端重连。",
            type="positive",
            multi_line=True,
        )
        return True

    def _save_device_bind(self) -> bool:
        """保存设备绑定策略；运行时文件写失败时回滚 JSON 配置。"""

        from app.services.user.device_bind_policy import (
            sync_device_bind_mode_file,
        )

        previous = load_config()
        try:
            merged = build_device_bind_config(
                previous,
                mode=str(self.device_bind_mode_input.value or ""),
            )
        except Exception as exc:
            safe_notify(f"配置校验失败: {exc}", type="negative")
            return False

        try:
            save_config(merged)
        except Exception as exc:
            logger.error("保存设备绑定配置失败: %s", exc)
            safe_notify(f"配置保存失败: {exc}", type="negative")
            return False

        try:
            sync_device_bind_mode_file(merged.device_bind_mode)
        except OSError as exc:
            try:
                save_config(previous)
            except Exception:
                logger.exception("设备绑定配置回滚失败")
            safe_notify(
                "写入 device_bind_mode 失败，配置已尝试回滚: "
                f"{exc}",
                type="negative",
                multi_line=True,
            )
            return False

        self.config = load_config()
        safe_notify(
            "设备绑定已保存，新连接按新模式",
            type="positive",
        )
        return True
