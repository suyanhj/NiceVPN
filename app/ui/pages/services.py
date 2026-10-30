# -*- coding: utf-8 -*-
"""服务管理页面。"""

import asyncio
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from nicegui import background_tasks, context, run, ui
from nicegui.functions.notify import ARG_MAP as _NICEGUI_NOTIFY_ARG_MAP

from app.core.config import load_config
from app.core.constants import (
    DEVICE_BIND_LOG_FILE,
    OPENVPN_DAEMON_LOG_DIR,
    OPENVPN_UPGRADE_LOG_FILE,
)
from app.services.config_editor.config_backup import backup_before_edit, save_with_backup
from app.services.monitor.service_monitor import InstanceStatus, ServiceMonitor
from app.services.openvpn.instance import (
    resolve_status_log_path,
    resolve_server_conf_dir,
    restart_instance,
    start_instance,
    stop_instance,
)
from app.services.peer_instance.service import PeerService
from app.ui.adapters.services import adapt_local_service_view, adapt_peer_service_view
from app.ui.components import confirm_dialog
from app.ui.components.actions import secondary_action
from app.ui.components.form_dialog import create_form_dialog
from app.ui.components.states import empty_state, error_state
from app.ui.components.status import status_badge, structured_value

logger = logging.getLogger(__name__)

# 日志正文外层可滚动视口（供实时 tail 判断是否贴底并自动跟随）
_LOG_TAIL_CODE_STYLE = "height: min(500px, 55vh); font-size: 12px;"
_LOG_TAIL_STICK_PX = 48
# 非实时「刷新」展示行数；实时模式仅保留最新若干行，控制内存与 DOM
_LOG_SNAPSHOT_MAX_LINES = 200
_LOG_REALTIME_MAX_LINES = 500
_LOG_REALTIME_POLL_SEC = 1.0


