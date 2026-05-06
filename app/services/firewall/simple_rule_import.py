# -*- coding: utf-8 -*-
"""简写防火墙规则行解析：``-s`` / ``-d``、可选 ``-p`` 与目标端口，动作为业务默认（可识别 ``-j``）。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.models.firewall import FirewallRule
from app.utils.cidr import validate_cidr, validate_iptables_addr_or_cidr, validate_iptables_rest_line_addr_tokens

# 可出现在简写行中的 -j 目标
_ACTION_MAP = {
    "ACCEPT": "accept",
    "DROP": "drop",
    "REJECT": "reject",
}


@dataclass
class SimplifiedLine:
    """单条简写行语义，供中心建 JSON 规则或对端拼 ``iptables`` 片段。"""

    source: str | None
    dest: str | None
    protocol: str
    dest_port: str | None
    action: str


def try_parse_simplified_line(line: str) -> SimplifiedLine | None:
    """从一行文本解析简写。不含 ``-s`` 且不含 ``-d`` 时返回 None。

    行首为 ``-A `` 时返回 None（交由 iptables 全量/片段解析）。

    参数:
        line: 去首尾空白后的一行。

    返回:
        成功返回 :class:`SimplifiedLine`；不识别为简写则返回 None。

    异常:
        无。非法组合由 :func:`parse_center_simplified_lines` 在中心场景下抛出。
    """
    s = (line or "").strip()
    if not s or s.startswith("#") or s.startswith("-A "):
        return None
    m_s = re.search(r"(?:^|\s)-s\s+([^\s]+)", s)
    m_d = re.search(r"(?:^|\s)-d\s+([^\s]+)", s)
    if not m_s and not m_d:
        return None
    src = m_s.group(1).strip() if m_s else None
    dst = m_d.group(1).strip() if m_d else None
    m_p = re.search(r"(?:^|\s)-p\s+([^\s]+)", s, re.IGNORECASE)
    raw_proto = (m_p.group(1).strip().lower() if m_p else "") or "all"
    if raw_proto in ("any",):
        raw_proto = "all"
    m_j = re.search(r"(?:^|\s)-j\s+([^\s]+)", s, re.IGNORECASE)
    j = (m_j.group(1).strip().upper() if m_j else "") or "ACCEPT"
    action = _ACTION_MAP.get(j, "accept")
    dport: str | None = None
    m_dp = re.search(r"(?:^|\s)--dport(?::\s*|\s+)(\S+)", s, re.IGNORECASE)
    m_dps = re.search(r"(?:^|\s)--dports\s+(\S+)", s, re.IGNORECASE)
    m_mpt = re.search(
        r"-m\s+multiport\s+--dports\s+(\S+)", s, re.IGNORECASE
    ) or re.search(r"--match\s+multiport\s+--dports\s+(\S+)", s, re.IGNORECASE)
    if m_mpt:
        dport = m_mpt.group(1).strip()
    elif m_dps:
        dport = m_dps.group(1).strip()
    elif m_dp:
        dport = m_dp.group(1).strip()
    return SimplifiedLine(
        source=src,
        dest=dst,
        protocol=raw_proto,
        dest_port=dport,
        action=action,
    )


def _proto_port_variants(
    protocol: str, dest_port: str | None
) -> list[dict[str, str | None]]:
    """与 :meth:`IptablesManager._protocol_port_variants` 语义对齐，供对端多行 rest 展开。"""
    dest_port = (dest_port or "").strip()
    proto = (protocol or "all").strip().lower()
    if proto in ("any",):
        proto = "all"
    if not dest_port:
        if proto == "all":
            return [{"proto": None, "dport": None, "multiport": None}]
        return [{"proto": proto, "dport": None, "multiport": None}]
    if "," in dest_port:
        ports = ",".join(p.strip() for p in dest_port.split(",") if p.strip())
        if proto == "all":
            return [
                {"proto": "tcp", "dport": None, "multiport": ports},
                {"proto": "udp", "dport": None, "multiport": ports},
            ]
        return [{"proto": proto, "dport": None, "multiport": ports}]
    if ":" in dest_port and dest_port.count(":") == 1:
        if proto == "all":
            return [
                {"proto": "tcp", "dport": dest_port, "multiport": None},
                {"proto": "udp", "dport": dest_port, "multiport": None},
            ]
        return [{"proto": proto, "dport": dest_port, "multiport": None}]
    if proto == "all":
        return [
            {"proto": "tcp", "dport": dest_port, "multiport": None},
            {"proto": "udp", "dport": dest_port, "multiport": None},
        ]
    return [{"proto": proto, "dport": dest_port, "multiport": None}]


def remote_rests_from_create_fields(
    *,
    source_subnet: str | None,
    source_ips: list[str] | None,
    action: str,
    protocol: str,
    dest_ip: str | None,
    dest_port: str | None,
) -> list[str]:
    """与中心页「新建规则」相同的源/目标字段，生成对端用户链 ``-A <chain>`` 之后的 ``rest`` 列表。

    多源 IP 时逐源展开；协议+端口与 :func:`peer_rests_from_simplified_line` 一致（all+ 端口时 tcp/udp 两条）。

    参数:
        source_subnet: 组模式单段 CIDR，或 None。
        source_ips: 用户模式多 IP 列表，或 None。
        action: ``accept`` / ``drop`` / ``reject``。
        protocol: 与中心规则 ``protocol`` 同形。
        dest_ip: 目标，可空。
        dest_port: 目标端口，可空。

    返回:
        非空的 ``rest`` 字符串列表。

    异常:
        ValueError: 源与目标皆空、或端口等无法通过 :class:`~app.models.firewall.FirewallRule` 校验。
    """
    dp = (dest_port or "").strip() or None
    act = str(action or "accept").lower()
    if act not in ("accept", "drop", "reject"):
        act = "accept"
    p = (protocol or "all").strip().lower()
    if p in ("any",):
        p = "all"
    FirewallRule(
        owner_type="group",
        owner_id="_",
        action=act,  # type: ignore[arg-type]
        priority=1,
        dest_port=dp,
    )
    dest = (dest_ip or "").strip() or None
    src_list: list[str | None] = []
    if source_ips:
        for ip in source_ips:
            s = str(ip).strip()
            if s:
                src_list.append(s)
    elif (source_subnet or "").strip():
        src_list.append(str(source_subnet).strip())
    if not src_list:
        src_list = [None]
    if not any(src_list) and not dest:
        raise ValueError("须至少指定源（子网 / 用户 IP）或目标地址之一")
    for s in src_list:
        if (s or "").strip():
            for part in str(s).split(","):
                part = part.strip()
                if part:
                    validate_iptables_addr_or_cidr(part)
    if dest:
        for part in str(dest).split(","):
            part = part.strip()
            if part:
                validate_iptables_addr_or_cidr(part)
    out: list[str] = []
    for src in src_list:
        spec = SimplifiedLine(
            source=src,
            dest=dest,
            protocol=p,
            dest_port=dp,
            action=act,
        )
        out.extend(peer_rests_from_simplified_line(spec))
    for line in out:
        validate_iptables_rest_line_addr_tokens(line)
    return out


def peer_rests_from_simplified_line(spec: SimplifiedLine) -> list[str]:
    """将简写行展开为对端链 ``-A <chain>`` 之后的 ``rest`` 列表（可多条：all+ 端口时 tcp/udp 各一条）。"""
    target = {"accept": "ACCEPT", "drop": "DROP", "reject": "REJECT"}.get(
        spec.action, "ACCEPT"
    )
    out: list[str] = []
    for pv in _proto_port_variants(spec.protocol, spec.dest_port):
        parts: list[str] = []
        if (spec.source or "").strip():
            parts.append(f"-s {spec.source.strip()}")
        if (spec.dest or "").strip():
            parts.append(f"-d {spec.dest.strip()}")
        p = pv.get("proto")
        if p:
            parts.append(f"-p {p}")
        if pv.get("multiport"):
            parts.append(f"-m multiport --dports {pv['multiport']}")
        elif pv.get("dport"):
            parts.append(f"--dport {pv['dport']}")
        parts.append(f"-j {target}")
        out.append(" ".join(parts))
    return out


def parse_center_simplified_lines(text: str) -> list[SimplifiedLine]:
    """解析多行简写，供中心在**当前归属**下逐条 ``create``。

    每行须含 ``-s`` 或 ``-d``；不得出现行首 ``-A `` 片段（与整表 iptables 区分）。

    参数:
        text: 多行文本，``#`` 为注释，空行忽略。

    返回:
        非空时返回 :class:`SimplifiedLine` 列表。

    异常:
        ValueError: 无有效行、含 ``-A``、或某行无法作为简写解析。
    """
    out: list[SimplifiedLine] = []
    for n, line in enumerate(text.splitlines(), start=1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if raw.startswith("-A "):
            raise ValueError(
                "中心简写导入不能包含 -A 行；整库请使用导出的 backup JSON，"
                "或去掉 -A 链名后仅贴规则体。"
            )
        spec = try_parse_simplified_line(raw)
        if spec is None:
            raise ValueError(
                f"第 {n} 行需为简写且至少含 -s 或 -d，当前: {raw[:200]!r}"
            )
        out.append(spec)
    if not out:
        raise ValueError("无有效行（# 与空行除外）。")
    return out


def center_rule_payload_from_simplified(
    spec: SimplifiedLine,
    *,
    owner_type: str,
    owner_id: str,
    instance: str,
    source_subnet: str | None,
    source_ips: list[str] | None,
) -> dict[str, Any]:
    """由简写行得到 :meth:`FirewallRuleService.create` 所需字段（已含 owner/deployment/enable）。"""
    p = (spec.protocol or "all").lower()
    if p in ("any",):
        p = "all"
    return {
        "owner_type": owner_type,
        "owner_id": owner_id,
        "instance": instance,
        "deployment_target": "center",
        "action": spec.action,
        "protocol": p,
        "source_subnet": source_subnet,
        "source_ips": source_ips,
        "dest_ip": (spec.dest or "").strip() or None,
        "dest_port": (spec.dest_port or "").strip() or None,
        "enabled": True,
    }


def is_center_backup_json_text(text: str) -> bool:
    """若文本为 ``backup()`` 的 JSON 根对象则返回 True（含 ``rules_by_owner``）。"""
    t = (text or "").strip()
    if not t.startswith("{"):
        return False
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict) and "rules_by_owner" in data


def resolve_center_owner_type(owner_id: str) -> str:
    """根据已选归属 id 判断 group 或 user。找不到时抛出明确错误。

    参数:
        owner_id: 侧栏/搜索选中的归属 id（组 id 或用户名）。

    返回:
        ``'group'`` 或 ``'user'``。

    异常:
        ValueError: 在组/用户中均找不到。
    """
    from app.services.group.crud import GroupService
    from app.services.user.crud import UserService

    oid = (owner_id or "").strip()
    if not oid:
        raise ValueError("归属为空。")
    for g in GroupService().list_all():
        if str(g.get("id") or "") == oid:
            return "group"
    for u in UserService().list_all():
        if str(u.get("username") or "") == oid:
            return "user"
    raise ValueError(f"找不到归属: {oid!r}（需为现网组或用户）。")


def _normalize_group_source_s(src: str) -> str:
    """组归属单段源；无掩码的 IPv4 补为 /32。"""
    t = (src or "").strip()
    if not t:
        raise ValueError("组归属 -s 为空。")
    if "," in t:
        raise ValueError("组归属简写 -s 仅支持单段，不要逗号分隔。")
    if validate_cidr(t):
        return t
    if re.match(r"^(?:\d{1,3}\.){3}\d{1,3}$", t):
        cand = f"{t}/32"
        if not validate_cidr(cand):
            raise ValueError(f"非法源地址: {t!r}")
        return cand
    raise ValueError(f"组归属 -s 须为 CIDR 或 IPv4: {t!r}")


def _parse_user_source_commas(text: str) -> tuple[str | None, list[str] | None]:
    """与 firewall 页 ``_parse_user_source_for_create`` 同逻辑，供中心简写复用（避免从 UI 回引）。"""
    t = (text or "").strip()
    if not t:
        return None, None
    chunks = [x.strip() for x in t.split(",") if x.strip()]
    if not chunks:
        return None, None
    cidrs = [c for c in chunks if validate_cidr(c)]
    if len(cidrs) == len(chunks) and len(chunks) == 1:
        return cidrs[0], None
    for c in chunks:
        if not validate_cidr(c) and re.match(r"^\d{1,3}(\.\d{1,3}){3}$", c):
            return None, list(chunks)
    raise ValueError("用户源 -s 需为单段 CIDR，或为逗号分隔的仅数字 IPv4 列表。")


def source_fields_for_center(
    spec: SimplifiedLine, owner_type: str
) -> tuple[str | None, list[str] | None]:
    """由简写源字段得到 ``source_subnet`` / ``source_ips``。"""
    if not (spec.source or "").strip():
        return None, None
    if owner_type == "group":
        return _normalize_group_source_s((spec.source or "").strip()), None
    return _parse_user_source_commas((spec.source or "").strip())
