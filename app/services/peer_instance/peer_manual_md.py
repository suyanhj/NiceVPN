# -*- coding: utf-8 -*-
"""对端站点手动部署说明：生成结构化执行手册与 Markdown 下载文本。"""
from __future__ import annotations

from typing import Any

CLIENT_CONFIG_PATH = "/etc/openvpn/client/client.conf"
CLIENT_LOG_PATH = "/etc/openvpn/log/client.log"
CLIENT_STATUS_PATH = "/etc/openvpn/log/client-status.log"


def build_peer_site_manual_context(
    *,
    peer_name: str,
    peer_id: str,
    bound_username: str,
    lan_cidrs: list[str],
    global_subnet: str,
    masquerade_on_peer: bool,
) -> dict[str, Any]:
    """根据对端元数据生成页面渲染用的结构化部署说明。

    Args:
        peer_name: 对端展示名。
        peer_id: 对端 UUID，用于 iptables 注释和清理定位。
        bound_username: 绑定的 VPN 用户名，对应客户端证书 CN。
        lan_cidrs: 对端后方内网 CIDR 列表。
        global_subnet: 中心 VPN 全局地址池 CIDR。
        masquerade_on_peer: 是否展示 SNAT/MASQUERADE 推荐命令。

    Returns:
        包含 overview、highlights、steps、commands 的字典，可直接用于 UI 渲染。
    """
    gs = (global_subnet or "").strip() or "0.0.0.0/0"
    cidrs = [str(c).strip() for c in lan_cidrs if str(c).strip()]
    peer_label = (peer_name or peer_id).strip() or peer_id
    fwd_comment = f"ovpn-mgmt-peer peer={peer_id} role=fwd-global"
    masq_comment = f"ovpn-mgmt-peer peer={peer_id} role=masq idx=0"
    prepare_cmd = f"""install -d -m 0755 /etc/openvpn/client /etc/openvpn/log
# 将中心生成的 {bound_username}.ovpn 放到下面路径
# {CLIENT_CONFIG_PATH}"""
    service_cmd = f"""systemctl daemon-reload
systemctl enable --now openvpn-client@client.service
systemctl restart openvpn-client@client.service"""
    firewall_cmd = (
        f"iptables -I FORWARD 1 -s {gs} "
        f'-m comment --comment "{fwd_comment}" -j ACCEPT'
    )
    verify_cmd = f"""systemctl status openvpn-client@client.service --no-pager
tail -n 120 {CLIENT_LOG_PATH}
iptables -S FORWARD | grep 'peer={peer_id}' || true
iptables -t nat -S POSTROUTING | grep 'peer={peer_id}' || true"""
    steps: list[dict[str, Any]] = [
        {
            "title": "准备客户端配置",
            "summary": f"将绑定用户 {bound_username} 的客户端配置放到固定路径，方便官方 systemd 模板读取。",
            "command": prepare_cmd,
        },
        {
            "title": "启动 OpenVPN 客户端",
            "summary": "优先使用发行版官方 openvpn-client@client.service；源码编译场景可改用 openvpn-client.service。",
            "command": service_cmd,
        },
        {
            "title": "放行对端 FORWARD",
            "summary": "允许中心 VPN 地址池进入对端后方网络，规则注释带 peer id，便于后续清理。",
            "command": firewall_cmd,
        },
    ]
    if masquerade_on_peer:
        steps.append(
            {
                "title": "可选 SNAT 转换",
                "summary": "需要让对端后方网络看到统一源地址时启用；目标地址不限制。",
                "command": (
                    f"iptables -t nat -I POSTROUTING 1 -s {gs} "
                    f'-m comment --comment "{masq_comment}" -j MASQUERADE'
                ),
            }
        )
    steps.append(
        {
            "title": "验证状态与规则",
            "summary": "确认客户端已连接、日志正常，并能看到带 peer id 的防火墙规则。",
            "command": verify_cmd,
        }
    )
    commands = [str(step["command"]) for step in steps if step.get("command")]
    return {
        "overview": {
            "peer_name": peer_label,
            "peer_id": peer_id,
            "bound_username": bound_username,
            "lan_cidrs": cidrs,
            "global_subnet": gs,
            "client_config_path": CLIENT_CONFIG_PATH,
            "client_log_path": CLIENT_LOG_PATH,
            "client_status_path": CLIENT_STATUS_PATH,
        },
        "highlights": [
            f"客户端配置固定放在 {CLIENT_CONFIG_PATH}。",
            "CIDR、CCD 或 mesh 策略变更后，需要重连 OpenVPN 客户端。",
            "firewalld active 时可能覆盖 iptables 规则，需按现网策略核对。",
        ],
        "steps": steps,
        "commands": commands,
    }


def build_peer_site_manual_markdown(
    *,
    peer_name: str,
    peer_id: str,
    bound_username: str,
    lan_cidrs: list[str],
    global_subnet: str,
    masquerade_on_peer: bool,
) -> str:
    """根据对端元数据与中心 global_subnet 生成可下载的 Markdown 文本。

    Args:
        peer_name: 对端展示名
        peer_id: 对端 UUID（iptables 注释 peer= 同源）
        bound_username: 绑定的 VPN 用户名（CCD CN）
        lan_cidrs: 对端后方内网 CIDR 列表
        global_subnet: 中心 VPN 全局地址池 CIDR（与配置一致）
        masquerade_on_peer: 是否在说明中强调对端 SNAT 选项

    Returns:
        UTF-8 Markdown 正文
    """
    ctx = build_peer_site_manual_context(
        peer_name=peer_name,
        peer_id=peer_id,
        bound_username=bound_username,
        lan_cidrs=lan_cidrs,
        global_subnet=global_subnet,
        masquerade_on_peer=masquerade_on_peer,
    )
    overview = ctx["overview"]
    cidrs_lines = "\n".join(f"- `{c}`" for c in overview["lan_cidrs"]) or "- （未配置）"
    highlights = "\n".join(f"- {item}" for item in ctx["highlights"])
    steps = "\n\n".join(
        f"## {idx}. {step['title']}\n\n{step['summary']}\n\n```bash\n{step['command']}\n```"
        for idx, step in enumerate(ctx["steps"], start=1)
    )
    return f"""# 对端站点部署说明 — {peer_name}

> 对端 ID：`{peer_id}`  
> 绑定 VPN 用户（CN）：`{bound_username}`
> 全局 VPN 地址池：`{overview['global_subnet']}`

## 关键提醒

{highlights}

## 对端内网

{cidrs_lines}

{steps}

---
*由 OpenVPN 管理端根据对端实例元数据自动生成。*
"""