class ServicesPage:
    """服务管理页面。

    全局在线数、流量汇总见首页仪表盘；本页展示实例级状态与「N Running」角标。
    """

    def __init__(self):
        self._monitor = ServiceMonitor()
        self._peer_service = PeerService()
        self._list_container = None
        self._peer_list_container = None
        self._running_chip = None
        self._peer_total_chip = None
        self._service_tabs = None
        self._active_tab = "local"
        self._peer_status_refreshers: list[Callable[[], Any]] = []
        self._peer_refresh_generation = 0
        self._peer_refresh_concurrency = 4
        self._peer_refresh_running = False
        # 本机 systemctl 启停与事件循环解耦：同实例不并发重复点
        self._local_instance_busy: set[str] = set()
        self._peer_action_busy: set[str] = set()
        self._openvpn_upgrade_stage: dict[str, Any] | None = None
        self._openvpn_upgrade_busy = False
        # background_tasks / run.io_bound 无当前 slot 栈时不能直接用 ui.notify
        self._ng_client: Any = None

    def _page_client(self) -> Any:
        """后台 Task 发通知、刷列表用（与 firewall 页一致）。"""
        c = self._ng_client
        if c is not None:
            return c
        for cont in (self._list_container, self._peer_list_container):
            if cont is not None:
                cl = getattr(cont, "client", None)
                if cl is not None:
                    return cl
        return None

    def _notify_for_page(
        self,
        message: Any,
        *,
        type: str | None = None,  # noqa: A001 与 Quasar / nicegui 一致
        position: str = "bottom",
        close_button: bool | str = False,
        color: str | None = None,
        multi_line: bool = False,
        **kwargs: Any,
    ) -> None:
        """任意协程内向当前页发通知，不依赖 slot 栈。"""
        c = self._page_client()
        if c is None:
            logger.error("服务管理：无 page client，无法弹通知: %s", message)
            return
        options = {
            _NICEGUI_NOTIFY_ARG_MAP.get(key, key): value
            for key, value in locals().items()
            if key not in ("self", "c", "message", "kwargs") and value is not None
        }
        options["message"] = str(message)
        options.update(kwargs)
        c.outbox.enqueue_message("notify", options, c.id)

    def _refresh_list_with_client(self) -> None:
        """在浏览器会话上下文中执行 _refresh_list（供后台 Task 使用）。"""
        c = self._page_client()
        if c is None:
            logger.error("服务管理：无 page client，无法刷新本机实例列表")
            return
        c.safe_invoke(lambda: self._refresh_list())

    def render(self, *, initial_tab: str = "local", focus_peer_id: str | None = None):
        """使用统一后台外壳渲染服务管理页面。"""

        try:
            self._ng_client = context.client
        except RuntimeError:
            self._ng_client = None

        config = load_config()
        self._instances: dict = dict(config.get("instances", {}))
        self._conf_dir: str = config.get("openvpn_conf_dir", "/etc/openvpn")
        self._active_tab = "peers" if initial_tab == "peers" else "local"
        self._focus_peer_id = str(focus_peer_id or "").strip()

        with ui.column().classes("page-shell mgmt-page services-page--v2"):
            with ui.tabs(
                value=self._active_tab,
                on_change=self._on_service_tab_change,
            ).classes("services-instance-tabs w-full") as self._service_tabs:
                ui.tab("local", label="本机实例")
                ui.tab("peers", label="对端实例")

            with ui.tab_panels(
                self._service_tabs,
                value=self._active_tab,
                keep_alive=False,
            ).classes("w-full min-h-0 flex-1 services-tabpanels"):
                with ui.tab_panel("local"):
                    with ui.element("section").classes("mgmt-panel mgmt-panel-list services-list-panel"):
                        with ui.element("div").classes("mgmt-list-head services-list-head"):
                            with ui.row().classes("items-center gap-2 w-full"):
                                ui.label("本机实例").classes("mgmt-kicker services-list-kicker")
                                self._running_chip = ui.label("0 运行").classes("services-running-chip")
                                ui.space()
                                secondary_action(
                                    "升级日志",
                                    icon="article",
                                    on_click=self._show_openvpn_upgrade_log_viewer,
                                ).tooltip("查看 OpenVPN 升级与兼容性预检日志")
                                secondary_action(
                                    "OpenVPN 升级",
                                    icon="system_update_alt",
                                    on_click=self._open_openvpn_upgrade_dialog,
                                ).tooltip("检查最新版本、验证服务端和客户端配置后安全升级")
                        self._list_container = ui.column().classes("mgmt-record-list w-full")
                        self._refresh_list()

                with ui.tab_panel("peers"):
                    with ui.element("section").classes("mgmt-panel mgmt-panel-list services-list-panel"):
                        with ui.element("div").classes("mgmt-list-head services-list-head"):
                            with ui.row().classes("items-center gap-2"):
                                ui.label("对端实例").classes("mgmt-kicker services-list-kicker")
                                self._peer_total_chip = ui.label("0 个").classes("services-running-chip")
                        self._peer_list_container = ui.column().classes("mgmt-record-list w-full")
                        self._refresh_peer_client_list()

    def _show_openvpn_upgrade_log_viewer(self) -> None:
        """查看独立 OpenVPN 升级日志，避免大量编译输出占据升级弹窗。"""

        log_path = OPENVPN_UPGRADE_LOG_FILE

        def load() -> tuple[str, str]:
            if not log_path.exists():
                return f"日志: {log_path}", "(尚无 OpenVPN 升级日志)"
            return f"日志: {log_path}", self._read_log_tail(log_path)

        self._open_log_tail_dialog(
            "OpenVPN 升级日志",
            load,
            precheck_file=False,
            watch_path=lambda: log_path,
            caption_for_path=lambda path: f"日志: {path}",
        )

    def _open_openvpn_upgrade_dialog(self) -> None:
        """检查最新 OpenVPN，staging 后同时验证 server.conf 与客户端 .ovpn。"""

        from app.services.openvpn.detector import detect_openvpn

        if self._openvpn_upgrade_busy:
            ui.notify("OpenVPN 升级检查正在执行，请稍候", type="info")
            return

        current = detect_openvpn()
        current_version = str(current.get("version") or "未知")
        state: dict[str, Any] = {
            "preflight": None,
            "stage": self._openvpn_upgrade_stage,
        }

        dialog = ui.dialog().props("persistent")
        with dialog, ui.card().classes("w-full max-w-4xl"):
            with ui.row().classes("w-full items-center gap-3"):
                ui.icon("system_update_alt", size="24px").classes("text-primary")
                with ui.column().classes("gap-0"):
                    ui.label("OpenVPN 安全升级").classes("text-lg font-semibold")
                    ui.label(
                        "候选版本不会先覆盖生产 binary；服务端与客户端配置均通过后才允许升级。"
                    ).classes("section-caption")

            with ui.row().classes("w-full gap-3 flex-wrap"):
                current_label = ui.label(f"当前版本：{current_version}").classes(
                    "services-running-chip"
                )
                target_label = ui.label("目标版本：待检查").classes(
                    "services-running-chip"
                )
                server_label = ui.label("服务端配置：待检查").classes(
                    "services-running-chip"
                )
                client_label = ui.label("客户端配置：待检查").classes(
                    "services-running-chip"
                )

            status_label = ui.label("尚未执行升级检查。").classes("section-caption")
            with ui.row().classes("w-full items-center gap-2"):
                ui.label(f"详细日志：{OPENVPN_UPGRADE_LOG_FILE}").classes(
                    "section-caption"
                )
                secondary_action(
                    "查看升级日志",
                    icon="article",
                    on_click=self._show_openvpn_upgrade_log_viewer,
                )

            with ui.row().classes("w-full justify-end gap-2"):
                close_button = ui.button(
                    "关闭",
                    on_click=lambda: self._close_upgrade_dialog(dialog),
                ).props("flat")
                check_button = ui.button(
                    "检查新版并预检",
                    icon="fact_check",
                )
                upgrade_button = ui.button(
                    "安全升级",
                    icon="system_update_alt",
                )
                upgrade_button.disable()

            async def run_check() -> None:
                if self._openvpn_upgrade_busy:
                    return
                from app.services.openvpn.upgrade import (
                    cleanup_staged_candidate,
                    inspect_upgrade,
                    preflight_upgrade_candidate,
                    stage_upgrade_candidate,
                )

                self._openvpn_upgrade_busy = True
                check_button.disable()
                upgrade_button.disable()
                close_button.disable()
                status_label.text = "正在检查最新版本并准备隔离候选 binary…"
                try:
                    if self._openvpn_upgrade_stage:
                        cleanup_staged_candidate(self._openvpn_upgrade_stage)
                        self._openvpn_upgrade_stage = None

                    info = await run.io_bound(inspect_upgrade)
                    target_version = str(info.get("target_version") or "未知")
                    target_label.text = f"目标版本：{target_version}"
                    if not bool(info.get("upgrade_available")):
                        status_label.text = "当前已经是最新版本，无需升级。"
                        server_label.text = "服务端配置：未执行"
                        client_label.text = "客户端配置：未执行"
                        return

                    status_label.text = (
                        f"发现 OpenVPN {target_version}，正在临时编译并执行兼容性预检…"
                    )
                    stage = await run.io_bound(stage_upgrade_candidate)
                    preflight = await run.io_bound(
                        preflight_upgrade_candidate,
                        stage,
                    )
                    state["stage"] = stage
                    state["preflight"] = preflight
                    self._openvpn_upgrade_stage = stage

                    server_results = list(preflight.get("server_results") or [])
                    server_ok = bool(server_results) and all(
                        bool(item.get("ok")) for item in server_results
                    )
                    server_label.text = (
                        f"服务端配置：{'通过' if server_ok else '失败'} "
                        f"({sum(1 for item in server_results if item.get('ok'))}/{len(server_results)})"
                    )

                    client_result = preflight.get("client_result") or {}
                    client_ok = bool(client_result.get("ok"))
                    source_kind = "一次性生成" if client_result.get("generated") else "现有配置"
                    client_label.text = (
                        f"客户端配置：{'通过' if client_ok else '失败'} ({source_kind})"
                    )

                    if bool(preflight.get("ok")):
                        status_label.text = (
                            f"候选 OpenVPN {stage.get('version')} 已通过全部兼容性检查，可以安全升级。"
                        )
                        upgrade_button.enable()
                    else:
                        status_label.text = (
                            "候选版本兼容性检查失败，生产 OpenVPN 未被修改："
                            + str(preflight.get("error") or "未知错误")
                        )
                except Exception as exc:
                    logger.exception("OpenVPN 升级预检失败")
                    status_label.text = f"升级检查失败：{exc}"
                    server_label.text = "服务端配置：失败"
                    client_label.text = "客户端配置：未通过"
                    self._notify_for_page(f"OpenVPN 升级检查失败: {exc}", type="negative")
                finally:
                    self._openvpn_upgrade_busy = False
                    check_button.enable()
                    close_button.enable()

            async def run_upgrade() -> None:
                stage = self._openvpn_upgrade_stage
                preflight = state.get("preflight")
                if not stage or not isinstance(preflight, dict) or not preflight.get("ok"):
                    ui.notify("请先完成候选版本兼容性预检", type="warning")
                    return

                from app.services.openvpn.upgrade import (
                    apply_staged_upgrade,
                    cleanup_staged_candidate,
                )

                self._openvpn_upgrade_busy = True
                check_button.disable()
                upgrade_button.disable()
                close_button.disable()
                status_label.text = (
                    f"正在升级到 OpenVPN {stage.get('version')}；失败会自动恢复旧 binary/systemd…"
                )
                try:
                    result = await run.io_bound(
                        apply_staged_upgrade,
                        stage,
                    )
                    status_label.text = (
                        f"OpenVPN {result.get('version')} 升级完成；"
                        f"{len(result.get('active_instances') or [])} 个原运行实例已恢复。"
                    )
                    current_label.text = f"当前版本：{result.get('version')}"
                    target_label.text = f"目标版本：{result.get('version')}（已安装）"
                    self._notify_for_page(
                        f"OpenVPN 已安全升级到 {result.get('version')}",
                        type="positive",
                    )
                    cleanup_staged_candidate(stage)
                    self._openvpn_upgrade_stage = None
                    state["stage"] = None
                    self._refresh_list_with_client()
                except Exception as exc:
                    logger.exception("OpenVPN 安全升级失败")
                    status_label.text = f"升级失败，已执行自动回滚：{exc}"
                    self._notify_for_page(
                        f"OpenVPN 升级失败并已尝试回滚: {exc}",
                        type="negative",
                        multi_line=True,
                    )
                    upgrade_button.enable()
                finally:
                    self._openvpn_upgrade_busy = False
                    check_button.enable()
                    close_button.enable()

            def confirm_upgrade() -> None:
                stage = self._openvpn_upgrade_stage
                if not stage:
                    ui.notify("没有已通过预检的候选版本", type="warning")
                    return
                confirm_dialog.show(
                    title=f"升级到 OpenVPN {stage.get('version')}？",
                    message=(
                        "升级会创建完整 NiceVPN 备份，替换本机 OpenVPN binary/systemd，"
                        "并重启当前正在运行的实例。"
                    ),
                    danger_level="warning",
                    impact="在线 VPN 会话会短暂中断；任何实例或升级后 preflight 失败都会自动回滚。",
                    on_confirm=lambda: ui.timer(0.01, run_upgrade, once=True),
                )

            check_button.on("click", run_check)
            upgrade_button.on("click", confirm_upgrade)

        dialog.open()

    def _close_upgrade_dialog(self, dialog: Any) -> None:
        """关闭升级对话框时清理未应用的 staging 目录。"""

        if self._openvpn_upgrade_busy:
            ui.notify("升级操作执行中，暂不能关闭", type="warning")
            return
        from app.services.openvpn.upgrade import cleanup_staged_candidate

        if self._openvpn_upgrade_stage:
            cleanup_staged_candidate(self._openvpn_upgrade_stage)
            self._openvpn_upgrade_stage = None
        dialog.close()

    def _on_service_tab_change(self, e) -> None:
        self._active_tab = str(getattr(e, "value", None) or "local")
        if self._active_tab != "peers":
            self._peer_refresh_generation += 1
            self._peer_status_refreshers = []
            self._peer_refresh_running = False
        self._refresh_current_tab()

    def _refresh_current_tab(self) -> None:
        if self._active_tab == "peers":
            if self._peer_refresh_running:
                ui.notify("对端状态刷新中，请稍候", type="info")
                return
            self._refresh_peer_client_list()
        else:
            self._refresh_list()

    def _refresh_list(self):
        """刷新本机实例列表，并把单个监控失败隔离为页面错误状态。"""

        if self._list_container is None:
            return

        self._list_container.clear()
        try:
            instance_names = self._collect_instance_names()
        except Exception as exc:
            logger.exception("扫描本机 OpenVPN 实例失败")
            if self._running_chip is not None:
                self._running_chip.text = "加载失败"
            with self._list_container:
                error_state(
                    str(exc),
                    title="实例扫描失败",
                    on_retry=self._refresh_list,
                )
            return

        if not instance_names:
            if self._running_chip is not None:
                self._running_chip.text = "0 运行"
            with self._list_container:
                empty_state(
                    "暂无本机实例",
                    description="完成 OpenVPN 初始化后，实例会显示在这里。",
                    icon="dns",
                )
            return

        try:
            statuses = self._monitor.check_all_instances(instance_names)
        except Exception as exc:
            logger.exception("读取本机 OpenVPN 实例状态失败")
            if self._running_chip is not None:
                self._running_chip.text = "状态失败"
            with self._list_container:
                error_state(
                    str(exc),
                    title="实例状态加载失败",
                    on_retry=self._refresh_list,
                )
            return

        running_count = sum(1 for item in statuses if item.active)
        if self._running_chip is not None:
            self._running_chip.text = f"{running_count} / {len(statuses)} 运行"

        with self._list_container:
            for status in statuses:
                instance_config = self._instances.get(status.name, {})
                self._render_instance_row(status, instance_config)

    def _collect_instance_names(self) -> list[str]:
        """config 中注册的实例名 + 扫描 conf 目录发现的未登记实例。"""
        instance_names = list(self._instances.keys())
        conf_dir = resolve_server_conf_dir(self._conf_dir)
        if conf_dir.is_dir():
            for conf_file in sorted(conf_dir.glob("*.conf")):
                name = conf_file.stem
                if name not in instance_names:
                    instance_names.append(name)
                    self._instances[name] = {"port": 1194, "proto": "udp", "subnet": "未知（扫描发现）"}
        return instance_names

    @staticmethod
    def _read_log_tail(path: Path, max_lines: int = _LOG_SNAPSHOT_MAX_LINES) -> str:
        """读取文本日志末尾若干行（整文件读入，仅用于小快照）。"""
        if not path.exists():
            raise FileNotFoundError(str(path))
        content = path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        tail_lines = lines[-max_lines:] if len(lines) > max_lines else lines
        return "\n".join(tail_lines)

    @staticmethod
    def _read_log_last_lines(path: Path, max_lines: int) -> str:
        """从文件末尾逆向块读至多 max_lines 行，避免大日志整文件读入内存。"""
        if max_lines <= 0:
            return ""
        if not path.exists():
            raise FileNotFoundError(str(path))
        block = 65536
        chunks: list[bytes] = []
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            total_nl = 0
            while pos > 0 and total_nl <= max_lines:
                step = min(block, pos)
                pos -= step
                f.seek(pos)
                chunks.insert(0, f.read(step))
                total_nl = sum(c.count(b"\n") for c in chunks)
                if total_nl > max_lines + 200:
                    break
        raw = b"".join(chunks)
        parts = raw.splitlines()
        tail = parts[-max_lines:] if len(parts) > max_lines else parts
        return "\n".join(p.decode("utf-8", errors="replace") for p in tail)

    def _open_log_tail_dialog(
        self,
        title: str,
        load: Callable[[], tuple[str, str]],
        *,
        precheck_file: bool = True,
        header_widget: Callable[[], object] | None = None,
        watch_path: Callable[[], Path] | None = None,
        caption_for_path: Callable[[Path], str] | None = None,
    ) -> None:
        """通用日志尾弹窗：load 返回 (路径说明行, 正文)。

        precheck_file=True：打开前与刷新时遇 FileNotFoundError 则 notify（实例/绑定日志）。
        precheck_file=False：由 load 自行处理缺文件（状态日志）。
        header_widget：若提供，在标题下插入控件（如实例选择），返回的组件应支持 on_value_change。
        watch_path：若提供则显示「实时查看」开关，按间隔只保留最新 _LOG_REALTIME_MAX_LINES 行。
        caption_for_path：实时刷新时路径行文案；默认「日志: …」。
        """
        cap = caption_for_path or (lambda p: f"日志: {p}")

        initial: tuple[str, str] | None = None
        if header_widget is None:
            if precheck_file:
                try:
                    initial = load()
                except FileNotFoundError as exc:
                    ui.notify("日志不存在", type="warning")
                    return
                except OSError as exc:
                    logger.error("读日志失败: %s", exc)
                    ui.notify(f"读日志失败: {exc}", type="negative")
                    return
            else:
                initial = load()

        with ui.dialog() as dialog, ui.card().classes("w-full max-w-4xl services-view-dialog"):
            ui.label(title).classes("text-h6 services-view-dialog__title")
            header_ctrl = header_widget() if header_widget else None
            path_caption = ui.label(initial[0] if initial else "").classes("section-caption services-view-dialog__caption")
            follow_tail = True

            def on_log_scroll(event) -> None:
                """用户离开底部时暂停自动跟随，回到底部后恢复。"""

                nonlocal follow_tail
                remaining = (
                    event.vertical_size
                    - event.vertical_container_size
                    - event.vertical_position
                )
                follow_tail = remaining <= _LOG_TAIL_STICK_PX

            with ui.scroll_area(on_scroll=on_log_scroll).classes(
                "w-full log-frame vpn-log-tail-viewport services-log-frame"
            ).style(_LOG_TAIL_CODE_STYLE) as log_viewport:
                log_label = ui.label(initial[1] if initial else "").classes(
                    "w-full whitespace-pre-wrap break-words font-mono text-left"
                ).style("font-size: 12px;")

            def set_log_body(text: str, *, follow_tail_after: bool) -> None:
                """更新日志正文，并仅在用户仍贴底时滚到最新内容。"""

                log_label.text = text
                if follow_tail_after and follow_tail:
                    log_viewport.scroll_to(percent=1.0)

            def do_refresh() -> None:
                if precheck_file:
                    try:
                        pl, t = load()
                    except FileNotFoundError as exc:
                        ui.notify("日志不存在", type="warning")
                        return
                    except OSError as exc:
                        logger.error("刷新日志失败: %s", exc)
                        ui.notify(f"读取失败: {exc}", type="negative")
                        return
                else:
                    pl, t = load()
                path_caption.text = pl
                set_log_body(t, follow_tail_after=False)

            def apply_realtime_tail() -> None:
                if watch_path is None:
                    return
                try:
                    p = watch_path()
                except FileNotFoundError:
                    path_caption.text = "日志不存在"
                    set_log_body("(尚无日志文件)", follow_tail_after=True)
                    return
                except OSError as exc:
                    logger.error("实时日志解析路径失败: %s", exc)
                    path_caption.text = f"路径错误: {exc}"
                    set_log_body(str(exc), follow_tail_after=True)
                    return
                path_caption.text = cap(p)
                try:
                    body = self._read_log_last_lines(p, _LOG_REALTIME_MAX_LINES)
                except FileNotFoundError:
                    body = f"(尚无文件)\n{p}"
                except OSError as exc:
                    logger.error("实时读日志失败 path=%s err=%s", p, exc)
                    body = f"读取失败: {exc}"
                set_log_body(body, follow_tail_after=True)

            rt_switch = None
            poll_timer = None
            if watch_path is not None:
                rt_switch = ui.switch(
                    f"实时（尾 {_LOG_REALTIME_MAX_LINES} 行 / ~{_LOG_REALTIME_POLL_SEC:g}s）",
                    value=False,
                ).classes("q-mt-sm")
                poll_timer = ui.timer(
                    _LOG_REALTIME_POLL_SEC,
                    lambda: apply_realtime_tail() if rt_switch.value else None,
                    active=False,
                )

                def on_rt_toggle() -> None:
                    if rt_switch.value:
                        poll_timer.activate()
                        apply_realtime_tail()
                    else:
                        # 关闭实时后保持当前正文，不立刻改回快照行数
                        poll_timer.deactivate()

                rt_switch.on_value_change(lambda _: on_rt_toggle())

            def do_manual_refresh() -> None:
                if rt_switch is not None and rt_switch.value:
                    apply_realtime_tail()
                else:
                    do_refresh()

            if header_ctrl is not None:
                header_ctrl.on_value_change(lambda _: do_manual_refresh())
                do_refresh()

            def cleanup_log_dialog() -> None:
                if poll_timer is not None:
                    poll_timer.deactivate()

            def close_log_dialog() -> None:
                cleanup_log_dialog()
                dialog.close()

            dialog.on("hide", lambda _event: cleanup_log_dialog())

            with ui.row().classes("justify-end q-mt-md gap-sm items-center"):
                secondary_action("刷新", icon="refresh", on_click=do_manual_refresh)
                secondary_action("关闭", on_click=close_log_dialog)
        dialog.open()

    def _resolve_instance_daemon_log_path(self, name: str) -> Path:
        """解析实例 log-append 对应文件路径（含 legacy）；不存在则抛出 FileNotFoundError。"""
        conf_path = resolve_server_conf_dir(self._conf_dir) / f"{name}.conf"
        log_path_str: str | None = None
        if conf_path.exists():
            try:
                for line in conf_path.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if stripped.startswith("log-append"):
                        parts = stripped.split(None, 1)
                        if len(parts) == 2:
                            log_path_str = parts[1].strip()
                            break
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning("读取 %s 配置失败: %s", name, exc)
        if not log_path_str:
            log_path_str = str((OPENVPN_DAEMON_LOG_DIR / f"{name}.log").resolve())
        log_file = Path(log_path_str)
        if not log_file.exists():
            legacy = Path(f"/var/log/openvpn-{name}.log")
            if legacy.exists():
                log_file = legacy
        if not log_file.exists():
            raise FileNotFoundError(log_path_str)
        return log_file

    def _show_device_bind_log_viewer(self):
        """展示 device-bind.sh 追加写入的绑定审计日志（路径见 constants.DEVICE_BIND_LOG_FILE）。"""
        log_path = DEVICE_BIND_LOG_FILE

        def load() -> tuple[str, str]:
            p = log_path.resolve()
            return f"日志: {p}", self._read_log_tail(log_path)

        self._open_log_tail_dialog(
            "设备绑定日志",
            load,
            watch_path=lambda: DEVICE_BIND_LOG_FILE,
            caption_for_path=lambda p: f"日志: {p.resolve()}",
        )

    def _show_status_log_viewer(self, instance_name: str):
        """展示指定实例的 status 文件（路径从该实例 .conf 的 status 指令解析）。"""
        conf_dir = self._conf_dir

        def load() -> tuple[str, str]:
            p = resolve_status_log_path(instance_name, openvpn_conf_dir=conf_dir)
            pl = f"日志: {p}"
            try:
                body = self._read_log_tail(p)
            except FileNotFoundError:
                body = f"(尚无文件)\n{p}"
            return pl, body

        self._open_log_tail_dialog(
            f"状态 — {instance_name}",
            load,
            precheck_file=False,
            watch_path=lambda: resolve_status_log_path(instance_name, openvpn_conf_dir=conf_dir),
            caption_for_path=lambda p: f"日志: {p}",
        )

    def _refresh_peer_client_list(self) -> None:
        """刷新对端客户端实例列表，并保持并发限制与代次保护。"""

        if self._peer_list_container is None:
            return
        self._peer_refresh_running = True

        try:
            peers = self._peer_service.list_all()
        except Exception as exc:
            logger.exception("读取对端服务列表失败")
            self._peer_refresh_running = False
            if self._peer_total_chip is not None:
                self._peer_total_chip.text = "加载失败"
            self._peer_list_container.clear()
            with self._peer_list_container:
                error_state(
                    str(exc),
                    title="对端服务列表加载失败",
                    on_retry=self._refresh_peer_client_list,
                )
            return

        if self._peer_total_chip is not None:
            self._peer_total_chip.text = f"{len(peers)} 个"

        self._peer_list_container.clear()
        self._peer_refresh_generation += 1
        refresh_generation = self._peer_refresh_generation
        self._peer_status_refreshers = []

        if not peers:
            self._peer_refresh_running = False
            with self._peer_list_container:
                empty_state(
                    "暂无对端服务",
                    description="请先在“对端站点”创建对端并配置 SSH。",
                    icon="hub",
                )
            return

        focus = self._focus_peer_id
        rows = sorted(
            peers,
            key=lambda peer: (
                0 if str(peer.get("id") or "") == focus else 1,
                str(peer.get("name") or ""),
            ),
        )
        with self._peer_list_container:
            for peer in rows:
                self._peer_status_refreshers.append(self._render_peer_client_row(peer))

        async def refresh_all_peer_rows() -> None:
            semaphore = asyncio.Semaphore(self._peer_refresh_concurrency)

            async def refresh_one(ref: Callable[[], Any]) -> None:
                if (
                    self._active_tab != "peers"
                    or refresh_generation != self._peer_refresh_generation
                ):
                    return
                async with semaphore:
                    if (
                        self._active_tab != "peers"
                        or refresh_generation != self._peer_refresh_generation
                    ):
                        return
                    await ref()

            try:
                await asyncio.gather(
                    *(refresh_one(ref) for ref in self._peer_status_refreshers),
                    return_exceptions=True,
                )
            finally:
                if refresh_generation == self._peer_refresh_generation:
                    self._peer_refresh_running = False

        ui.timer(0.1, refresh_all_peer_rows, once=True)

    def _render_peer_client_row(self, peer: dict) -> Callable[[], Any]:
        """渲染对端客户端服务，并按远端状态动态更新操作能力。"""

        peer_id = str(peer.get("id") or "").strip()
        initial_view = adapt_peer_service_view(
            peer,
            busy=peer_id in self._peer_action_busy,
        )
        refs: dict[str, Any] = {}

        with ui.element("article").classes("mgmt-record-card services-record-card"):
            with ui.element("div").classes("mgmt-record-main services-record-main"):
                with ui.element("div").classes("services-record-icon"):
                    ui.icon("cloud", size="22px").classes("services-record-icon__glyph")

                with ui.element("div").classes("mgmt-record-copy services-record-copy"):
                    with ui.row().classes("items-center gap-2 flex-wrap"):
                        ui.label(str(initial_view["name"])).classes("services-record-name")
                        ui.label("PEER").classes("services-record-proto")
                        refs["status_box"] = ui.row().classes("items-center")

                    with ui.element("div").classes("mgmt-record-meta services-record-meta"):
                        with ui.element("span").classes("mgmt-meta-item"):
                            ui.icon("dns", size="12px")
                            refs["endpoint"] = ui.label(
                                f"{initial_view['ssh_username']}@{initial_view['host']}"
                            )
                        with ui.element("span").classes("mgmt-meta-item"):
                            ui.icon("person", size="12px")
                            ui.label(f"绑 {initial_view['bound_username']}")
                        refs["service"] = ui.label("服务: 待查询").classes("mgmt-meta-item")
                        refs["detail"] = ui.label("状态: 待查询").classes("mgmt-meta-item")

            with ui.element("div").classes("mgmt-actions services-record-actions"):
                refs["power"] = secondary_action(
                    "启动",
                    icon="play_arrow",
                    on_click=lambda: do_toggle_power(),
                    disabled=True,
                )
                refs["restart"] = secondary_action(
                    "重启",
                    icon="restart_alt",
                    on_click=lambda: do_action("restart"),
                    disabled=True,
                )
                refs["boot"] = secondary_action(
                    "启用自启",
                    icon="done_all",
                    on_click=lambda: do_toggle_boot(),
                    disabled=True,
                )
                refs["log"] = secondary_action(
                    "日志",
                    icon="article",
                    on_click=lambda p=peer_id, n=str(initial_view["name"]): self._show_peer_client_file_log_viewer(p, n),
                    disabled=True,
                )

        state: dict[str, Any] = {"view": initial_view}

        def render_status(view: dict[str, Any]) -> None:
            state["view"] = view
            refs["status_box"].clear()
            with refs["status_box"]:
                status_badge(
                    str(view["status_tone"]),
                    label=str(view["status_label"]),
                    tooltip=str(view["detail"] or view["error"] or ""),
                )
            refs["service"].text = f"服务: {view['service']}"
            refs["detail"].text = f"状态: {view['detail'] or view['status_label']}"

            power = refs["power"]
            if view["can_stop"]:
                power.set_text("停止")
                power.props("icon=stop")
                power.enable()
            elif view["can_start"]:
                power.set_text("启动")
                power.props("icon=play_arrow")
                power.enable()
            else:
                power.disable()

            restart = refs["restart"]
            if view["can_restart"]:
                restart.enable()
            else:
                restart.disable()

            boot = refs["boot"]
            if view["can_disable"]:
                boot.set_text("禁用自启")
                boot.props("icon=block")
                boot.enable()
            elif view["can_enable"]:
                boot.set_text("启用自启")
                boot.props("icon=done_all")
                boot.enable()
            else:
                boot.disable()

            if view["can_view_logs"]:
                refs["log"].enable()
            else:
                refs["log"].disable()

        render_status(initial_view)

        async def refresh_status() -> None:
            if not peer_id or self._active_tab != "peers":
                return
            render_status(
                adapt_peer_service_view(
                    peer,
                    busy=peer_id in self._peer_action_busy,
                )
            )
            try:
                data = await run.io_bound(
                    self._peer_service.fetch_peer_client_service_status_via_ssh,
                    peer_id,
                )
            except Exception as exc:
                if self._active_tab != "peers":
                    return
                logger.error("查询对端客户端实例失败 peer=%s: %s", peer_id, exc)
                render_status(adapt_peer_service_view(peer, error=str(exc)))
                return
            if self._active_tab != "peers":
                return
            render_status(adapt_peer_service_view(peer, data))

        async def do_action(action: str) -> None:
            render_status(
                adapt_peer_service_view(
                    peer,
                    {
                        "exists": state["view"]["exists"],
                        "active_state": state["view"]["active_state"],
                        "unit_file_state": state["view"]["enabled_state"],
                        "config_exists": state["view"]["config_exists"],
                        "service": state["view"]["service"],
                    },
                    busy=True,
                )
            )
            await self._run_peer_service_action(peer_id, action)
            await refresh_status()

        async def do_toggle_power() -> None:
            action = "stop" if bool(state["view"].get("can_stop")) else "start"
            await do_action(action)

        async def do_toggle_boot() -> None:
            action = "disable" if bool(state["view"].get("can_disable")) else "enable"
            await do_action(action)

        return refresh_status

    async def _run_peer_service_action(self, peer_id: str, action: str) -> bool:
        """执行对端 systemd 动作，并按 peer_id 互斥。"""

        peer_id = str(peer_id or "").strip()
        if not peer_id:
            ui.notify("缺少对端 ID", type="negative")
            return False
        if action not in {"start", "stop", "restart", "enable", "disable"}:
            raise ValueError(f"不支持的对端服务操作: {action}")
        if peer_id in self._peer_action_busy:
            ui.notify("该对端已有服务操作执行中，请稍候", type="info")
            return False

        self._peer_action_busy.add(peer_id)
        try:
            await run.io_bound(
                lambda: self._peer_service.control_peer_client_service_via_ssh(
                    peer_id,
                    action,
                )
            )
        except Exception as exc:
            logger.error(
                "对端客户端实例操作失败 peer=%s action=%s: %s",
                peer_id,
                action,
                exc,
            )
            ui.notify(f"{action} 失败: {exc}", type="negative")
            return False
        finally:
            self._peer_action_busy.discard(peer_id)

        ui.notify(f"已执行 {action}", type="positive")
        return True

    def _show_peer_client_file_log_viewer(self, peer_id: str, peer_name: str) -> None:
        """查看对端 openvpn-client 文件日志；关闭后不再写入失效元素。"""

        state = {"open": True, "generation": 0}

        with ui.dialog() as dialog, ui.card().classes("w-full max-w-4xl services-view-dialog"):
            dialog.on("hide", lambda _event: state.update(open=False, generation=state["generation"] + 1))
            ui.label(f"对端日志 — {peer_name}").classes("text-h6 services-view-dialog__title")
            path_caption = ui.label("/etc/openvpn/log/client.log").classes("section-caption services-view-dialog__caption")
            log_container = ui.column().classes("w-full log-frame services-log-frame")
            log_ref: dict[str, Any] = {"label": None}

            def set_body(text: str) -> None:
                if not state["open"]:
                    return
                if log_ref["label"] is None:
                    log_container.clear()
                    with log_container:
                        with ui.element("div").classes("w-full vpn-log-tail-viewport").style(
                            _LOG_TAIL_CODE_STYLE
                        ):
                            log_ref["label"] = ui.label(text).classes(
                                "w-full whitespace-pre-wrap break-words font-mono text-left"
                            ).style("font-size: 12px;")
                else:
                    cast(Any, log_ref["label"]).text = text

            async def refresh_log() -> None:
                if not state["open"]:
                    return
                state["generation"] += 1
                generation = state["generation"]
                set_body("读取…")
                try:
                    data = await run.io_bound(
                        lambda: self._peer_service.fetch_peer_client_service_logs_via_ssh(
                            peer_id,
                            lines=_LOG_SNAPSHOT_MAX_LINES,
                        )
                    )
                except Exception as exc:
                    if not state["open"] or generation != state["generation"]:
                        return
                    logger.error("读取对端客户端日志失败 peer=%s: %s", peer_id, exc)
                    set_body(f"读取失败: {exc}")
                    ui.notify(f"读日志失败: {exc}", type="negative")
                    return

                if not state["open"] or generation != state["generation"]:
                    return
                path_caption.text = (
                    f"日志: {data.get('log_path') or '/etc/openvpn/log/client.log'}"
                )
                set_body(str(data.get("log") or "(空)"))

            ui.timer(0.1, refresh_log, once=True)
            with ui.row().classes("justify-end q-mt-md gap-sm items-center"):
                secondary_action("刷新", icon="refresh", on_click=refresh_log)
                secondary_action("关闭", on_click=dialog.close)

        dialog.open()

    def _render_instance_row(self, status: InstanceStatus, instance_config: dict):
        """使用统一状态和操作层级渲染本机实例卡片。"""

        view = adapt_local_service_view(
            status,
            instance_config,
            busy=status.name in self._local_instance_busy,
        )

        with ui.element("article").classes("mgmt-record-card services-record-card"):
            with ui.element("div").classes("mgmt-record-main services-record-main"):
                with ui.element("div").classes("services-record-icon"):
                    ui.icon(
                        "dns",
                        size="22px",
                    ).classes("text-primary")

                with ui.element("div").classes("mgmt-record-copy services-record-copy"):
                    with ui.row().classes("items-center gap-2 flex-wrap"):
                        ui.label(str(view["name"])).classes("services-record-name")
                        ui.label(str(view["proto"])).classes("services-record-proto")
                        status_badge(
                            str(view["status_tone"]),
                            label=str(view["status_label"]),
                        )

                    with ui.element("div").classes("mgmt-record-meta services-record-meta"):
                        structured_value(view["port"], kind="port")
                        with ui.element("span").classes("mgmt-meta-item"):
                            ui.icon("people", size="12px")
                            ui.label(f"在线 {view['client_count']}")
                        with ui.element("span").classes("mgmt-meta-item"):
                            ui.icon("swap_vert", size="12px")
                            ui.label(
                                f"↓{view['traffic_received']} ↑{view['traffic_sent']}"
                            )
                        structured_value(view["subnet"], kind="cidr")

            with ui.element("div").classes("mgmt-actions services-record-actions"):
                if view["can_start"]:
                    ui.button(
                        icon="play_arrow",
                        on_click=lambda name=status.name: self._confirm_start(name),
                    ).props("flat round dense").classes(
                        "services-icon-action services-icon-action--primary"
                    ).tooltip("启动")
                elif view["can_stop"]:
                    ui.button(
                        icon="stop",
                        on_click=lambda name=status.name: self._confirm_stop(name),
                    ).props("flat round dense").classes(
                        "services-icon-action"
                    ).tooltip("停止")

                restart_button = ui.button(
                    icon="restart_alt",
                    on_click=lambda name=status.name: self._confirm_restart(name),
                ).props("flat round dense").classes(
                    "services-icon-action"
                ).tooltip("重启")
                if not bool(view["can_restart"]) or bool(view["busy"]):
                    restart_button.disable()

                ui.button(
                    icon="article",
                    on_click=lambda name=status.name: self._show_log_viewer(name),
                ).props("flat round dense").classes(
                    "services-icon-action"
                ).tooltip("守护进程日志")

                ui.button(
                    icon="fingerprint",
                    on_click=self._show_device_bind_log_viewer,
                ).props("flat round dense").classes(
                    "services-icon-action"
                ).tooltip("设备绑定日志")

                ui.button(
                    icon="poll",
                    on_click=lambda name=status.name: self._show_status_log_viewer(name),
                ).props("flat round dense").classes(
                    "services-icon-action"
                ).tooltip("OpenVPN 状态文件")

                config_button = ui.button(
                    icon="edit_note",
                    on_click=lambda name=status.name: self._open_config_editor(name),
                ).props("flat round dense").classes(
                    "services-icon-action"
                ).tooltip("编辑配置")
                if not bool(view["can_edit_config"]):
                    config_button.disable()

    @staticmethod
    def _format_traffic(n: int) -> str:
        """本实例 status 累计流量可读格式。"""
        size = float(max(n, 0))
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                if unit == "B":
                    return f"{int(size)} {unit}"
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{n} B"

    def _show_log_viewer(self, name: str):
        """读取实例配置文件中 log-append 指定的日志路径，展示最新日志。"""

        def load() -> tuple[str, str]:
            p = self._resolve_instance_daemon_log_path(name)
            return f"日志: {p}", self._read_log_tail(p)

        self._open_log_tail_dialog(
            f"实例日志 — {name}",
            load,
            watch_path=lambda: self._resolve_instance_daemon_log_path(name),
            caption_for_path=lambda p: f"日志: {p}",
        )

    def _confirm_start(self, name: str):
        """启动前确认。"""

        confirm_dialog.show(
            f"启动 {name}？",
            on_confirm=lambda: self._queue_local_action(name, "start"),
            title="启动实例",
            impact="将启动本机 OpenVPN systemd 实例。",
            danger_level="normal",
        )

    def _confirm_stop(self, name: str):
        """停止前确认，明确在线会话影响。"""

        confirm_dialog.show(
            f"停止 {name}？",
            on_confirm=lambda: self._queue_local_action(name, "stop"),
            title="停止实例",
            impact="当前在线用户会立即断开，需要客户端重新连接。",
            danger_level="danger",
        )

    def _confirm_restart(self, name: str):
        """重启前确认。"""

        confirm_dialog.show(
            f"重启 {name}？",
            on_confirm=lambda: self._queue_local_action(name, "restart"),
            title="重启实例",
            impact="在线会话会短暂中断，并重新加载当前配置。",
            danger_level="warning",
        )

    def _do_start(self, name: str):
        """兼容旧调用入口。"""

        self._queue_local_action(name, "start")

    def _do_stop(self, name: str):
        """兼容旧调用入口。"""

        self._queue_local_action(name, "stop")

    def _do_restart(self, name: str):
        """兼容旧调用入口。"""

        self._queue_local_action(name, "restart")

    def _queue_local_action(self, name: str, action: str) -> bool:
        """在创建后台任务前占用实例，消除重复点击竞态。"""

        if name in self._local_instance_busy:
            ui.notify(f"{name} 尚有本机操作进行中，请稍候", type="warning")
            return False
        if action not in {"start", "stop", "restart"}:
            raise ValueError(f"不支持的本机服务操作: {action}")

        self._local_instance_busy.add(name)
        try:
            background_tasks.create(
                self._async_systemctl_action(name, action, reserved=True),
                name=f"ovpn-local-{name}-{action}",
            )
        except RuntimeError as exc:
            self._local_instance_busy.discard(name)
            logger.error(
                "调度本机服务任务失败 instance=%s action=%s: %s",
                name,
                action,
                exc,
            )
            ui.notify(f"{name} {action} 任务未能排队", type="negative")
            return False
        return True

    async def _async_systemctl_action(
        self,
        name: str,
        action: str,
        *,
        reserved: bool = False,
    ) -> None:
        """在线程池执行 systemctl；结束后释放互斥并刷新。"""

        if not reserved:
            if name in self._local_instance_busy:
                self._notify_for_page(f"{name} 尚有本机操作进行中，请稍候", type="warning")
                return
            self._local_instance_busy.add(name)

        labels = {
            "start": ("正在启动", "已启动", "启动失败"),
            "stop": ("正在停止", "已停止", "停止失败"),
            "restart": ("正在重启", "已重启", "重启失败"),
        }
        progress_message, success_message, failure_message = labels[action]
        try:
            self._notify_for_page(
                f"{progress_message} {name}…（后台执行 systemctl）",
                type="info",
            )
            if action == "start":
                ok = await run.io_bound(start_instance, name)
            elif action == "stop":
                ok = await run.io_bound(stop_instance, name)
            else:
                ok = await run.io_bound(restart_instance, name)
        except Exception as exc:
            logger.exception(
                "本机实例 systemctl 异常 instance=%s action=%s",
                name,
                action,
            )
            self._notify_for_page(
                f"{name} {failure_message}: {exc}",
                type="negative",
            )
            self._refresh_list_with_client()
            return
        finally:
            self._local_instance_busy.discard(name)

        if ok:
            self._notify_for_page(f"{name} {success_message}", type="positive")
        else:
            self._notify_for_page(f"{name} {failure_message}", type="negative")
        self._refresh_list_with_client()

    def _open_config_editor(self, name: str):
        """使用统一表单编辑实例配置；保存失败时保留输入。"""

        conf_file = resolve_server_conf_dir(self._conf_dir) / f"{name}.conf"
        conf_path = str(conf_file)
        if not conf_file.exists():
            ui.notify(f"配置文件不存在: {conf_path}", type="negative")
            return

        try:
            backup_path = backup_before_edit(conf_path)
            content = conf_file.read_text(encoding="utf-8")
        except Exception as exc:
            logger.exception("打开实例配置失败 instance=%s", name)
            ui.notify(f"读取或备份配置失败: {exc}", type="negative")
            return

        fields: dict[str, Any] = {}

        def body() -> None:
            ui.label(f"已备份：{backup_path}").classes("section-caption")
            ui.label("保存成功后需要重启实例才能生效。").classes("section-caption")
            fields["content"] = (
                ui.textarea(value=content)
                .classes("w-full font-mono")
                .props("outlined rows=20")
            )

        def submit() -> bool:
            return self._save_config_content(
                conf_path,
                str(fields["content"].value or ""),
            )

        form = create_form_dialog(
            f"编辑配置 — {name}.conf",
            body=body,
            on_submit=submit,
            submit_label="保存",
            close_on_success=True,
        )
        form.card.classes("w-full max-w-4xl")
        form.open()

    def _save_config_content(self, conf_path: str, new_content: str) -> bool:
        """保存实例配置；失败返回 False 以保留统一表单。"""

        try:
            save_with_backup(conf_path, new_content)
        except Exception as exc:
            logger.exception("保存实例配置失败 path=%s", conf_path)
            ui.notify(f"保存失败: {exc}", type="negative")
            return False
        ui.notify("已保存配置；请重启实例使新配置生效", type="warning")
        return True

    def _do_save_config(self, dialog, conf_path: str, new_content: str):
        """兼容旧调用入口。"""

        success = self._save_config_content(conf_path, new_content)
        if success and dialog is not None:
            dialog.close()
        return success

