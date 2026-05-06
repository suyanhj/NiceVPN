# -*- coding: utf-8 -*-
"""对端站点实例（组网）管理页面：绑定用户、内网 CIDR、CCD iroute、中心 VPN_PEER"""

import html
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from nicegui import context, ui, run
from nicegui.functions.notify import ARG_MAP as _NICEGUI_NOTIFY_ARG_MAP

from app.core.constants import LOGS_DIR, OPENVPN_MIN_VERSION, OVPN_PROFILES_DIR
from app.services.peer_instance.peer_ssh_connect import peer_row_has_usable_ssh_auth
from app.services.group.crud import GroupService
from app.services.peer_instance.remote_peer_ovpn import default_remote_ovpn_path
from app.services.peer_instance.service import PeerService
from app.services.user.crud import UserService
from app.ui.components import confirm_dialog
from app.ui.copy_clipboard import copy_text_to_clipboard

logger = logging.getLogger(__name__)
peer_remote_log = logging.getLogger("peer.remote")
_PEER_REMOTE_LOG_MAX_LINES = 180
_PEER_REMOTE_LOG_POLL_SEC = 1.0


def _parse_lan_cidrs_text(raw: str) -> list[str]:
    """将多行或逗号分隔的 CIDR 文本拆成列表（去空、去首尾空白）。"""
    if not raw or not str(raw).strip():
        return []
    parts = re.split(r"[\s,;]+", str(raw).strip())
    return [p.strip() for p in parts if p.strip()]


