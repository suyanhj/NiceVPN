# -*- coding: utf-8 -*-
"""经 SSH 在对端主机写入/清理 FORWARD（及可选 MASQUERADE）。

**与中心的关系（双向）**：中心侧已在 ``VPN_FORWARD`` 放行「源 = 对端内网」；对端侧必须放行
「源 = 中心 VPN 地址池（global_subnet）、从隧道入站」，否则中心发起的访问到不了对端内网。
本模块即对端这一侧（你允许我从隧道进你家门）；中心那一侧由 ``iptables_mgr`` 合并重建。
可选 SNAT 用于“中心 VPN 地址池从隧道进入对端”时在对端做源地址转换，目标不做限制。

规则注释统一带 ``ovpn-mgmt-peer`` 与 ``peer=<对端 id>``，与中心侧清理语义一致，避免误删。
"""
from __future__ import annotations

import logging
import re
import shlex
from typing import Any

import paramiko

from app.services.firewall.iptables_mgr import IptablesManager
from app.services.peer_instance.ccd_merge import sorted_unique_ipv4_cidrs
from app.services.peer_instance.peer_ssh_connect import connect_peer_ssh_client_from_row
from app.utils.cidr import validate_iptables_rest_line_addr_tokens

logger = logging.getLogger("peer.remote")

# 与 peer_manual_md / 运维文档对齐；清理时匹配子串 peer=<id>
COMMENT_FWD_TMPL = "ovpn-mgmt-peer peer={peer_id} role=fwd-global"
COMMENT_JUMP_TMPL = "ovpn-mgmt-peer peer={peer_id} role=jump-chain"
COMMENT_MASQ_TMPL = "ovpn-mgmt-peer peer={peer_id} role=masq idx={idx}"


def _exec_ssh(
    client: paramiko.SSHClient,
    command: str,
    *,
    timeout: int,
) -> tuple[str, str, int]:
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out_b = stdout.read()
    err_b = stderr.read()
    code = stdout.channel.recv_exit_status()
    return out_b.decode("utf-8", errors="replace"), err_b.decode("utf-8", errors="replace"), code


def _detect_sudo_prefix(client: paramiko.SSHClient, *, timeout: int = 15) -> str:
    """返回 ``sudo -n `` 或 ``''``（已为 root）。"""
    _, _, c = _exec_ssh(client, "sudo -n true 2>/dev/null", timeout=timeout)
    if c == 0:
        logger.debug("远端 SSH 用户可使用 sudo -n")
        return "sudo -n "
    out, _, c2 = _exec_ssh(client, "id -u", timeout=timeout)
    if c2 == 0 and out.strip() == "0":
        logger.debug("远端 SSH 为 root，无需 sudo")
        return ""
    raise RuntimeError("远端既不是 root，也无法无密码 sudo（sudo -n），无法修改 iptables")


def _remove_rules_matching_peer(
    client: paramiko.SSHClient,
    peer_id: str,
    sp: str,
    *,
    timeout: int,
) -> int:
    """删除 filter/FORWARD 与 nat/POSTROUTING 中带 ``peer=<peer_id>`` 且含 ovpn-mgmt-peer 的规则。返回删除条数估算。"""
    removed = 0
    peer_tag = f"peer={peer_id}"
    for table, chain in (("filter", "FORWARD"), ("nat", "POSTROUTING")):
        tpart = "" if table == "filter" else "-t nat "
        out, err, code = _exec_ssh(client, f"{sp}iptables {tpart}-S {chain}", timeout=timeout)
        if code != 0:
            raise RuntimeError(f"iptables -S {chain} 失败: {err.strip() or out}")
        prefix = f"-A {chain} "
        for line in out.splitlines():
            s = line.strip()
            if not s.startswith(prefix):
                continue
            if peer_tag not in s or "ovpn-mgmt-peer" not in s:
                continue
            spec = s[len(prefix) :]
            del_cmd = f"{sp}iptables {tpart}-D {chain} {spec}"
            _, derr, dcode = _exec_ssh(client, del_cmd, timeout=timeout)
            if dcode == 0:
                removed += 1
            else:
                logger.warning("删除规则失败（可忽略若已不存在）: %s | %s", del_cmd, derr.strip())
    return removed


