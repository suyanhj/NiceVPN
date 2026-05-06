# -*- coding: utf-8 -*-
"""服务管理页面。"""

import asyncio
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from nicegui import background_tasks, context, run, ui
from nicegui.functions.notify import ARG_MAP as _NICEGUI_NOTIFY_ARG_MAP

from app.core.config import load_config
from app.core.constants import DEVICE_BIND_LOG_FILE, OPENVPN_DAEMON_LOG_DIR
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
from app.ui.components import confirm_dialog

logger = logging.getLogger(__name__)

# 日志正文外层可滚动视口（供实时 tail 判断是否贴底并自动跟随）
_LOG_VIEWPORT_ID = "vpn-log-tail-viewport"
_LOG_TAIL_CODE_STYLE = "max-height: 500px; overflow-y: auto; overflow-x: auto; font-size: 12px;"
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
        self._refresh_button: Any = None
        # 本机 systemctl 启停与事件循环解耦：同实例不并发重复点
        self._local_instance_busy: set[str] = set()
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
        """渲染服务管理页面。"""
        try:
            self._ng_client = context.client
        except RuntimeError:
            self._ng_client = None
        config = load_config()
        self._instances: dict = dict(config.get("instances", {}))
        self._conf_dir: str = config.get("openvpn_conf_dir", "/etc/openvpn")
        self._active_tab = "peers" if initial_tab == "peers" else "local"
        self._focus_peer_id = str(focus_peer_id or "").strip()

        with ui.column().classes("page-shell mgmt-page"):
            with ui.element("section").classes("mgmt-panel"):
                with ui.element("div").classes("mgmt-header-row"):
                    with ui.element("div").classes("mgmt-header-copy"):
                        ui.label("服务管理").classes("mgmt-title")
                        ui.label("本机 OpenVPN 实例与对端客户端服务；启停、日志与配置。").classes("mgmt-desc")

            with ui.element("div").classes("firewall-control-header firewall-tab-bar"):
                with ui.tabs(value=self._active_tab, on_change=self._on_service_tab_change).classes(
                    "firewall-compact-tabs"
                ) as self._service_tabs:
                    ui.tab("local", label="本机实例")
                    ui.tab("peers", label="对端实例")
                with ui.element("div").classes("firewall-btn-group"):
                    self._refresh_button = ui.button("刷新", icon="refresh", on_click=self._refresh_current_tab).props(
                        "outline dense no-caps no-ripple"
                    ).classes("mgmt-toolbar-btn is-outline")

            with ui.tab_panels(self._service_tabs, value=self._active_tab, keep_alive=False).classes(
                "w-full min-h-0 flex-1 firewall-tabpanels"
            ):
                with ui.tab_panel("local"):
                    with ui.element("section").classes("mgmt-panel mgmt-panel-list"):
                        with ui.element("div").classes("mgmt-list-head"):
                            with ui.element("div").classes("service-list-meta"):
                                ui.label("本机实例").classes("mgmt-kicker")
                                self._running_chip = ui.label("0 Running").classes("service-running-chip")

                        self._list_container = ui.column().classes("mgmt-record-list")
                        self._refresh_list()
                with ui.tab_panel("peers"):
                    with ui.element("section").classes("mgmt-panel mgmt-panel-list"):
                        with ui.element("div").classes("mgmt-list-head"):
                            with ui.element("div").classes("service-list-meta"):
                                ui.label("对端实例").classes("mgmt-kicker")
                                self._peer_total_chip = ui.label("0 PEERS").classes("service-running-chip")

                        self._peer_list_container = ui.column().classes("mgmt-record-list")
                        self._refresh_peer_client_list()

    def _on_service_tab_change(self, e) -> None:
        self._active_tab = str(getattr(e, "value", None) or "local")
        if self._active_tab != "peers":
            self._peer_refresh_generation += 1
            self._peer_status_refreshers = []
            self._peer_refresh_running = False
            self._set_refresh_button_busy(False)
        self._refresh_current_tab()

    def _refresh_current_tab(self) -> None:
        if self._active_tab == "peers":
            if self._peer_refresh_running:
                ui.notify("对端状态刷新中，请稍候", type="info")
                return
            self._refresh_peer_client_list()
        else:
            self._refresh_list()

    def _set_refresh_button_busy(self, busy: bool) -> None:
        if self._refresh_button is None:
            return
        if busy:
            self._refresh_button.disable()
            self._refresh_button.set_text("刷新中")
        else:
            self._refresh_button.enable()
            self._refresh_button.set_text("刷新")

    def _refresh_list(self):
        """刷新实例列表。

        先使用 config.instances 中注册的名称；
        再扫描 /etc/openvpn/server/*.conf 文件发现未注册但已安装的实例（实例可见性优化）。
        """
        if self._list_container is None:
            return

        self._list_container.clear()
        instance_names = self._collect_instance_names()

        if not instance_names:
            if self._running_chip is not None:
                self._running_chip.text = "0 Running"
            with self._list_container:
                ui.label("暂无本机实例。").classes("service-empty")
            return

        statuses = self._monitor.check_all_instances(instance_names)
        running_count = sum(1 for item in statuses if item.active)
        if self._running_chip is not None:
            self._running_chip.text = f"{running_count} Running"

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

    @staticmethod
    def _install_log_viewport_tail_follow_binding() -> None:
        """在 document 上监听日志视口滚动，维护 window.__vpn_log_follow_tail（贴底才在实时刷新时跟新行）。"""
        vid = json.dumps(_LOG_VIEWPORT_ID)
        thresh = _LOG_TAIL_STICK_PX
        ui.run_javascript(
            f"""
            (function() {{
              if (window.__vpn_log_tail_follow_installed) return;
              window.__vpn_log_tail_follow_installed = true;
              window.__vpn_log_follow_tail = true;
              document.addEventListener(
                "scroll",
                function(ev) {{
                  const t = ev.target;
                  if (!t || t.id !== {vid}) return;
                  window.__vpn_log_follow_tail =
                    t.scrollHeight - t.clientHeight - t.scrollTop <= {thresh};
                }},
                true
              );
            }})();
            """
        )

    @staticmethod
    def _scroll_log_viewport_if_follow_tail() -> None:
        """正文更新后若用户仍在底部，则滚到最新（等布局完成再设 scrollTop）。"""
        vid = json.dumps(_LOG_VIEWPORT_ID)
        ui.run_javascript(
            f"""
            requestAnimationFrame(function() {{
              requestAnimationFrame(function() {{
                setTimeout(function() {{
                  if (!window.__vpn_log_follow_tail) return;
                  const el = document.getElementById({vid});
                  if (el) el.scrollTop = el.scrollHeight;
                }}, 0);
              }});
            }});
            """
        )

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

        with ui.dialog() as dialog, ui.card().classes("w-full max-w-4xl"):
            ui.label(title).classes("text-h6")
            header_ctrl = header_widget() if header_widget else None
            path_caption = ui.label(initial[0] if initial else "").classes("section-caption")
            log_container = ui.column().classes("w-full log-frame")
            log_label_ref: dict[str, Any] = {"lbl": None}

            def set_log_body(text: str, *, follow_tail_after: bool) -> None:
                """固定外层滚动 div，只改 label 正文，避免 clear 掉视口导致滚动与贴底判断失效。"""
                if log_label_ref["lbl"] is None:
                    log_container.clear()
                    with log_container:
                        with ui.element("div").props(f"id={_LOG_VIEWPORT_ID}").classes(
                            "w-full vpn-log-tail-viewport"
                        ).style(_LOG_TAIL_CODE_STYLE):
                            log_label_ref["lbl"] = ui.label(text).classes(
                                "w-full whitespace-pre-wrap break-words font-mono text-left"
                            ).style("font-size: 12px;")
                    ServicesPage._install_log_viewport_tail_follow_binding()
                else:
                    cast(Any, log_label_ref["lbl"]).text = text
                if follow_tail_after:
                    ServicesPage._scroll_log_viewport_if_follow_tail()

            if initial:
                set_log_body(initial[1], follow_tail_after=False)

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

            def close_log_dialog() -> None:
                if poll_timer is not None:
                    poll_timer.deactivate()
                dialog.close()

            with ui.row().classes("justify-end q-mt-md gap-sm items-center"):
                ui.button("刷新", icon="refresh", on_click=do_manual_refresh).props("outline")
                ui.button("关闭", on_click=close_log_dialog).props("flat")
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
        """刷新对端客户端实例列表（不主动 SSH 扫描，避免打开页面阻塞）。"""
        if self._peer_list_container is None:
            return
        self._peer_refresh_running = True
        self._set_refresh_button_busy(True)
        peers = self._peer_service.list_all()
        if self._peer_total_chip is not None:
            self._peer_total_chip.text = f"{len(peers)} PEERS"
        self._peer_list_container.clear()
        self._peer_refresh_generation += 1
        refresh_generation = self._peer_refresh_generation
        self._peer_status_refreshers = []
        if not peers:
            self._peer_refresh_running = False
            self._set_refresh_button_busy(False)
            with self._peer_list_container:
                ui.label("无对端，请先在「对端站点」添加。").classes("service-empty")
            return
        focus = self._focus_peer_id
        rows = sorted(peers, key=lambda p: (0 if str(p.get("id") or "") == focus else 1, p.get("name") or ""))
        with self._peer_list_container:
            for peer in rows:
                self._peer_status_refreshers.append(self._render_peer_client_row(peer))

        async def refresh_all_peer_rows() -> None:
            semaphore = asyncio.Semaphore(self._peer_refresh_concurrency)

            async def refresh_one(ref: Callable[[], Any]) -> None:
                if self._active_tab != "peers" or refresh_generation != self._peer_refresh_generation:
                    return
                async with semaphore:
                    if self._active_tab != "peers" or refresh_generation != self._peer_refresh_generation:
                        return
                    await ref()

            try:
                await asyncio.gather(*(refresh_one(ref) for ref in self._peer_status_refreshers), return_exceptions=True)
            finally:
                if refresh_generation == self._peer_refresh_generation:
                    self._peer_refresh_running = False
                    self._set_refresh_button_busy(False)

        ui.timer(0.1, refresh_all_peer_rows, once=True)

    def _render_peer_client_row(self, peer: dict) -> Callable[[], Any]:
        """渲染一个对端客户端 systemd 实例卡片。"""
        pid = str(peer.get("id") or "").strip()
        name = str(peer.get("name") or pid)
        host = str(peer.get("ssh_host") or "未配 SSH")
        user = str(peer.get("ssh_username") or "—")
        bound = str(peer.get("bound_username") or "—")
        status_ref: dict[str, Any] = {"active": "unknown", "enabled": "unknown"}
        controls_ref: dict[str, Any] = {"power": None, "boot": None}

        with ui.element("div").classes("mgmt-record-card"):
            with ui.element("div").classes("mgmt-record-main"):
                with ui.element("div").classes("service-status-shell"):
                    status_ref["dot"] = ui.element("span").classes("service-status-dot is-offline")
                with ui.element("div").classes("mgmt-record-copy"):
                    with ui.element("div").classes("service-name-row"):
                        ui.label(name).classes("service-name")
                        ui.label("PEER").classes("service-proto")
                    with ui.element("div").classes("mgmt-record-meta"):
                        with ui.element("span").classes("mgmt-meta-item"):
                            ui.icon("dns", size="12px")
                            ui.label(f"{user}@{host}")
                        with ui.element("span").classes("mgmt-meta-item"):
                            ui.icon("person", size="12px")
                            ui.label(f"绑 {bound}")
                        with ui.element("span").classes("mgmt-meta-item"):
                            status_ref["service"] = ui.label("服务: …")
                        with ui.element("span").classes("mgmt-meta-item"):
                            status_ref["state"] = ui.label("状态: …")

            def update_action_buttons() -> None:
                power_btn = controls_ref.get("power")
                boot_btn = controls_ref.get("boot")
                active = str(status_ref.get("active") or "unknown")
                enabled = str(status_ref.get("enabled") or "unknown")
                if power_btn is not None:
                    if active == "active":
                        power_btn.props("flat round dense no-caps no-ripple icon=stop")
                        power_btn.classes(replace="mgmt-icon-btn is-stop")
                    else:
                        power_btn.props("flat round dense no-caps no-ripple icon=play_arrow")
                        power_btn.classes(replace="mgmt-icon-btn is-start")
                if boot_btn is not None:
                    if enabled.startswith("enabled"):
                        boot_btn.props("flat round dense no-caps no-ripple icon=block")
                        boot_btn.classes(replace="mgmt-icon-btn is-neutral")
                    else:
                        boot_btn.props("flat round dense no-caps no-ripple icon=done_all")
                        boot_btn.classes(replace="mgmt-icon-btn is-start")

            async def refresh_status() -> None:
                if not pid:
                    status_ref["service"].text = "服务: 失败"
                    status_ref["state"].text = "状态: 无 ID"
                    status_ref["active"] = "unknown"
                    status_ref["enabled"] = "unknown"
                    update_action_buttons()
                    return
                if self._active_tab != "peers":
                    return
                status_ref["state"].text = "状态: 查询…"
                try:
                    data = await run.io_bound(self._peer_service.fetch_peer_client_service_status_via_ssh, pid)
                except Exception as exc:
                    if self._active_tab != "peers":
                        return
                    logger.error("查询对端客户端实例失败 peer=%s: %s", pid, exc)
                    status_ref["service"].text = "服务: 失败"
                    status_ref["state"].text = f"状态: {exc}"
                    status_ref["dot"].classes(replace="service-status-dot is-offline")
                    status_ref["active"] = "unknown"
                    status_ref["enabled"] = "unknown"
                    update_action_buttons()
                    return
                if self._active_tab != "peers":
                    return
                if not data.get("exists"):
                    status_ref["service"].text = "服务: 未装"
                    status_ref["state"].text = "状态: not-found"
                    status_ref["dot"].classes(replace="service-status-dot is-offline")
                    status_ref["active"] = "not-found"
                    status_ref["enabled"] = "unknown"
                    update_action_buttons()
                    return
                service = str(data.get("service") or "—")
                active = str(data.get("active_state") or data.get("is_active") or "unknown")
                enabled = str(data.get("unit_file_state") or data.get("is_enabled") or "unknown")
                config = "config ok" if data.get("config_exists") else "config missing"
                status_ref["service"].text = f"服务: {service}"
                status_ref["state"].text = f"状态: {active} / {enabled} / {config}"
                status_ref["active"] = active
                status_ref["enabled"] = enabled
                dot_class = "service-status-dot is-online" if active == "active" else "service-status-dot is-offline"
                status_ref["dot"].classes(replace=dot_class)
                update_action_buttons()

            async def do_action(action: str) -> None:
                if not pid:
                    ui.notify("缺少对端 ID", type="negative")
                    return
                try:
                    await run.io_bound(lambda: self._peer_service.control_peer_client_service_via_ssh(pid, action))
                except Exception as exc:
                    logger.error("对端客户端实例操作失败 peer=%s action=%s: %s", pid, action, exc)
                    ui.notify(f"{action} 失败: {exc}", type="negative")
                    return
                ui.notify(f"已执行 {action}", type="positive")
                await refresh_status()

            async def do_toggle_power() -> None:
                action = "stop" if str(status_ref.get("active") or "") == "active" else "start"
                await do_action(action)

            async def do_restart() -> None:
                await do_action("restart")

            async def do_toggle_boot() -> None:
                enabled = str(status_ref.get("enabled") or "")
                action = "disable" if enabled.startswith("enabled") else "enable"
                await do_action(action)

            with ui.element("div").classes("mgmt-actions"):
                ui.button(
                    icon="article",
                    on_click=lambda p=pid, n=name: self._show_peer_client_file_log_viewer(p, n),
                ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-neutral").tooltip(
                    "客户端日志"
                )
                controls_ref["power"] = ui.button(
                    icon="play_arrow",
                    on_click=do_toggle_power,
                ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-start").tooltip("启停")
                ui.button(
                    icon="restart_alt",
                    on_click=do_restart,
                ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-restart").tooltip("重启")
                controls_ref["boot"] = ui.button(
                    icon="done_all",
                    on_click=do_toggle_boot,
                ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-start").tooltip("自启开关")
            update_action_buttons()
            return refresh_status

    def _show_peer_client_file_log_viewer(self, peer_id: str, peer_name: str) -> None:
        """查看对端 openvpn-client 文件日志。"""
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-4xl"):
            ui.label(f"对端日志 — {peer_name}").classes("text-h6")
            path_caption = ui.label("/etc/openvpn/log/client.log").classes("section-caption")
            log_container = ui.column().classes("w-full log-frame")
            log_ref: dict[str, Any] = {"label": None}

            def set_body(text: str) -> None:
                if log_ref["label"] is None:
                    log_container.clear()
                    with log_container:
                        with ui.element("div").classes("w-full vpn-log-tail-viewport").style(_LOG_TAIL_CODE_STYLE):
                            log_ref["label"] = ui.label(text).classes(
                                "w-full whitespace-pre-wrap break-words font-mono text-left"
                            ).style("font-size: 12px;")
                else:
                    cast(Any, log_ref["label"]).text = text

            async def refresh_log() -> None:
                set_body("读取…")
                try:
                    data = await run.io_bound(
                        lambda: self._peer_service.fetch_peer_client_service_logs_via_ssh(
                            peer_id,
                            lines=_LOG_SNAPSHOT_MAX_LINES,
                        )
                    )
                except Exception as exc:
                    logger.error("读取对端客户端日志失败 peer=%s: %s", peer_id, exc)
                    set_body(f"读取失败: {exc}")
                    ui.notify(f"读日志失败: {exc}", type="negative")
                    return
                path_caption.text = f"日志: {data.get('log_path') or '/etc/openvpn/log/client.log'}"
                set_body(str(data.get("log") or "(空)"))

            ui.timer(0.1, refresh_log, once=True)
            with ui.row().classes("justify-end q-mt-md gap-sm items-center"):
                ui.button("刷新", icon="refresh", on_click=refresh_log).props("outline no-caps")
                ui.button("关闭", on_click=dialog.close).props("flat no-caps")
        dialog.open()

    def _render_instance_row(self, status: InstanceStatus, instance_config: dict):
        """渲染实例卡片。"""
        proto = str(instance_config.get("proto", "udp")).upper()
        port = instance_config.get("port", 1194)
        subnet = instance_config.get("subnet") or instance_config.get("server") or "子网未配"

        with ui.element("div").classes("mgmt-record-card"):
            with ui.element("div").classes("mgmt-record-main"):
                with ui.element("div").classes("service-status-shell"):
                    ui.element("span").classes(
                        "service-status-dot is-online" if status.active else "service-status-dot is-offline"
                    )

                with ui.element("div").classes("mgmt-record-copy"):
                    with ui.element("div").classes("service-name-row"):
                        ui.label(status.name).classes("service-name")
                        ui.label(proto).classes("service-proto")

                    with ui.element("div").classes("mgmt-record-meta"):
                        with ui.element("span").classes("mgmt-meta-item"):
                            ui.label(f"Port: {port}")
                        with ui.element("span").classes("mgmt-meta-item"):
                            ui.label(f"在线 {status.client_count}")
                        with ui.element("span").classes("mgmt-meta-item"):
                            ui.label(
                                f"↓{self._format_traffic(status.bytes_received)} "
                                f"↑{self._format_traffic(status.bytes_sent)}"
                            )
                        with ui.element("span").classes("mgmt-meta-item"):
                            ui.label("运行中" if status.active else "已停止")
                        with ui.element("span").classes("mgmt-meta-item is-network"):
                            ui.icon("router", size="12px")
                            ui.label(str(subnet))

            with ui.element("div").classes("mgmt-actions"):
                ui.button(
                    icon="article",
                    on_click=lambda name=status.name: self._show_log_viewer(name),
                ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-neutral").tooltip("守护进程日志")
                ui.button(
                    icon="fingerprint",
                    on_click=self._show_device_bind_log_viewer,
                ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-neutral").tooltip(
                    "设备绑定"
                )
                ui.button(
                    icon="poll",
                    on_click=lambda name=status.name: self._show_status_log_viewer(name),
                ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-neutral").tooltip("状态文件")
                ui.button(
                    icon="edit_note",
                    on_click=lambda name=status.name: self._open_config_editor(name),
                ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-neutral").tooltip("编辑 .conf")

                if status.active:
                    ui.button(
                        icon="restart_alt",
                        on_click=lambda name=status.name: self._confirm_restart(name),
                    ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-restart").tooltip("重启")
                    ui.button(
                        icon="stop",
                        on_click=lambda name=status.name: self._confirm_stop(name),
                    ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-stop").tooltip("停止")
                else:
                    ui.button(
                        icon="play_arrow",
                        on_click=lambda name=status.name: self._confirm_start(name),
                    ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-start").tooltip("启动")
                    restart_btn = ui.button(
                        icon="restart_alt",
                        on_click=lambda name=status.name: self._confirm_restart(name),
                    ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-restart").tooltip("重启")
                    restart_btn.disable()

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
        """启动前二次确认。"""
        confirm_dialog.show(
            f"启动 {name}？",
            on_confirm=lambda: self._do_start(name),
            title="启动",
            confirm_color="primary",
        )

    def _confirm_stop(self, name: str):
        """停止前二次确认。"""
        confirm_dialog.show(
            f"停止 {name}？在线用户将断开。",
            on_confirm=lambda: self._do_stop(name),
            title="停止",
        )

    def _confirm_restart(self, name: str):
        """重启前二次确认。"""
        confirm_dialog.show(
            f"重启 {name}？在线会话将短暂中断。",
            on_confirm=lambda: self._do_restart(name),
            title="重启",
            confirm_color="primary",
        )

    def _do_start(self, name: str):
        """执行启动：systemctl 在线程池执行，避免阻塞 UI。"""
        try:
            background_tasks.create(
                self._async_systemctl_action(name, "start"),
                name=f"ovpn-local-{name}-start",
            )
        except RuntimeError as exc:
            logger.error("调度本机启动任务失败 instance=%s: %s", name, exc)
            ui.notify(f"{name} 启动任务未能排队", type="negative")

    def _do_stop(self, name: str):
        """执行停止：同上。"""
        try:
            background_tasks.create(
                self._async_systemctl_action(name, "stop"),
                name=f"ovpn-local-{name}-stop",
            )
        except RuntimeError as exc:
            logger.error("调度本机停止任务失败 instance=%s: %s", name, exc)
            ui.notify(f"{name} 停止任务未能排队", type="negative")

    def _do_restart(self, name: str):
        """执行重启：同上。"""
        try:
            background_tasks.create(
                self._async_systemctl_action(name, "restart"),
                name=f"ovpn-local-{name}-restart",
            )
        except RuntimeError as exc:
            logger.error("调度本机重启任务失败 instance=%s: %s", name, exc)
            ui.notify(f"{name} 重启任务未能排队", type="negative")

    async def _async_systemctl_action(self, name: str, action: str) -> None:
        """在线程池执行 start_instance / stop_instance / restart_instance，结束后再刷新列表。"""
        if name in self._local_instance_busy:
            self._notify_for_page(f"{name} 尚有本机操作进行中，请稍候", type="warning")
            return
        self._local_instance_busy.add(name)
        labels = {
            "start": ("正在启动", "已启动", "启动失败"),
            "stop": ("正在停止", "已停止", "停止失败"),
            "restart": ("正在重启", "已重启", "重启失败"),
        }
        prog, ok_msg, fail_msg = labels[action]
        try:
            self._notify_for_page(f"{prog} {name}…（后台执行 systemctl）", type="info")
            if action == "start":
                ok = await run.io_bound(start_instance, name)
            elif action == "stop":
                ok = await run.io_bound(stop_instance, name)
            else:
                ok = await run.io_bound(restart_instance, name)
        except Exception as exc:
            logger.exception("本机实例 systemctl 异常 instance=%s action=%s", name, action)
            self._notify_for_page(f"{name} {fail_msg}: {exc}", type="negative")
            self._refresh_list_with_client()
            return
        finally:
            self._local_instance_busy.discard(name)

        if ok:
            self._notify_for_page(f"{name} {ok_msg}", type="positive")
        else:
            self._notify_for_page(f"{name} {fail_msg}", type="negative")
        self._refresh_list_with_client()

    def _open_config_editor(self, name: str):
        """打开配置编辑弹窗。"""
        conf_file = resolve_server_conf_dir(self._conf_dir) / f"{name}.conf"
        conf_path = str(conf_file)

        if not conf_file.exists():
            ui.notify(f"配置文件不存在: {conf_path}", type="negative")
            return

        try:
            backup_path = backup_before_edit(conf_path)
            ui.notify(f"已备份: {backup_path}", type="info")
        except Exception as exc:
            ui.notify(f"备份失败: {exc}", type="negative")
            return

        content = conf_file.read_text(encoding="utf-8")

        with ui.dialog() as dialog, ui.card().classes("w-full max-w-3xl"):
            ui.label(f"编辑配置 - {name}.conf").classes("text-h6")
            ui.label("保存后需重启实例生效。").classes("section-caption")

            textarea = ui.textarea(value=content).classes("w-full font-mono q-mt-md").props("outlined rows=20")

            with ui.row().classes("justify-end q-mt-md gap-sm"):
                ui.button("取消", on_click=dialog.close).props("flat")
                ui.button(
                    "保存",
                    icon="save",
                    on_click=lambda: self._do_save_config(dialog, conf_path, textarea.value),
                ).props("color=primary")

        dialog.open()

    def _do_save_config(self, dialog, conf_path: str, new_content: str):
        """保存配置。"""
        try:
            save_with_backup(conf_path, new_content)
            dialog.close()
            ui.notify("已保存，请重启实例", type="warning")
        except Exception as exc:
            ui.notify(f"保存失败: {exc}", type="negative")