class PeersPage:
    """对端实例列表与 CRUD。"""

    def __init__(self) -> None:
        self.peer_service = PeerService()
        self.user_service = UserService()
        self.group_service = GroupService()
        self.list_container: ui.column | None = None
        self._peers_total_label: ui.label | None = None
        self._busy_peer_ids: set[str] = set()
        self._ng_client: Any = None

    def _notify_for_page(
        self,
        message: Any,
        *,
        type: str | None = None,  # noqa: A001 与 NiceGUI / Quasar 参数一致
        position: str = "bottom",
        close_button: bool | str = False,
        color: str | None = None,
        multi_line: bool = False,
        **kwargs: Any,
    ) -> None:
        """在异步任务结束后发通知，不依赖可能已删除的弹窗 slot。"""
        client = self._ng_client
        if client is None and self.list_container is not None:
            client = getattr(self.list_container, "client", None)
        if client is None:
            logger.error("peers: 无 page client，无法弹通知: %s", message)
            return
        options = {
            _NICEGUI_NOTIFY_ARG_MAP.get(key, key): value
            for key, value in locals().items()
            if key not in ("self", "client", "message", "kwargs") and value is not None
        }
        options["message"] = str(message)
        options.update(kwargs)
        client.outbox.enqueue_message("notify", options, client.id)

    def _set_peer_row_busy(self, peer_id: str, busy: bool) -> None:
        """列表行内转圈：探测/安装期间提示该对端正在执行远端操作。"""
        pid = str(peer_id or "").strip()
        if not pid:
            return
        if busy:
            self._busy_peer_ids.add(pid)
        else:
            self._busy_peer_ids.discard(pid)
        self._refresh_list()

    def render(self) -> None:
        """渲染页面。"""
        try:
            self._ng_client = context.client
        except RuntimeError:
            self._ng_client = None
        peers = self.peer_service.list_all()

        with ui.column().classes("page-shell mgmt-page"):
            with ui.element("section").classes("mgmt-panel"):
                with ui.element("div").classes("mgmt-header-row"):
                    with ui.element("div").classes("mgmt-header-copy"):
                        with ui.row().classes("items-center gap-xs"):
                            ui.label("对端管理").classes("mgmt-title")
                            ui.button(
                                icon="help_outline",
                                on_click=self._show_peers_help_dialog,
                            ).props("flat round dense no-caps no-ripple").tooltip("绑定与运维要点")
                    with ui.element("div").classes("mgmt-toolbar"):
                        ui.button("刷新 Mesh 路由", icon="sync", on_click=self._sync_mesh_ccd_only).props(
                            "outline no-caps no-ripple"
                        ).classes("mgmt-toolbar-btn is-outline")
                        ui.button("新建对端", icon="hub", on_click=self._show_create_dialog).props(
                            "unelevated no-caps no-ripple"
                        ).classes("mgmt-toolbar-btn is-primary")

            with ui.element("section").classes("mgmt-panel mgmt-panel-list"):
                with ui.element("div").classes("mgmt-list-head"):
                    with ui.row().classes("items-center"):
                        ui.label("对端列表").classes("mgmt-kicker")
                    self._peers_total_label = ui.label(f"共 {len(peers)} 个").classes("group-list-count")

                self.list_container = ui.column().classes("w-full")
                with self.list_container:
                    self._render_peer_list(peers)

            ui.label(
                "改配置后请让对端 **重连 OpenVPN**，Mesh / 客户端路由才会对齐。"
            ).classes("text-caption text-grey q-mt-md mgmt-page-foot")

    def _active_usernames(self) -> list[str]:
        users = self.user_service.list_all()
        names = [str(u.get("username") or "") for u in users if u.get("status") == "active"]
        return sorted({n for n in names if n})

    def _peer_bind_select_usernames(self, *, exclude_peer_id: str | None) -> list[str]:
        """活跃用户中可被本对端选用的绑定用户名（已被其它对端绑定者除外）。"""
        bound = self.peer_service.list_bound_usernames(exclude_peer_id=exclude_peer_id)
        return [u for u in self._active_usernames() if u not in bound]

    def _group_id_to_name(self) -> dict[str, str]:
        return {str(g.get("id") or ""): str(g.get("name") or "") for g in self.group_service.list_all() if g.get("id")}

    @staticmethod
    def _peer_manual_download_filename(row: dict) -> str:
        pid = str(row.get("id") or "")
        slug = "".join(c if c.isalnum() else "-" for c in (row.get("name") or pid)[:32]).strip("-") or "peer"
        return f"{slug}-{pid[:8]}-manual.md"

    @staticmethod
    def _read_log_last_lines(path: Path, max_lines: int) -> str:
        """从文件尾部读取日志，避免安装日志较大时整文件读入。"""
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
        lines = b"".join(chunks).splitlines()
        tail = lines[-max_lines:] if len(lines) > max_lines else lines
        return "\n".join(line.decode("utf-8", errors="replace") for line in tail)

    @staticmethod
    def _markdown_for_probe_payload(data: dict, *, eff_bin: str | None = None) -> str:
        """将 probe_openvpn 返回结构转为说明用 Markdown。"""
        if not data.get("connected"):
            return f"**SSH 未连通**：{data.get('ssh_error') or 'unknown'}"
        dist = data.get("remote_distro") or {}
        dist_txt = (dist.get("pretty_name") or dist.get("name") or dist.get("id") or "—").strip()
        if data.get("installed"):
            ok = data.get("meets_requirement")
            return (
                f"**远端系统**：{dist_txt}\n\n"
                f"**OpenVPN**：已安装\n\n"
                f"- 路径：`{data.get('path')}`\n"
                f"- 版本：`{data.get('version')}`\n"
                f"- 满足最低版本 **{OPENVPN_MIN_VERSION}+**：**{'是' if ok else '否'}**"
            )
        hint = (
            f"（已试 `{eff_bin}` 及标准路径、`command -v`）"
            if eff_bin
            else "（已搜标准路径与 `command -v`）"
        )
        return f"**远端系统**：{dist_txt}\n\n未检测到 OpenVPN 可执行文件{hint}。"

    def render_manual_page(self, peer_id: str) -> None:
        """渲染对端部署说明独立页面。"""
        pid = str(peer_id or "").strip()
        row = self.peer_service.get(pid) if pid else None
        if not row:
            with ui.column().classes("page-shell peer-manual-page"):
                with ui.element("section").classes("peer-manual-empty"):
                    ui.icon("description", size="42px").classes("text-grey-6")
                    ui.label("对端部署说明不可用").classes("peer-manual-page-title")
                    ui.label("未找到该对端，请返回列表重选。").classes("peer-manual-page-subtitle")
                    ui.button("返回对端站点", icon="arrow_back", on_click=lambda: ui.navigate.to("/peers")).props(
                        "outline no-caps no-ripple"
                    )
            return
        text = self.peer_service.export_peer_manual_markdown(pid)
        data = self.peer_service.export_peer_manual_context(pid)
        overview = data["overview"]
        fname = self._peer_manual_download_filename(row)
        all_commands = "\n\n".join(str(x) for x in data.get("commands") or [] if str(x).strip())
        with ui.column().classes("page-shell peer-manual-page"):
            with ui.element("section").classes("peer-manual-hero"):
                with ui.element("div").classes("peer-manual-hero-copy"):
                    ui.label("对端部署说明").classes("peer-manual-page-title")
                    ui.label("按步执行，命令块可一键复制。").classes("peer-manual-page-subtitle")
                    with ui.element("div").classes("peer-manual-chip-row"):
                        for label, value in [
                            ("对端", overview["peer_name"]),
                            ("用户", overview["bound_username"]),
                            ("VPN 地址池", overview["global_subnet"]),
                        ]:
                            with ui.element("span").classes("peer-manual-chip"):
                                ui.label(label).classes("peer-manual-chip-label")
                                ui.label(str(value or "—")).classes("peer-manual-chip-value")
                with ui.element("div").classes("peer-manual-hero-actions"):
                    ui.button("返回", icon="arrow_back", on_click=lambda: ui.navigate.to("/peers")).props(
                        "flat no-caps no-ripple"
                    )
                    ui.button(
                        "复制全部命令",
                        icon="content_copy",
                        on_click=lambda t=all_commands: self._copy_manual_text(t, "已复制全部命令"),
                    ).props("outline no-caps no-ripple")
                    ui.button(
                        "下载 Markdown",
                        icon="download",
                        on_click=lambda: ui.download(text.encode("utf-8"), fname),
                    ).props("unelevated no-caps no-ripple")

            with ui.element("section").classes("peer-manual-grid"):
                with ui.element("div").classes("peer-manual-summary-card"):
                    ui.label("关键提醒").classes("peer-manual-section-title")
                    for item in data.get("highlights") or []:
                        with ui.element("div").classes("peer-manual-note"):
                            ui.icon("info", size="16px")
                            ui.label(str(item)).classes("peer-manual-note-text")
                with ui.element("div").classes("peer-manual-summary-card"):
                    ui.label("对端信息").classes("peer-manual-section-title")
                    self._render_manual_meta("对端 ID", overview["peer_id"])
                    self._render_manual_meta("客户端配置", overview["client_config_path"])
                    self._render_manual_meta("客户端日志", overview["client_log_path"])
                    cidrs = overview.get("lan_cidrs") or []
                    self._render_manual_meta("后方内网", "、".join(cidrs) if cidrs else "未配置")

            with ui.element("section").classes("peer-manual-steps"):
                ui.label("执行步骤").classes("peer-manual-section-title")
                for idx, step in enumerate(data.get("steps") or [], start=1):
                    self._render_manual_step(idx, step)

    def _render_manual_meta(self, label: str, value: str) -> None:
        """渲染部署说明元信息行。"""
        with ui.element("div").classes("peer-manual-meta-row"):
            ui.label(label).classes("peer-manual-meta-label")
            ui.label(str(value or "—")).classes("peer-manual-meta-value")

    def _render_manual_step(self, idx: int, step: dict) -> None:
        """渲染一个部署步骤卡片。"""
        command = str(step.get("command") or "").strip()
        with ui.element("article").classes("peer-manual-step-card"):
            with ui.element("div").classes("peer-manual-step-head"):
                with ui.element("span").classes("peer-manual-step-index"):
                    ui.label(str(idx))
                with ui.element("div").classes("min-w-0"):
                    ui.label(str(step.get("title") or f"步骤 {idx}")).classes("peer-manual-step-title")
                    ui.label(str(step.get("summary") or "")).classes("peer-manual-step-summary")
            if command:
                with ui.element("div").classes("peer-manual-command"):
                    with ui.element("div").classes("peer-manual-command-head"):
                        ui.label("bash").classes("peer-manual-command-lang")
                        ui.button(
                            "复制",
                            icon="content_copy",
                            on_click=lambda t=command: self._copy_manual_text(t, "已复制命令"),
                        ).props("flat dense no-caps no-ripple").classes("peer-manual-copy-btn")
                    ui.html(f"<pre><code>{html.escape(command)}</code></pre>").classes("peer-manual-command-code")

    @staticmethod
    def _copy_manual_text(text: str, message: str) -> None:
        """复制部署说明文本并给出提示。"""
        if not str(text or "").strip():
            ui.notify("没有可复制的内容", type="warning")
            return
        copy_text_to_clipboard(text)
        ui.notify(message, type="positive")

    def _show_peer_config_push_dialog(self, row: dict) -> None:
        """集中推送对端客户端配置、systemd service 与 iptables 规则。"""
        pid = str(row.get("id") or "")
        if not pid:
            ui.notify("缺少对端 ID", type="negative")
            return
        if not str(row.get("ssh_host") or "").strip() or not str(row.get("ssh_username") or "").strip():
            ui.notify("请先填写 SSH 主机与用户名", type="warning")
            return
        if not peer_row_has_usable_ssh_auth(row):
            ui.notify("请配置 SSH 密码或私钥，或使用系统设置中的全局私钥", type="warning")
            return
        uname = str(row.get("bound_username") or "").strip()
        try:
            default_r = default_remote_ovpn_path(uname)
        except ValueError:
            default_r = "/etc/openvpn/client/client.conf"

        custom_tmp: dict[str, str | None] = {"path": None}
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-xl"):
            ui.label("对端配置推送").classes("text-h6")
            ui.label("经 SSH 推送客户端配置、systemd、iptables；远端需 root 或免密 sudo。").classes(
                "text-caption text-grey q-mb-sm"
            )
            push_ovpn = ui.checkbox("推送客户端配置", value=True)
            push_service = ui.checkbox("推送并启用 systemd", value=True)
            push_fw = ui.checkbox("推送 iptables", value=True)
            ui.separator().classes("q-my-sm")
            fw_box = ui.column().classes("w-full q-gutter-xs")
            with fw_box:
                ui.label("iptables 推送参数").classes("text-caption text-grey")
                snat_sw = ui.switch("对端 POSTROUTING SNAT", value=bool(row.get("masquerade_on_peer")))
            fw_box.set_visibility(bool(push_fw.value))
            push_fw.on_value_change(lambda _: fw_box.set_visibility(bool(push_fw.value)))
            ui.separator().classes("q-my-sm")
            ovpn_box = ui.column().classes("w-full q-gutter-xs")
            with ovpn_box:
                src_sel = ui.select(
                    {
                        "center": "使用中心生成的配置",
                        "custom": "使用本机已修改的 .ovpn",
                    },
                    value="center",
                    label="客户端配置来源",
                ).classes("w-full")
                path_in = ui.input("客户端配置远端路径", value=default_r).classes("w-full")
                custom_box = ui.column().classes("w-full")
                with custom_box:
                    async def _on_custom_ovpn_upload(e) -> None:
                        try:
                            raw = await e.file.read()
                            suf = Path(getattr(e.file, "name", "") or "profile").suffix or ".ovpn"
                            fd, tmp = tempfile.mkstemp(suffix=suf)
                            try:
                                os.write(fd, raw)
                            finally:
                                os.close(fd)
                            old = custom_tmp.get("path")
                            if old:
                                Path(old).unlink(missing_ok=True)
                            custom_tmp["path"] = tmp
                            ui.notify(f"已选择本机文件: {getattr(e.file, 'name', '')}", type="positive")
                        except Exception as exc:
                            peer_remote_log.exception("读取本机上传 .ovpn 失败")
                            ui.notify(str(exc), type="negative")
                            raise RuntimeError(f"读取本机上传失败: {exc}") from exc

                    ui.upload(
                        label="选择 .ovpn 文件",
                        on_upload=_on_custom_ovpn_upload,
                        auto_upload=True,
                        max_files=1,
                    ).props('accept=".ovpn,application/x-openvpn,text/plain,*"').classes("w-full")
                custom_box.set_visibility(False)
                src_sel.on_value_change(lambda _: custom_box.set_visibility(src_sel.value == "custom"))
            ovpn_box.set_visibility(bool(push_ovpn.value))
            push_ovpn.on_value_change(lambda _: ovpn_box.set_visibility(bool(push_ovpn.value)))

            result_box = ui.column().classes("w-full min-h-[80px] q-mt-sm")

            async def do_push() -> None:
                selected = bool(push_ovpn.value) or bool(push_service.value) or bool(push_fw.value)
                if not selected:
                    ui.notify("请至少勾选一个推送项", type="warning")
                    return
                result_box.clear()
                with result_box:
                    ui.label("推送中…").classes("text-grey")
                lines: list[str] = []
                service_config_path = (path_in.value or "").strip() or None
                try:
                    if push_ovpn.value:
                        mode = str(src_sel.value or "center")
                        if mode == "custom":
                            local_pre = custom_tmp.get("path")
                            if not local_pre or not Path(local_pre).is_file():
                                raise ValueError("请先上传本机 .ovpn 文件")
                            ovpn_data = await run.io_bound(
                                lambda lp=local_pre: self.peer_service.deploy_peer_ovpn_from_local_path_via_ssh(
                                    pid, lp, remote_path=service_config_path
                                )
                            )
                        else:
                            ovpn_data = await run.io_bound(
                                lambda: self.peer_service.deploy_peer_ovpn_via_ssh(pid, remote_path=service_config_path)
                            )
                        service_config_path = str(ovpn_data.get("remote_path") or service_config_path or "").strip() or None
                        lines.append(f"- 客户端配置：已推送到 `{ovpn_data.get('remote_path')}`")
                    if push_service.value:
                        service_data = await run.io_bound(
                            lambda: self.peer_service.deploy_peer_client_systemd_via_ssh(
                                pid,
                                config_path=service_config_path,
                            )
                        )
                        lines.append(f"- service：已启用 `{service_data.get('service')}`")
                    if push_fw.value:
                        snat_enabled = bool(snat_sw.value)
                        fw_data = await run.io_bound(
                            lambda: self.peer_service.deploy_peer_site_firewall_via_ssh(
                                pid,
                                masquerade_on_peer=snat_enabled,
                                force=True,
                            )
                        )
                        fw_status = "已是最新，跳过重写" if fw_data.get("already_current") else "已下发"
                        lines.append(
                            f"- iptables：{fw_status}，SNAT 转换规则 {fw_data.get('masquerade_rules', 0)} 条"
                        )
                except Exception as exc:
                    peer_remote_log.error("对端配置推送失败 peer=%s: %s", pid, exc)
                    result_box.clear()
                    with result_box:
                        ui.label(str(exc)).classes("text-negative")
                    return
                finally:
                    if custom_tmp.get("path"):
                        try:
                            Path(custom_tmp["path"]).unlink(missing_ok=True)
                        except OSError:
                            pass
                        custom_tmp["path"] = None
                result_box.clear()
                with result_box:
                    ui.markdown("**推送完成**\n\n" + "\n".join(lines))
                ui.notify("对端配置推送完成", type="positive")

            with ui.row().classes("w-full justify-end q-gutter-sm q-mt-md"):
                ui.button("关闭", on_click=dialog.close).props("flat no-caps")
                ui.button("开始推送", icon="cloud_upload", on_click=do_push).props("unelevated no-caps")
        dialog.open()

    def _show_peer_openvpn_setup_dialog(self, row: dict) -> None:
        """SSH 自动探测并安装/装配 OpenVPN 客户端。"""
        pid = str(row.get("id") or "")
        if not pid:
            ui.notify("缺少对端 ID", type="negative")
            return
        if not str(row.get("ssh_host") or "").strip() or not str(row.get("ssh_username") or "").strip():
            ui.notify("请先填写 SSH 主机与用户名", type="warning")
            return
        if not peer_row_has_usable_ssh_auth(row):
            ui.notify("请配置 SSH 密码或私钥，或使用系统设置中的全局私钥", type="warning")
            return
        eff_bin = str(row.get("ssh_openvpn_binary") or "").strip() or None
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-lg"):
            dialog_state = {"open": True}
            dialog.on("hide", lambda _: dialog_state.update(open=False))
            ui.label("OpenVPN 客户端安装").classes("text-h6")
            ui.label(
                "已安装则跳过；否则首次安装。详单见 data/logs/peer-remote.log。"
            ).classes("text-caption text-grey q-mb-sm")
            result_box = ui.column().classes("w-full min-h-[60px] q-mt-sm")

            async def do_run() -> None:
                if not dialog_state["open"]:
                    return
                result_box.clear()
                with result_box:
                    ui.label("探测中…（安装可能需数分钟）").classes("text-grey")
                self._set_peer_row_busy(pid, True)
                try:
                    data = await run.io_bound(lambda: self.peer_service.ensure_openvpn_on_peer_via_ssh(pid))
                except ValueError as exc:
                    peer_remote_log.error("对端 OpenVPN 探测/安装参数错误: %s", exc)
                    if dialog_state["open"]:
                        result_box.clear()
                        with result_box:
                            ui.label(str(exc)).classes("text-negative")
                    self._notify_for_page(str(exc), type="negative")
                    return
                except RuntimeError as exc:
                    peer_remote_log.error("对端 OpenVPN 探测/安装失败: %s", exc)
                    if dialog_state["open"]:
                        result_box.clear()
                        with result_box:
                            ui.label(str(exc)).classes("text-negative")
                    self._notify_for_page(str(exc), type="negative")
                    return
                except Exception as exc:
                    peer_remote_log.exception("对端 OpenVPN 探测/安装异常")
                    if dialog_state["open"]:
                        result_box.clear()
                        with result_box:
                            ui.label(str(exc)).classes("text-negative")
                    self._notify_for_page(str(exc), type="negative")
                    return
                finally:
                    self._set_peer_row_busy(pid, False)

                if not dialog_state["open"]:
                    self._notify_for_page("对端安装流程已结束", type="positive")
                    return
                result_box.clear()
                probe = data.get("probe") or {}
                inst = data.get("install") or {}
                push = data.get("ovpn_push")
                push_md = ""
                if data.get("skipped_install"):
                    with result_box:
                        ui.markdown(
                            "**已安装，跳过部署**\n\n"
                            f"- 路径：`{probe.get('path') or '—'}`\n"
                            f"- 版本：`{probe.get('version') or '—'}`\n\n"
                            "如需覆盖配置，用 **配置推送**。"
                        )
                    self._notify_for_page("远端已安装 OpenVPN", type="positive")
                    return
                if isinstance(push, dict):
                    if push.get("ok"):
                        push_md = f"\n\n**已自动推送 .ovpn** → `{push.get('remote_path')}`（{push.get('bytes')} 字节）"
                    elif push.get("error"):
                        push_md = f"\n\n**自动推 .ovpn 失败**：{push.get('error')}（可用 **配置推送**）"
                sd = data.get("systemd_client") or {}
                fw = data.get("peer_firewall") or {}
                post_md = ""
                if isinstance(sd, dict) and sd.get("ok"):
                    source_label = "官方模板" if sd.get("unit_source") == "official" else "兜底 unit"
                    post_md += (
                        f"\n\n**systemd**：已启用 **{sd.get('service')}**（{source_label}，"
                        f"`{sd.get('config_path')}`）"
                    )
                elif isinstance(sd, dict) and sd.get("error"):
                    post_md += f"\n\n**systemd**：失败 — {sd.get('error')}"
                if isinstance(fw, dict) and fw.get("ok"):
                    post_md += "\n\n**对端 iptables**：已下发（global_subnet → VPN_PEER）"
                elif isinstance(fw, dict) and fw.get("error"):
                    post_md += f"\n\n**对端 iptables**：失败 — {fw.get('error')}（**配置推送** 可重试）"
                with result_box:
                    ui.markdown(
                        "**已执行首次部署**"
                        f"{push_md}{post_md}\n\n"
                        f"- 路径：`{probe.get('path') or '—'}`\n"
                        f"- 版本：`{probe.get('version') or '—'}`\n"
                        f"- 发行版：`{inst.get('pretty_name') or inst.get('distro_id') or probe.get('remote_distro', {}).get('pretty_name') or '—'}`\n"
                        "完整日志：**data/logs/peer-remote.log**。"
                    )
                self._notify_for_page("对端安装流程已结束", type="positive")

            def close_dialog() -> None:
                dialog_state["open"] = False
                dialog.close()

            with ui.row().classes("w-full justify-end q-gutter-sm q-mt-md"):
                ui.button("关闭", on_click=close_dialog).props("flat no-caps")
                ui.button("安装", icon="build", on_click=do_run).props("unelevated no-caps")
        dialog.open()

    def _show_peer_remote_log_dialog(self, row: dict) -> None:
        """独立右侧侧栏查看对端 SSH / 安装日志。"""
        name = str(row.get("name") or row.get("id") or "对端")
        log_path = LOGS_DIR / "peer-remote.log"
        state = {"open": True}
        log_label_ref: dict[str, object | None] = {"label": None}
        timer_ref: dict[str, object | None] = {"timer": None}

        with ui.dialog().props("position=right maximized") as dialog, ui.card().classes(
            "peer-remote-log-drawer w-[min(100vw,42rem)] h-full no-wrap"
        ):
            def close_log_dialog() -> None:
                state["open"] = False
                timer = timer_ref.get("timer")
                if timer is not None:
                    timer.deactivate()  # type: ignore[attr-defined]
                dialog.close()

            def on_hide(_: object) -> None:
                state["open"] = False
                timer = timer_ref.get("timer")
                if timer is not None:
                    timer.deactivate()  # type: ignore[attr-defined]

            dialog.on("hide", on_hide)

            with ui.element("div").classes("peer-manual-drawer-head"):
                with ui.element("div").classes("min-w-0"):
                    ui.label("对端远程日志").classes("peer-manual-title")
                    ui.label(name).classes("peer-manual-subtitle")
                ui.button(icon="close", on_click=close_log_dialog).props("flat round dense no-caps no-ripple")
            ui.label(f"日志：{log_path}").classes("peer-manual-hint")
            log_box = ui.column().classes("peer-remote-log-frame w-full flex-1")

            def set_log_body(text: str) -> None:
                if not state["open"]:
                    return
                if log_label_ref["label"] is None:
                    log_box.clear()
                    with log_box:
                        with ui.element("div").classes("peer-remote-log-viewport"):
                            log_label_ref["label"] = ui.label(text).classes(
                                "w-full whitespace-pre-wrap break-words font-mono text-left"
                            )
                else:
                    log_label_ref["label"].text = text  # type: ignore[attr-defined]

            def refresh_log() -> None:
                if not state["open"]:
                    return
                try:
                    body = self._read_log_last_lines(log_path, _PEER_REMOTE_LOG_MAX_LINES)
                except FileNotFoundError:
                    body = f"日志文件尚未生成：{log_path}"
                except OSError as exc:
                    logger.error("读取对端远程日志失败: %s", exc)
                    body = f"读取日志失败：{exc}"
                set_log_body(body)

            refresh_log()
            realtime_sw = ui.switch(
                f"实时（尾 {_PEER_REMOTE_LOG_MAX_LINES} 行 / ~{_PEER_REMOTE_LOG_POLL_SEC:g}s）",
                value=True,
            ).classes("q-mx-md")
            timer = ui.timer(
                _PEER_REMOTE_LOG_POLL_SEC,
                lambda: refresh_log() if realtime_sw.value else None,
                active=True,
            )
            timer_ref["timer"] = timer

            def on_realtime_change() -> None:
                if realtime_sw.value:
                    timer.activate()
                    refresh_log()
                else:
                    timer.deactivate()

            realtime_sw.on_value_change(lambda _: on_realtime_change())
            with ui.row().classes("peer-manual-drawer-foot"):
                ui.button("刷新", icon="refresh", on_click=refresh_log).props("outline no-caps no-ripple")
                ui.button("关闭", on_click=close_log_dialog).props("flat no-caps no-ripple")
        dialog.open()

    async def _apply_peer_snat_policy_after_save(self, peer_id: str, *, source: str) -> None:
        """保存对端后按 SNAT 策略幂等下发远端规则。"""
        self._set_peer_row_busy(peer_id, True)
        try:
            data = await run.io_bound(lambda: self.peer_service.deploy_peer_site_firewall_via_ssh(peer_id))
        except Exception as exc:
            logger.error("%s后下发对端 SNAT 策略失败 peer=%s: %s", source, peer_id, exc)
            peer_remote_log.error("%s后下发对端 SNAT 策略失败 peer=%s: %s", source, peer_id, exc)
            self._notify_for_page(f"SNAT 策略下发失败: {exc}", type="negative", multi_line=True)
            return
        finally:
            self._set_peer_row_busy(peer_id, False)
        status = "已存在" if isinstance(data, dict) and data.get("already_current") else "已下发"
        self._notify_for_page(f"SNAT 策略{status}", type="positive")

    def _sync_mesh_ccd_only(self) -> None:
        try:
            self.peer_service.sync_all_mesh_push_routes_in_ccd()
        except RuntimeError as exc:
            logger.error("手动刷新 mesh CCD 失败: %s", exc)
            ui.notify(str(exc), type="negative")
            return
        ui.notify("已重写各用户 CCD 中的 Mesh 路由", type="positive")

    def _show_peers_help_dialog(self) -> None:
        """右侧说明栏：精简要点 + 快捷跳转。"""
        with ui.dialog().props("position=right maximized") as dialog, ui.card().classes(
            "w-[min(100vw,28rem)] h-full no-wrap overflow-y-auto q-pa-md peers-help-panel"
        ):
            with ui.row().classes("w-full items-center justify-between q-mb-sm"):
                ui.label("绑定与运维说明").classes("text-h6")
                ui.button(icon="close", on_click=dialog.close).props("flat round dense no-caps")
            ui.markdown(
                f"""
- **绑定**：在 **用户管理** 建用户；**一人一对端**。下拉不含已被占用的名。
- **`.ovpn`**：`{OVPN_PROFILES_DIR}/<用户名>.ovpn`
- **行内**：**扳手**=探测/安装并装配；**云上传**=单独推配置、service、iptables。日志：**data/logs/peer-remote.log**
- **生效**：改内网 / Mesh / 归组后需 **重连**。
- **细则**：**配置推送** 可重试基础规则；远端策略在 **防火墙规则**。
"""
            )

            def _go_users() -> None:
                dialog.close()
                ui.navigate.to("/users")

            def _go_firewall() -> None:
                dialog.close()
                ui.navigate.to("/firewall")

            ui.label("快捷跳转").classes("peers-help-jump-label")
            with ui.column().classes("w-full gap-sm q-mt-xs"):
                ui.button("用户管理", icon="people", on_click=_go_users).props(
                    "flat no-caps no-ripple align=left icon-right=arrow_forward"
                ).classes("peers-help-jump-btn")
                ui.button("防火墙", icon="shield", on_click=_go_firewall).props(
                    "flat no-caps no-ripple align=left icon-right=arrow_forward"
                ).classes("peers-help-jump-btn")
        dialog.open()

    def _refresh_list(self) -> None:
        if self.list_container is None:
            return
        peers = self.peer_service.list_all()
        if self._peers_total_label is not None:
            self._peers_total_label.text = f"共 {len(peers)} 个"
        self.list_container.clear()
        with self.list_container:
            self._render_peer_list(peers)

    def _render_peer_list(self, peers: list[dict]) -> None:
        if not peers:
            ui.label("暂无对端，点「新建对端」。").classes("empty-state")
            return
        with ui.element("div").classes("mgmt-record-list"):
            for row in sorted(peers, key=lambda r: (r.get("name") or "", r.get("id") or "")):
                self._render_peer_card(row)

    def _render_peer_card(self, row: dict) -> None:
        pid = row.get("id") or ""
        name = row.get("name") or pid
        user = row.get("bound_username") or "—"
        cidrs = row.get("lan_cidrs") or []
        cidr_label = ", ".join(cidrs) if cidrs else "无内网"
        vis = [str(x).strip() for x in (row.get("mesh_route_visible_group_ids") or []) if str(x).strip()]
        gmap = self._group_id_to_name()
        if not vis:
            mesh_label = "Mesh 路由: 全部组"
        else:
            short = "、".join((gmap.get(gid) or gid[:8]) for gid in vis[:3])
            more = f" 等{len(vis)}组" if len(vis) > 3 else ""
            mesh_label = f"Mesh 路由: {short}{more}"

        id_short = f"{pid[:8]}…" if len(str(pid)) > 8 else str(pid)

        with ui.element("div").classes("mgmt-record-card mgmt-peer-row"):
            with ui.element("div").classes("mgmt-record-main"):
                with ui.element("div").classes("mgmt-peer-avatar"):
                    if str(pid) in self._busy_peer_ids:
                        ui.spinner(size="28px", color="primary", thickness=2)
                    else:
                        ui.icon("settings_input_component", size="22px")
                with ui.element("div").classes("mgmt-record-copy"):
                    with ui.element("div").classes("mgmt-peer-title-row"):
                        ui.label(name).classes("mgmt-peer-name")
                    with ui.element("div").classes("mgmt-peer-meta-row"):
                        with ui.element("span").classes("mgmt-peer-meta-tag"):
                            ui.icon("person", size="14px")
                            ui.label(user)
                        with ui.element("span").classes("mgmt-peer-meta-tag"):
                            ui.icon("lan", size="14px")
                            ui.label(cidr_label)
                        with ui.element("span").classes("mgmt-peer-meta-tag"):
                            ui.icon("filter_alt", size="14px")
                            ui.label(mesh_label)
                        with ui.element("span").classes("mgmt-peer-meta-tag"):
                            ui.label(f"ID {id_short}")

            with ui.element("div").classes("mgmt-peer-actions-rail"):
                ui.button(
                    icon="build",
                    on_click=lambda r=row: self._show_peer_openvpn_setup_dialog(r),
                ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn").tooltip(
                    "探测/安装并装配客户端"
                )
                ui.button(
                    icon="dns",
                    on_click=lambda p=pid: ui.navigate.to(f"/services?tab=peers&peer={p}"),
                ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn").tooltip(
                    "服务管理 · 对端实例"
                )
                ui.button(
                    icon="article",
                    on_click=lambda r=row: self._show_peer_remote_log_dialog(r),
                ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn").tooltip(
                    "SSH / 安装日志"
                )
                ui.button(
                    icon="cloud_upload",
                    on_click=lambda r=row: self._show_peer_config_push_dialog(r),
                ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn").tooltip(
                    "推送配置、service、iptables"
                )
                ui.button(
                    icon="description",
                    on_click=lambda p=pid: ui.navigate.to(f"/peers/manual?peer={p}"),
                ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn").tooltip(
                    "部署说明（可复制命令）"
                )
                ui.element("div").classes("mgmt-peer-action-sep")
                ui.button(
                    icon="edit",
                    on_click=lambda r=row: self._show_edit_dialog(r),
                ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-edit").tooltip(
                    "编辑"
                )
                ui.button(
                    icon="delete_outline",
                    on_click=lambda r=row: self._confirm_delete(r),
                ).props("flat round dense no-caps no-ripple").classes("mgmt-icon-btn is-delete").tooltip(
                    "删除"
                )

    def _show_create_dialog(self) -> None:
        """新建对端：与防火墙规则页一致的右侧全高侧栏（QDialog position=right）。"""
        usernames = self._peer_bind_select_usernames(exclude_peer_id=None)
        if not usernames:
            ui.notify(
                "无可用绑定用户（均已占用或无活跃用户）。请先建用户或释放占用。",
                type="warning",
            )
            logger.warning("新建对端：绑定用户下拉为空")
            return

        group_options = self._group_id_to_name()

        with ui.dialog().props("position=right maximized") as dialog, ui.card().classes(
            "w-[min(100vw,28rem)] h-full no-wrap overflow-y-auto"
        ):
            ui.label("新建对端站点").classes("text-h6")
            ui.label("基础信息").classes("text-caption text-grey q-mt-sm")
            name_in = ui.input("显示名称").classes("w-full")
            user_sel = ui.select(
                options=usernames,
                label="绑定用户（每用户一个对端，须有 CCD）",
                value=usernames[0],
            ).classes("w-full")
            cidr_in = ui.textarea(
                label="后方内网 CIDR（多行或逗号分隔）",
                placeholder="例如 192.168.10.0/24",
            ).classes("w-full")
            ui.label("路由策略").classes("text-caption text-grey q-mt-sm")
            mesh_vis_sel = ui.select(
                options=group_options,
                label="Mesh 仅对这些组可见（不选=全部活跃用户）",
                multiple=True,
                value=[],
            ).props("use-chips clearable").classes("w-full")
            masq = ui.checkbox("启用对端 SNAT 策略", value=False)
            ui.label("SSH 连接").classes("text-caption text-grey q-mt-sm")
            ssh_host = ui.input("SSH 主机（可选）").classes("w-full")
            ssh_user = ui.input("SSH 用户名（可选，检测时用）").classes("w-full")
            ssh_port = ui.number("SSH 端口", value=22, min=1, max=65535, step=1).classes("w-full")
            ui.label("SSH 凭据").classes("text-caption text-grey q-mt-sm")
            ssh_auth = ui.select(
                options={"none": "none", "password": "password", "key": "key"},
                label="SSH 认证（占位）",
                value="none",
            ).classes("w-full")
            ssh_pw_in = ui.input("SSH 密码", password=True, password_toggle_button=True).classes("w-full")
            ssh_key_ta = ui.textarea(
                label="SSH 私钥 PEM（可粘贴；留空则使用系统设置中的全局私钥）",
                placeholder="-----BEGIN OPENSSH PRIVATE KEY----- …",
            ).classes("w-full").props('rows="5"')

            async def handle_create_key_upload(e) -> None:
                try:
                    raw = await e.file.read()
                    ssh_key_ta.set_value(raw.decode("utf-8", errors="replace"))
                    ui.notify(f"已载入私钥文件: {e.file.name}", type="positive")
                except Exception as exc:
                    fname = getattr(e.file, "name", "")
                    logger.exception("新建对端：读取上传私钥失败 file=%s", fname)
                    ui.notify(f"读取上传失败 ({fname}): {exc}", type="negative")
                    raise RuntimeError(f"读取上传私钥失败: {exc}") from exc

            ui.upload(
                label="或上传私钥文件",
                on_upload=handle_create_key_upload,
                auto_upload=True,
                max_files=1,
            ).props('accept=".pem,.key,text/plain,*"').classes("w-full")
            ssh_key_pp_in = ui.input(
                "私钥口令（若密钥文件有加密）",
                password=True,
                password_toggle_button=True,
            ).classes("w-full")
            ui.label("远端运行参数").classes("text-caption text-grey q-mt-sm")
            ssh_ovpn_bin = ui.input(
                "远端 openvpn 路径（可选）",
                placeholder="例 /opt/openvpn/sbin/openvpn；留空则 PATH 探测",
            ).classes("w-full")
            ui.label("部署选项").classes("text-caption text-grey q-mt-sm")
            auto_inst = ui.checkbox(
                "创建后自动安装",
                value=False,
            )

            async def do_create() -> None:
                nm = (name_in.value or "").strip()
                if not nm:
                    ui.notify("请填写显示名称", type="warning")
                    return
                uname = str(user_sel.value or "").strip()
                if not uname:
                    ui.notify("请选择绑定用户", type="warning")
                    return
                lan = _parse_lan_cidrs_text(cidr_in.value or "")
                mesh_ids = [str(x).strip() for x in (mesh_vis_sel.value or []) if str(x).strip()]
                payload = {
                    "name": nm,
                    "bound_username": uname,
                    "lan_cidrs": lan,
                    "mesh_route_visible_group_ids": mesh_ids,
                    "ssh_host": (ssh_host.value or "").strip(),
                    "ssh_username": (ssh_user.value or "").strip(),
                    "ssh_port": int(ssh_port.value or 22),
                    "ssh_auth": str(ssh_auth.value or "none"),
                    "ssh_password": (ssh_pw_in.value or "").strip(),
                    "ssh_private_key": (ssh_key_ta.value or "").strip(),
                    "ssh_private_key_passphrase": (ssh_key_pp_in.value or "").strip(),
                    "ssh_openvpn_binary": (ssh_ovpn_bin.value or "").strip(),
                    "masquerade_on_peer": bool(masq.value),
                    "auto_install_on_peer": bool(auto_inst.value),
                }
                try:
                    created = self.peer_service.create(payload)
                except ValueError as exc:
                    logger.error("创建对端校验失败: %s", exc)
                    ui.notify(str(exc), type="negative")
                    return
                except RuntimeError as exc:
                    logger.error("创建对端 iptables/CCD 失败: %s", exc)
                    ui.notify(str(exc), type="negative")
                    return
                pid = str(created.get("id") or "")
                dialog.close()
                create_msg = "已创建；请通知客户端重连以更新路由。"
                self._notify_for_page(create_msg, type="positive")
                self._refresh_list()
                if bool(auto_inst.value) and pid:
                    self._set_peer_row_busy(pid, True)
                    try:
                        data = await run.io_bound(lambda: self.peer_service.ensure_openvpn_on_peer_via_ssh(pid))
                    except Exception as exc:
                        logger.error("创建后自动 SSH 装配失败 peer=%s: %s", pid, exc)
                        peer_remote_log.error("创建后自动 SSH 装配失败 peer=%s: %s", pid, exc)
                        self._notify_for_page(f"自动 SSH 装配失败: {exc}", type="negative", multi_line=True)
                    else:
                        if isinstance(data, dict) and data.get("skipped_install"):
                            fw_msg = "，已安装未覆盖配置"
                        else:
                            fw = data.get("peer_firewall") if isinstance(data, dict) else None
                            fw_msg = "，对端防火墙已下发" if isinstance(fw, dict) and fw.get("ok") else ""
                        self._notify_for_page(f"自动 SSH 装配已完成{fw_msg}", type="positive")
                    finally:
                        self._set_peer_row_busy(pid, False)
                elif bool(masq.value) and pid:
                    await self._apply_peer_snat_policy_after_save(pid, source="创建")

            with ui.row().classes("w-full justify-end q-gutter-sm q-mt-md"):
                ui.button("取消", on_click=dialog.close).props("flat")
                ui.button("创建", on_click=do_create).props("color=primary")

        dialog.open()

    def _show_edit_dialog(self, row: dict) -> None:
        pid = str(row.get("id") or "")
        if not pid:
            ui.notify("缺少对端 ID", type="negative")
            return
        usernames = self._peer_bind_select_usernames(exclude_peer_id=pid)
        cur_user = str(row.get("bound_username") or "")
        if cur_user and cur_user not in usernames:
            usernames = sorted(set(usernames) | {cur_user})

        group_options = dict(self._group_id_to_name())
        cur_mesh = [str(x).strip() for x in (row.get("mesh_route_visible_group_ids") or []) if str(x).strip()]
        for gid in cur_mesh:
            if gid not in group_options:
                group_options[gid] = f"{gid[:8]}…（组可能已删）"
        with ui.dialog().props("position=right maximized") as dialog, ui.card().classes(
            "w-[min(100vw,28rem)] h-full no-wrap overflow-y-auto"
        ):
            ui.label("编辑对端站点").classes("text-h6")
            ui.label("基础信息").classes("text-caption text-grey q-mt-sm")
            name_in = ui.input("显示名称", value=row.get("name") or "").classes("w-full")
            user_sel = ui.select(
                options=usernames,
                label="绑定用户（每用户一个对端）",
                value=cur_user if cur_user in usernames else (usernames[0] if usernames else None),
            ).classes("w-full")
            cidrs = row.get("lan_cidrs") or []
            cidr_in = ui.textarea(
                label="后方内网 CIDR",
                value="\n".join(str(c) for c in cidrs),
            ).classes("w-full")
            ui.label("路由策略").classes("text-caption text-grey q-mt-sm")
            mesh_vis_sel = ui.select(
                options=group_options,
                label="Mesh 仅对这些组可见（不选=全部活跃用户）",
                multiple=True,
                value=cur_mesh,
            ).props("use-chips clearable").classes("w-full")
            masq = ui.checkbox(
                "启用对端 SNAT 策略",
                value=bool(row.get("masquerade_on_peer")),
            )
            ui.label("SSH 连接").classes("text-caption text-grey q-mt-sm")
            ssh_host = ui.input("SSH 主机", value=row.get("ssh_host") or "").classes("w-full")
            ssh_user = ui.input("SSH 用户名", value=row.get("ssh_username") or "").classes("w-full")
            ssh_port = ui.number(
                "SSH 端口",
                value=int(row.get("ssh_port") or 22),
                min=1,
                max=65535,
                step=1,
            ).classes("w-full")
            ui.label("SSH 凭据").classes("text-caption text-grey q-mt-sm")
            ssh_auth = ui.select(
                options={"none": "none", "password": "password", "key": "key"},
                label="SSH 认证（占位）",
                value=str(row.get("ssh_auth") or "none"),
            ).classes("w-full")
            ui.label(
                "凭据留空表示不改已存值；清除私钥后用系统全局私钥（若已配置）。"
            ).classes("text-caption text-grey q-mt-sm")
            ssh_pw_in = ui.input("SSH 密码（留空不改）", password=True, password_toggle_button=True).classes(
                "w-full"
            )
            clear_pw = ui.checkbox("清除已存 SSH 密码")
            has_saved_key = bool((row.get("ssh_private_key") or "").strip())
            if has_saved_key:
                ui.label("已保存私钥：可粘贴/上传覆盖，或勾选清除。").classes(
                    "text-caption text-grey"
                )
            ssh_key_ta = ui.textarea(
                label="SSH 私钥 PEM（留空不改；清除对端私钥后回退全局私钥）",
                placeholder="粘贴新私钥或上传文件…",
            ).classes("w-full").props('rows="5"')

            async def handle_edit_key_upload(e) -> None:
                try:
                    raw = await e.file.read()
                    ssh_key_ta.set_value(raw.decode("utf-8", errors="replace"))
                    ui.notify(f"已载入私钥文件: {e.file.name}", type="positive")
                except Exception as exc:
                    fname = getattr(e.file, "name", "")
                    logger.exception("编辑对端：读取上传私钥失败 file=%s", fname)
                    ui.notify(f"读取上传失败 ({fname}): {exc}", type="negative")
                    raise RuntimeError(f"读取上传私钥失败: {exc}") from exc

            ui.upload(
                label="或上传私钥文件",
                on_upload=handle_edit_key_upload,
                auto_upload=True,
                max_files=1,
            ).props('accept=".pem,.key,text/plain,*"').classes("w-full")
            ssh_key_pp_in = ui.input(
                "私钥口令（留空不改；填写新私钥时请一并填写）",
                password=True,
                password_toggle_button=True,
            ).classes("w-full")
            clear_key = ui.checkbox("清除已存 SSH 私钥及口令")
            ui.label("远端运行参数").classes("text-caption text-grey q-mt-sm")
            ssh_ovpn_bin = ui.input(
                "远端 openvpn 路径（可选）",
                value=str(row.get("ssh_openvpn_binary") or ""),
                placeholder="留空则 PATH 探测",
            ).classes("w-full")
            ui.label("部署选项").classes("text-caption text-grey q-mt-sm")
            auto_inst = ui.checkbox(
                "保存后自动安装",
                value=bool(row.get("auto_install_on_peer")),
            )

            async def do_save() -> None:
                nm = (name_in.value or "").strip()
                if not nm:
                    ui.notify("请填写显示名称", type="warning")
                    return
                uname = str(user_sel.value or "").strip()
                lan = _parse_lan_cidrs_text(cidr_in.value or "")
                mesh_ids = [str(x).strip() for x in (mesh_vis_sel.value or []) if str(x).strip()]
                payload = {
                    "name": nm,
                    "bound_username": uname,
                    "lan_cidrs": lan,
                    "mesh_route_visible_group_ids": mesh_ids,
                    "ssh_host": (ssh_host.value or "").strip(),
                    "ssh_username": (ssh_user.value or "").strip(),
                    "ssh_port": int(ssh_port.value or 22),
                    "ssh_openvpn_binary": (ssh_ovpn_bin.value or "").strip(),
                    "ssh_auth": str(ssh_auth.value or "none"),
                    "masquerade_on_peer": bool(masq.value),
                    "auto_install_on_peer": bool(auto_inst.value),
                }
                if clear_pw.value:
                    payload["ssh_password"] = ""
                elif (ssh_pw_in.value or "").strip():
                    payload["ssh_password"] = (ssh_pw_in.value or "").strip()
                if clear_key.value:
                    payload["ssh_private_key"] = ""
                    payload["ssh_private_key_passphrase"] = ""
                else:
                    new_pem = (ssh_key_ta.value or "").strip()
                    new_pp = (ssh_key_pp_in.value or "").strip()
                    if new_pem:
                        payload["ssh_private_key"] = new_pem
                        payload["ssh_private_key_passphrase"] = new_pp
                    elif new_pp and has_saved_key:
                        payload["ssh_private_key_passphrase"] = new_pp
                try:
                    self.peer_service.update(pid, payload)
                except ValueError as exc:
                    logger.error("更新对端校验失败: %s", exc)
                    ui.notify(str(exc), type="negative")
                    return
                except RuntimeError as exc:
                    logger.error("更新对端 iptables/CCD 失败: %s", exc)
                    ui.notify(str(exc), type="negative")
                    return
                dialog.close()
                save_msg = "已保存；Mesh / CCD 变更需重连生效。"
                self._notify_for_page(save_msg, type="positive")
                self._refresh_list()
                if bool(auto_inst.value) and pid:
                    self._set_peer_row_busy(pid, True)
                    try:
                        data = await run.io_bound(lambda: self.peer_service.ensure_openvpn_on_peer_via_ssh(pid))
                    except Exception as exc:
                        logger.error("编辑后自动 SSH 装配失败 peer=%s: %s", pid, exc)
                        peer_remote_log.error("编辑后自动 SSH 装配失败 peer=%s: %s", pid, exc)
                        self._notify_for_page(f"自动 SSH 装配失败: {exc}", type="negative", multi_line=True)
                    else:
                        if isinstance(data, dict) and data.get("skipped_install"):
                            fw_msg = "，已安装未覆盖配置"
                        else:
                            fw = data.get("peer_firewall") if isinstance(data, dict) else None
                            fw_msg = "，对端防火墙已下发" if isinstance(fw, dict) and fw.get("ok") else ""
                        self._notify_for_page(f"自动 SSH 装配已完成{fw_msg}", type="positive")
                    finally:
                        self._set_peer_row_busy(pid, False)
                elif bool(masq.value) and pid:
                    await self._apply_peer_snat_policy_after_save(pid, source="编辑")

            with ui.row().classes("w-full justify-end q-gutter-sm q-mt-md"):
                ui.button("取消", on_click=dialog.close).props("flat")
                ui.button("保存", on_click=do_save).props("color=primary")

        dialog.open()

    def _confirm_delete(self, row: dict) -> None:
        pid = str(row.get("id") or "")
        name = str(row.get("name") or pid)

        def do_delete() -> None:
            try:
                self.peer_service.delete(pid)
            except ValueError as exc:
                logger.error("删除对端失败: %s", exc)
                ui.notify(str(exc), type="negative")
                return
            ui.notify("已删除对端实例", type="positive")
            self._refresh_list()

        confirm_dialog.show(
            title="删除对端",
            message=f"删除「{name}」？将清理 CCD 中该对端块及中心 VPN_PEER。",
            on_confirm=do_delete,
            confirm_color="negative",
        )