def _remove_remote_peer_chain_and_jump(
    client: paramiko.SSHClient,
    chain: str,
    sp: str,
    *,
    timeout: int,
) -> None:
    """删除 FORWARD 中 ``-j <chain>`` 跳转并 ``-F/-X`` 该链（对端本机 ``VPN_PEER_<hash>``）。"""
    out, err, code = _exec_ssh(client, f"{sp}iptables -S FORWARD", timeout=timeout)
    if code != 0:
        raise RuntimeError(f"iptables -S FORWARD 失败: {err.strip() or out}")
    prefix = "-A FORWARD "
    for line in out.splitlines():
        s = line.strip()
        if not s.startswith(prefix):
            continue
        if f"-j {chain}" not in s:
            continue
        spec = s[len(prefix) :]
        del_cmd = f"{sp}iptables -D FORWARD {spec}"
        _exec_ssh(client, del_cmd, timeout=timeout)
    _exec_ssh(client, f"{sp}iptables -F {shlex.quote(chain)} 2>/dev/null || true", timeout=timeout)
    _exec_ssh(client, f"{sp}iptables -X {shlex.quote(chain)} 2>/dev/null || true", timeout=timeout)


def _peer_site_rules_already_current(
    client: paramiko.SSHClient,
    peer_id: str,
    chain: str,
    sp: str,
    global_subnet: str,
    want_masquerade: bool,
    *,
    timeout: int,
) -> bool:
    """检测当前规则是否已与本次期望一致，避免重复清理和重写。"""
    peer_tag = f"peer={peer_id}"
    out_forward, err_forward, code_forward = _exec_ssh(client, f"{sp}iptables -S FORWARD", timeout=timeout)
    if code_forward != 0:
        raise RuntimeError(f"iptables -S FORWARD 失败: {err_forward.strip() or out_forward}")

    out_chain, _, code_chain = _exec_ssh(client, f"{sp}iptables -S {shlex.quote(chain)}", timeout=timeout)
    if code_chain != 0:
        return False

    out_nat, err_nat, code_nat = _exec_ssh(client, f"{sp}iptables -t nat -S POSTROUTING", timeout=timeout)
    if code_nat != 0:
        raise RuntimeError(f"iptables -S POSTROUTING 失败: {err_nat.strip() or out_nat}")

    forward_peer_lines = [
        line for line in out_forward.splitlines() if peer_tag in line and "ovpn-mgmt-peer" in line
    ]
    chain_peer_lines = [line for line in out_chain.splitlines() if peer_tag in line and "ovpn-mgmt-peer" in line]
    nat_peer_lines = [line for line in out_nat.splitlines() if peer_tag in line and "ovpn-mgmt-peer" in line]

    c_jump = COMMENT_JUMP_TMPL.format(peer_id=peer_id)
    jump_ok = len(forward_peer_lines) == 1 and all(
        part in forward_peer_lines[0] for part in (c_jump, f"-j {chain}")
    )
    c_fwd = COMMENT_FWD_TMPL.format(peer_id=peer_id)
    fwd_ok = len(chain_peer_lines) == 1 and all(
        part in chain_peer_lines[0] for part in (f"-s {global_subnet}", c_fwd, "-j ACCEPT")
    )
    if not jump_ok or not fwd_ok:
        return False

    if not want_masquerade:
        return len(nat_peer_lines) == 0
    if len(nat_peer_lines) != 1:
        return False
    c_nat = COMMENT_MASQ_TMPL.format(peer_id=peer_id, idx=0)
    return all(part in nat_peer_lines[0] for part in (f"-s {global_subnet}", c_nat, "-j MASQUERADE"))


