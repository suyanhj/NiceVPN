# -*- coding: utf-8 -*-
"""首次运行初始化向导页面。"""

import logging
import queue
import threading
import json

from nicegui import ui

from app.core.init_wizard import InitWizard, StepResult, WizardStep
from app.ui.components import alert_card

logger = logging.getLogger(__name__)

_STEP_TITLES = {
    WizardStep.DETECT_OPENVPN: "环境检查",
    WizardStep.INSTALL_OPENVPN: "安装组件",
    WizardStep.CUSTOM_PATH: "校验路径",
    WizardStep.CONFIG_PKI: "证书体系",
    WizardStep.CONFIG_SUBNET: "接入网段",
    WizardStep.CREATE_DEFAULT_GROUP: "默认策略域",
    WizardStep.START_SERVICE: "启动服务",
}

_STEP_ORDER = [
    (WizardStep.DETECT_OPENVPN, "环境校验"),
    (WizardStep.INSTALL_OPENVPN, "组件准备"),
    (WizardStep.CONFIG_PKI, "证书体系"),
    (WizardStep.CONFIG_SUBNET, "接入规划"),
    (WizardStep.START_SERVICE, "服务接管"),
]


class InitPage:
    """系统初始化引导页。"""

    def __init__(self):
        self.wizard = InitWizard()
        self.current_step = WizardStep.DETECT_OPENVPN
        self.status_label = None
        self.step_container = None
        self.content = None

        self._install_log_queue = queue.Queue()
        self._install_result: StepResult | None = None
        self._install_running = False
        self._install_timer = None

        self._pki_log_queue = queue.Queue()
        self._pki_result: StepResult | None = None
        self._pki_running = False
        self._pki_timer = None

    def render(self):
        """渲染引导页面。"""
        with ui.column().classes("page-shell"):
            with ui.element("div").classes("setup-shell"):
                with ui.element("div").classes("setup-brand"):
                    ui.label("Secure Access Console").classes("setup-brand-badge")
                    ui.label("安全接入平台初始化").classes("setup-brand-title")
                    ui.label(
                        "右栏按步执行：环境、证书、地址池与默认服务；左栏显示当前进度。"
                    ).classes("setup-brand-copy")

                    with ui.element("div").classes("setup-status-card"):
                        ui.label("部署状态").classes("setup-status-kicker")
                        ui.label("等待初始化").classes("setup-status-title")
                        ui.label("完成后进入控制台，管理用户、站点与策略。").classes("setup-status-copy")

                    with ui.column().classes("setup-points"):
                        self._brand_point("顺序固定", "校验 → 证书 → 网段 → 启动。")
                        self._brand_point("可看日志", "安装与 PKI 输出日志，便于排错。")
                        self._brand_point("开箱可用", "默认策略域与接入实例会自动建好。")

                with ui.card().classes("setup-panel"):
                    with ui.column().classes("setup-panel-header"):
                        ui.label("First Run Setup").classes("section-kicker")
                        ui.label("开始配置").classes("setup-title")
                        self.status_label = ui.label("正在检查本机 OpenVPN 与环境…").classes("setup-subtitle")

                    self.step_container = ui.row().classes("setup-steps")
                    self.content = ui.column().classes("w-full gap-md")

                    self._render_initial_state()

    @staticmethod
    def _brand_point(title: str, copy: str) -> None:
        """渲染左侧说明块。"""
        with ui.element("div").classes("setup-point"):
            ui.label(title).classes("setup-point-title")
            ui.label(copy).classes("setup-point-copy")

    def _render_initial_state(self) -> None:
        """根据已保存配置恢复初始化进度，避免刷新后回到前置步骤。"""
        config = self.wizard.config
        if config.get("pki_dir") and config.get("global_subnet"):
            if self._has_default_group():
                self.current_step = WizardStep.START_SERVICE
                self._update_step_indicator()
                self.content.clear()
                with self.content:
                    alert_card.show("info", "已恢复进度", "证书与地址池已完成，请启动默认接入服务。")
                self._render_start_service()
                return

            self._run_step(WizardStep.CREATE_DEFAULT_GROUP)
            return

        if config.get("pki_dir"):
            self.current_step = WizardStep.CONFIG_SUBNET
            self._update_step_indicator()
            self.content.clear()
            with self.content:
                alert_card.show("info", "已恢复进度", "证书已完成，请填写接入地址池。")
            self._render_subnet_form()
            return

        self._run_step(WizardStep.DETECT_OPENVPN)

    @staticmethod
    def _has_default_group() -> bool:
        """检查初始化默认策略域是否已经存在。"""
        from app.core.constants import GROUPS_DIR

        if not GROUPS_DIR.is_dir():
            return False
        for path in GROUPS_DIR.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.error("读取初始化默认组失败 path=%s: %s", path, exc)
                raise
            if data.get("name") == "默认用户组":
                return True
        return False

    def _update_step_indicator(self):
        """更新顶部步骤指示器。"""
        if self.step_container is None:
            return

        current_index = self._progress_index(self.current_step)
        self.step_container.clear()

        with self.step_container:
            for index, (_, title) in enumerate(_STEP_ORDER, start=1):
                classes = "step-chip"
                if index < current_index:
                    classes += " is-done"
                elif index == current_index:
                    classes += " is-current"

                with ui.element("div").classes(classes):
                    ui.label(f"{index:02d}").classes("step-chip-index")
                    ui.label(title).classes("step-chip-title")

    @staticmethod
    def _progress_index(step: WizardStep) -> int:
        """把内部步骤映射到展示步骤序号。"""
        if step in (WizardStep.DETECT_OPENVPN,):
            return 1
        if step in (WizardStep.INSTALL_OPENVPN, WizardStep.CUSTOM_PATH):
            return 2
        if step == WizardStep.CONFIG_PKI:
            return 3
        if step in (WizardStep.CONFIG_SUBNET, WizardStep.CREATE_DEFAULT_GROUP):
            return 4
        return 5

    def _run_step(self, step: WizardStep, data: dict | None = None):
        """执行向导步骤并刷新 UI。"""
        self.current_step = step
        self._update_step_indicator()
        result = self.wizard.run_step(step, data)
        self._render_step_result(result)

    def _render_step_result(self, result: StepResult):
        """根据步骤结果渲染界面。"""
        self.content.clear()
        self._update_step_indicator()
        self.status_label.text = _STEP_TITLES.get(self.current_step, "初始化")

        with self.content:
            if result.success:
                alert_card.show("info", "本步完成", result.message)
            else:
                alert_card.show("warning", "待处理", result.message)

            if self.current_step == WizardStep.DETECT_OPENVPN:
                self._render_detect_result(result)
            elif self.current_step in (WizardStep.INSTALL_OPENVPN, WizardStep.CUSTOM_PATH):
                self._render_install_result(result)
            elif self.current_step == WizardStep.CONFIG_PKI:
                if result.success:
                    self._render_subnet_form()
                else:
                    self._render_pki_form()
            elif self.current_step == WizardStep.CONFIG_SUBNET:
                if result.success:
                    self._run_step(WizardStep.CREATE_DEFAULT_GROUP)
                else:
                    self._render_subnet_form()
            elif self.current_step == WizardStep.CREATE_DEFAULT_GROUP:
                if result.success:
                    self._render_start_service()
            elif self.current_step == WizardStep.START_SERVICE:
                if result.success:
                    self._render_complete()

    def _render_detect_result(self, result: StepResult):
        """渲染环境检查结果。"""
        with self.content:
            if result.success:
                self.status_label.text = "OpenVPN 已就绪，下一步建立证书。"
                self._render_pki_form()
                return

            self.status_label.text = "未检测到 OpenVPN：可自动安装或指定路径。"
            with ui.card().classes("page-panel"):
                ui.label("接入组件未就绪").classes("section-title")
                ui.label("本机未找到可用的 OpenVPN，请先安装或指定可执行文件。").classes("section-caption")
                with ui.row().classes("action-row q-mt-md"):
                    ui.button("自动安装 OpenVPN", icon="download", on_click=self._handle_install).props(
                        "unelevated color=primary"
                    ).classes("primary-action")
                    ui.button("我已安装，指定路径", icon="route", on_click=self._render_custom_path_form).props(
                        "outline"
                    ).classes("secondary-action")

    def _render_custom_path_form(self):
        """渲染自定义路径表单。"""
        self.current_step = WizardStep.CUSTOM_PATH
        self._update_step_indicator()
        self.content.clear()

        with self.content:
            self.status_label.text = "填写 OpenVPN 可执行文件的绝对路径。"
            with ui.card().classes("page-panel"):
                ui.label("校验自定义执行路径").classes("section-title")
                ui.label("已安装但不在 PATH 时，在此填写路径。").classes("section-caption")
                path_input = ui.input(
                    "OpenVPN 可执行文件路径",
                    placeholder="/usr/local/sbin/openvpn",
                ).classes("w-full q-mt-md")

                with ui.row().classes("action-row q-mt-md"):
                    ui.button(
                        "校验并继续",
                        icon="task_alt",
                        on_click=lambda: self._run_step(
                            WizardStep.CUSTOM_PATH,
                            {"path": path_input.value},
                        ),
                    ).props("unelevated color=primary").classes("primary-action")
                    ui.button("返回上一步", icon="arrow_back", on_click=lambda: self._run_step(WizardStep.DETECT_OPENVPN)).props(
                        "flat"
                    ).classes("secondary-action")

    def _handle_install(self):
        """后台执行 OpenVPN 安装。"""
        if self._install_running:
            ui.notify("OpenVPN 正在安装，请稍候", type="warning")
            return

        logger.info("步骤 2/5：开始安装 OpenVPN（按钮触发）")
        self.current_step = WizardStep.INSTALL_OPENVPN
        self._update_step_indicator()
        self.content.clear()
        self._install_running = True
        self._install_result = None
        self._install_log_queue = queue.Queue()

        with self.content:
            self.status_label.text = "正在安装 OpenVPN，请看下方日志。"
            with ui.card().classes("page-panel"):
                ui.label("正在安装接入组件").classes("section-title")
                ui.label("日志自动刷新；勿关页、勿重复点击安装。").classes("section-caption")
                ui.spinner("dots", size="lg").classes("q-my-md")
                with ui.element("div").classes("log-frame"):
                    log_area = ui.log(max_lines=80).classes("w-full h-72")
                log_area.push("开始执行 OpenVPN 接入组件安装流程...")

                def on_output(message: str):
                    self._install_log_queue.put(message)

                def worker():
                    try:
                        self._install_result = self.wizard.run_step(
                            WizardStep.INSTALL_OPENVPN,
                            {"on_output": on_output},
                        )
                    except Exception as exc:
                        self._install_result = StepResult(False, f"安装过程异常: {exc}")

                threading.Thread(target=worker, daemon=True).start()
                self._install_timer = ui.timer(0.2, lambda: self._poll_install_progress(log_area))

    def _poll_install_progress(self, log_area):
        """轮询安装进度并刷新日志。"""
        while True:
            try:
                message = self._install_log_queue.get_nowait()
            except queue.Empty:
                break

            log_area.push(message)
            if "下载" in message:
                self.status_label.text = "正在下载安装包…"
            elif "安装" in message or "编译" in message or "make" in message:
                self.status_label.text = "正在安装 OpenVPN…"

        if self._install_result is None:
            return

        if self._install_timer is not None:
            if hasattr(self._install_timer, "deactivate"):
                self._install_timer.deactivate()
            elif hasattr(self._install_timer, "active"):
                self._install_timer.active = False
            self._install_timer = None

        self._install_running = False
        result = self._install_result
        self._install_result = None
        logger.info("步骤 2/5：安装流程结束，success=%s", result.success)
        self.current_step = WizardStep.INSTALL_OPENVPN
        self._render_step_result(result)

    def _render_install_result(self, result: StepResult):
        """渲染安装步骤结果。"""
        with self.content:
            if result.success:
                self.status_label.text = "OpenVPN 已就绪，下一步建立证书。"
                self._render_pki_form()

    def _handle_pki_init(self):
        """后台执行 PKI 初始化。"""
        if self._pki_running:
            ui.notify("证书体系初始化进行中，请稍候", type="warning")
            return

        self.current_step = WizardStep.CONFIG_PKI
        self._update_step_indicator()
        self.content.clear()
        self._pki_running = True
        self._pki_result = None
        self._pki_log_queue = queue.Queue()

        with self.content:
            self.status_label.text = "正在建立证书体系…"
            with ui.card().classes("page-panel"):
                ui.label("正在建立证书体系").classes("section-title")
                ui.label("自动生成 CA、服务端证书、DH、CRL、tls-crypt-v2。").classes("section-caption")
                ui.spinner("dots", size="lg").classes("q-my-md")
                with ui.element("div").classes("log-frame"):
                    log_area = ui.log(max_lines=100).classes("w-full h-72")
                log_area.push("开始执行平台证书体系初始化流程...")

                def on_output(message: str):
                    self._pki_log_queue.put(message)

                def worker():
                    try:
                        self._pki_result = self.wizard.run_step(
                            WizardStep.CONFIG_PKI,
                            {"on_output": on_output},
                        )
                    except Exception as exc:
                        self._pki_result = StepResult(False, f"PKI 初始化过程异常: {exc}")

                threading.Thread(target=worker, daemon=True).start()
                self._pki_timer = ui.timer(0.2, lambda: self._poll_pki_progress(log_area))

    def _poll_pki_progress(self, log_area):
        """轮询 PKI 初始化进度。"""
        while True:
            try:
                message = self._pki_log_queue.get_nowait()
            except queue.Empty:
                break

            log_area.push(message)
            if "创建 PKI 目录" in message:
                self.status_label.text = "正在创建 PKI 目录..."
            elif "生成无密码 CA" in message:
                self.status_label.text = "正在生成 CA..."
            elif "生成 DH 参数" in message:
                self.status_label.text = "正在生成 DH 参数..."
            elif "生成 CRL" in message:
                self.status_label.text = "正在生成 CRL..."
            elif "tls-crypt-v2" in message:
                self.status_label.text = "正在生成 tls-crypt-v2 密钥..."

        if self._pki_result is None:
            return

        if self._pki_timer is not None:
            if hasattr(self._pki_timer, "deactivate"):
                self._pki_timer.deactivate()
            elif hasattr(self._pki_timer, "active"):
                self._pki_timer.active = False
            self._pki_timer = None

        self._pki_running = False
        result = self._pki_result
        self._pki_result = None
        self.current_step = WizardStep.CONFIG_PKI
        self._render_step_result(result)

    def _render_pki_form(self):
        """渲染 PKI 初始化按钮。"""
        with self.content:
            self.status_label.text = "OpenVPN 已就绪，可建立证书体系。"
            with ui.card().classes("page-panel"):
                ui.label("建立平台证书体系").classes("section-title")
                ui.label("签发服务端与客户端凭据，过程全自动，无需在控制台输入。").classes("section-caption")
                ui.button("开始建立证书体系", icon="vpn_key", on_click=self._handle_pki_init).props(
                    "unelevated color=primary"
                ).classes("primary-action q-mt-md")

    def _render_subnet_form(self):
        """渲染全局子网 + 服务端连接配置表单。"""
        with self.content:
            self.status_label.text = (
                "填写地址池与连接入口；需访问服务端局域网时在下方填写内网 CIDR（右栏内容较多时可向下滚动）。"
            )
            with ui.card().classes("page-panel"):
                ui.label("规划接入地址池").classes("section-title")
                ui.label("客户端与站点共用的 IPv4 地址池，CIDR 前缀须在 /16～/30。").classes("section-caption")
                subnet_input = ui.input(
                    "接入地址池",
                    placeholder="10.224.0.0/16",
                    validation={"CIDR 格式不正确或前缀超出 /16~/30 范围": lambda value: _quick_cidr_check(value)},
                ).classes("w-full q-mt-md")

                ui.separator().classes("q-my-md")
                ui.label("客户端连接入口").classes("section-title")
                ui.label("写入客户端配置的 remote：公网 IP 或域名、端口、协议。").classes("section-caption")
                server_ip_input = ui.input(
                    "公网 IP 或域名",
                    placeholder="如 1.2.3.4 或 vpn.example.com",
                ).classes("w-full q-mt-sm")
                with ui.row().classes("w-full gap-sm"):
                    server_port_input = ui.number(
                        "端口", value=1194, min=1, max=65535,
                    ).classes("flex-1")
                    server_proto_input = ui.select(
                        {"udp": "UDP", "tcp": "TCP"},
                        label="协议", value="udp",
                    ).classes("flex-1")

                ui.separator().classes("q-my-md")
                ui.label("访问服务端物理局域网").classes("section-title")
                ui.label(
                    "访问本机物理局域网时，每行一条要推送的 CIDR（勿填 VPN 地址池）。"
                    "留空则仅走隧道；可与系统设置一致，后续仍可改。"
                ).classes("section-caption")
                push_lan_input = ui.textarea(
                    "推送给客户端的内网路由（每行一条 CIDR）",
                    value="\n".join(self.wizard.config.get("push_lan_routes") or []),
                    placeholder="例如服务端局域网：\n172.16.22.0/24",
                ).classes("w-full q-mt-sm")

                ui.button(
                    "保存接入规划",
                    icon="arrow_forward",
                    on_click=lambda: self._run_step(
                        WizardStep.CONFIG_SUBNET,
                        {
                            "subnet": subnet_input.value,
                            "server_ip": (server_ip_input.value or "").strip(),
                            "port": int(server_port_input.value or 1194),
                            "proto": server_proto_input.value or "udp",
                            "push_lan_routes": [
                                x.strip()
                                for x in (push_lan_input.value or "").splitlines()
                                if x.strip()
                            ],
                        },
                    ),
                ).props("unelevated color=primary").classes("primary-action q-mt-md")

    def _render_start_service(self):
        """渲染启动服务按钮。"""
        with self.content:
            self.status_label.text = "配置已完成，可启动默认接入服务。"
            with ui.card().classes("page-panel"):
                ui.label("启动默认接入服务").classes("section-title")
                ui.label("启动后由平台托管，首页可看服务与连接概况。").classes("section-caption")
                ui.button(
                    "启动并进入平台",
                    icon="play_circle",
                    on_click=lambda: self._run_step(
                        WizardStep.START_SERVICE,
                        {"instance_name": "server"},
                    ),
                ).props("unelevated color=primary size=lg").classes("primary-action q-mt-md")

    def _render_complete(self):
        """渲染初始化完成界面。"""
        self.content.clear()
        self._update_step_indicator()

        with self.content:
            self.status_label.text = "初始化完成，正在进入控制台…"
            alert_card.show("info", "平台已就绪", "初始化完成，即将跳转首页。")
            ui.timer(2.0, lambda: ui.navigate.to("/"), once=True)


def _quick_cidr_check(value: str) -> bool:
    """快速校验 CIDR 格式与前缀长度。"""
    if not value:
        return True
    from app.utils.cidr import validate_cidr
    if not validate_cidr(value):
        return False
    from ipaddress import IPv4Network
    try:
        return IPv4Network(value, strict=False).prefixlen >= 16
    except ValueError:
        return False