def apply_peer_site_iptables_via_ssh(
    row: dict,
    global_subnet: str,
    *,
    force: bool = False,
    exec_timeout: int = 120,
) -> dict[str, Any]:
    """SSH 登录对端：先清理本 peer 旧规则，再写入 FORWARD（global_subnet 为源）及可选 MASQUERADE。

    Args:
        row: 对端实例 dict（须含 id、ssh_*、lan_cidrs、masquerade_on_peer）
        global_subnet: 中心 VPN 地址池 CIDR
    Returns:
        ok、messages、warnings、removed_count 等

    Raises:
        ValueError: 参数缺失
        RuntimeError: SSH/iptables 失败
    """
    peer_id = str(row.get("id") or "").strip()
    if not peer_id:
        raise ValueError("对端 id 为空")
    gs = str(global_subnet or "").strip()
    if not gs:
        raise ValueError("global_subnet 未配置，请在系统设置中填写后再下发对端规则")
    warnings: list[str] = []
    client = connect_peer_ssh_client_from_row(row, connect_timeout=25)
    try:
        out_act, _, c_act = _exec_ssh(client, "systemctl is-active firewalld 2>/dev/null", timeout=10)
        if c_act == 0 and out_act.strip() == "active":
            msg = "远端 firewalld 处于 active，iptables-nft 规则可能被 firewalld 覆盖，请现网核对"
            logger.warning(msg)
            warnings.append(msg)

        sp = _detect_sudo_prefix(client, timeout=15)
        chain = IptablesManager.peer_chain_name_for_id(peer_id)
        want_masquerade = bool(row.get("masquerade_on_peer"))
        if not force and _peer_site_rules_already_current(
            client,
            peer_id,
            chain,
            sp,
            gs,
            want_masquerade,
            timeout=exec_timeout,
        ):
            logger.info("对端 SSH iptables 已是最新，跳过重写 peer=%s masq=%s", peer_id, int(want_masquerade))
            return {
                "ok": True,
                "peer_id": peer_id,
                "global_subnet": gs,
                "removed_prior": 0,
                "masquerade_rules": int(want_masquerade),
                "already_current": True,
                "warnings": warnings,
            }

        _remove_remote_peer_chain_and_jump(client, chain, sp, timeout=exec_timeout)
        removed = _remove_rules_matching_peer(client, peer_id, sp, timeout=exec_timeout)

        c_fwd = COMMENT_FWD_TMPL.format(peer_id=peer_id)
        c_jump = COMMENT_JUMP_TMPL.format(peer_id=peer_id)
        mk = f"{sp}iptables -N {shlex.quote(chain)}"
        _, err_n, cn = _exec_ssh(client, mk, timeout=exec_timeout)
        if cn != 0:
            logger.warning("创建链 %s（可能已存在将依赖后续 -A）: %s", chain, (err_n or "").strip())
        fw_rule = (
            f"{sp}iptables -A {shlex.quote(chain)} -s {shlex.quote(gs)} "
            f"-m comment --comment {shlex.quote(c_fwd)} -j ACCEPT"
        )
        _, err_f, cf = _exec_ssh(client, fw_rule, timeout=exec_timeout)
        if cf != 0:
            raise RuntimeError(f"写入对端链 {chain} 规则失败: {err_f.strip()}")
        jump_cmd = (
            f"{sp}iptables -I FORWARD 1 -m comment --comment {shlex.quote(c_jump)} "
            f"-j {shlex.quote(chain)}"
        )
        _, err_j, cj = _exec_ssh(client, jump_cmd, timeout=exec_timeout)
        if cj != 0:
            raise RuntimeError(f"写入 FORWARD 跳转至 {chain} 失败: {err_j.strip()}")

        masq_n = 0
        if want_masquerade:
            c_nat = COMMENT_MASQ_TMPL.format(peer_id=peer_id, idx=0)
            nat_cmd = (
                f"{sp}iptables -t nat -I POSTROUTING 1 -s {shlex.quote(gs)} "
                f"-m comment --comment {shlex.quote(c_nat)} -j MASQUERADE"
            )
            _, err_n, cn = _exec_ssh(client, nat_cmd, timeout=exec_timeout)
            if cn != 0:
                raise RuntimeError(f"写入 POSTROUTING SNAT 失败: {err_n.strip()}")
            masq_n = 1

        logger.info(
            "对端 SSH iptables 下发成功 peer=%s removed=%s masq=%s",
            peer_id,
            removed,
            masq_n,
        )
        return {
            "ok": True,
            "peer_id": peer_id,
            "global_subnet": gs,
            "removed_prior": removed,
            "masquerade_rules": masq_n,
            "already_current": False,
            "warnings": warnings,
        }
    finally:
        client.close()


def remove_peer_site_iptables_via_ssh(row: dict, *, exec_timeout: int = 120) -> dict[str, Any]:
    """仅清理对端上带本 peer 标记的 FORWARD/POSTROUTING 规则（不重加）。"""
    peer_id = str(row.get("id") or "").strip()
    if not peer_id:
        raise ValueError("对端 id 为空")
    client = connect_peer_ssh_client_from_row(row, connect_timeout=25)
    try:
        sp = _detect_sudo_prefix(client, timeout=15)
        chain = IptablesManager.peer_chain_name_for_id(peer_id)
        _remove_remote_peer_chain_and_jump(client, chain, sp, timeout=exec_timeout)
        removed = _remove_rules_matching_peer(client, peer_id, sp, timeout=exec_timeout)
        logger.info("对端 SSH iptables 已清理 peer=%s removed=%s", peer_id, removed)
        return {"ok": True, "peer_id": peer_id, "removed": removed}
    finally:
        client.close()


def _filter_chain_rests_from_iptables_s(out: str, chain: str) -> list[str]:
    """从 ``iptables -S <chain>`` 输出解析出每条规则在 ``-A <chain>`` 之后的片段。"""
    prefix = f"-A {chain} "
    rules: list[str] = []
    for line in out.splitlines():
        s = line.strip()
        if s.startswith(prefix):
            rules.append(s[len(prefix) :].strip())
    return rules


def _forward_lines_jumping_to_chain(iptables_s_forward: str, chain: str) -> list[str]:
    out: list[str] = []
    for line in iptables_s_forward.splitlines():
        s = line.strip()
        if not s.startswith("-A FORWARD "):
            continue
        if f"-j {chain}" not in s:
            continue
        out.append(s)
    return out


def _chain_missing_message(stderr_out: str) -> bool:
    t = (stderr_out or "").lower()
    return "no chain" in t or "does not exist" in t or "inexistent" in t


def fetch_peer_filter_chain_snapshot_via_ssh(
    row: dict,
    *,
    exec_timeout: int = 60,
) -> dict[str, Any]:
    """经 SSH 读取对端：本对端 ``VPN_PEER_<hash>`` 链内规则 + FORWARD 中指向该链的匹配行（原文）。"""
    peer_id = str(row.get("id") or "").strip()
    if not peer_id:
        raise ValueError("对端 id 为空")
    chain = IptablesManager.peer_chain_name_for_id(peer_id)
    client = connect_peer_ssh_client_from_row(row, connect_timeout=25)
    try:
        sp = _detect_sudo_prefix(client, timeout=15)
        chq = shlex.quote(chain)
        f_out, f_err, f_code = _exec_ssh(client, f"{sp}iptables -S FORWARD", timeout=exec_timeout)
        if f_code != 0:
            raise RuntimeError(f"iptables -S FORWARD 失败: {(f_err or f_out or '').strip()}")
        forward_refs = _forward_lines_jumping_to_chain(f_out, chain)
        out, err, code = _exec_ssh(client, f"{sp}iptables -S {chq}", timeout=exec_timeout)
        if code != 0:
            msg = f"{(err or '').strip()}\n{(out or '').strip()}"
            if _chain_missing_message(msg):
                return {
                    "ok": True,
                    "chain": chain,
                    "chain_exists": False,
                    "chain_rests": [],
                    "forward_refs": forward_refs,
                }
            raise RuntimeError(f"iptables -S {chain} 失败: {msg}")
        rests = _filter_chain_rests_from_iptables_s(out, chain)
        return {
            "ok": True,
            "chain": chain,
            "chain_exists": True,
            "chain_rests": rests,
            "forward_refs": forward_refs,
        }
    finally:
        client.close()


def replace_peer_filter_chain_rests_via_ssh(
    row: dict,
    rests: list[str],
    *,
    exec_timeout: int = 120,
) -> None:
    """清空对端本对端 ``VPN_PEER_*`` 链后按序 ``-A`` 写回。``rests`` 为与 ``-S`` 解析得到的片段同形字符串。"""
    peer_id = str(row.get("id") or "").strip()
    if not peer_id:
        raise ValueError("对端 id 为空")
    expect = IptablesManager.peer_chain_name_for_id(peer_id)
    for r in rests:
        s = (r or "").strip()
        if s and (("\n" in s) or ("\r" in s) or ("\x00" in s)):
            raise ValueError("单条规则含非法字符")
    for r in rests:
        s = (r or "").strip()
        if s:
            # 在 ``-F`` 清链**之前**校验，避免已下发失败且远端链已被清空
            validate_iptables_rest_line_addr_tokens(s)
    client = connect_peer_ssh_client_from_row(row, connect_timeout=25)
    try:
        sp = _detect_sudo_prefix(client, timeout=15)
        chq = shlex.quote(expect)
        pout, perr, pcode = _exec_ssh(client, f"{sp}iptables -S {chq}", timeout=exec_timeout)
        if pcode != 0:
            msg = f"{(perr or '').strip()}\n{(pout or '').strip()}"
            if _chain_missing_message(msg):
                raise ValueError(
                    f"对端上尚不存在链 {expect}。请先在「组网对端」对该节点执行「SSH 下发对端防火墙」以创建链。"
                )
            raise RuntimeError(f"无法读取对端链 {expect}: {msg}")
        _exec_ssh(client, f"{sp}iptables -F {chq}", timeout=exec_timeout)
        n = 0
        for r in rests:
            body = (r or "").strip()
            if not body:
                continue
            cmd = f"{sp}iptables -A {chq} {body}"
            o2, e2, c2 = _exec_ssh(client, cmd, timeout=exec_timeout)
            if c2 != 0:
                raise RuntimeError(f"追加对端链规则失败: {cmd}\n{(e2 or o2 or '').strip()}")
            n += 1
        logger.debug("对端链 %s 已写回 %s 条", expect, n)
    finally:
        client.close()
