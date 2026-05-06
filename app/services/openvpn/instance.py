# -*- coding: utf-8 -*-
"""OpenVPN 多实例管理服务 — 配置生成、启停控制、状态读取"""
import ipaddress
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.core.constants import (
    CCD_DIR,
    MGMT_PORT_START,
    OPENVPN_DAEMON_LOG_DIR,
    OPENVPN_DEFAULT_MAX_CLIENTS,
    OPENVPN_ETC_DIR,
)

logger = logging.getLogger(__name__)

# 生成内容比对时忽略「生成时间」行，否则每次启动都会因时间戳不同而误判为变更
_SERVER_CONF_GEN_TIME_LINE = re.compile(r"^# 生成时间:.*\r?\n", re.MULTILINE)


def resolve_server_conf_dir(openvpn_conf_dir: str | Path | None = None) -> Path:
    """解析官方服务端实例配置目录：``<openvpn_conf_dir>/server``。"""
    base = Path(str(openvpn_conf_dir or OPENVPN_ETC_DIR))
    if base.name == "server":
        return base
    return base / "server"


def _normalize_server_conf_for_compare(text: str) -> str:
    """去掉动态时间行并统一换行，用于判断是否与磁盘上 conf 实质相同。"""
    normalized = text.replace("\r\n", "\n")
    return _SERVER_CONF_GEN_TIME_LINE.sub("", normalized)


def _format_push_lan_routes_block(routes: list | None) -> str:
    """将 IPv4 CIDR 列表格式化为 OpenVPN push route 指令块；非法项跳过并打日志。"""
    lines_out: list[str] = []
    for raw in routes or []:
        s = str(raw).strip()
        if not s:
            continue
        try:
            net = ipaddress.ip_network(s, strict=False)
        except ValueError:
            logger.warning("跳过无效 push_lan_routes 项: %s", s)
            continue
        if net.version != 4:
            logger.warning("跳过非 IPv4 push_lan_routes 项: %s", s)
            continue
        nw = net.network_address
        nm = net.netmask
        lines_out.append(f'push "route {nw} {nm}"')
    if not lines_out:
        return ""
    header = (
        "# ======== 推送给客户端的内网路由（客户端经 VPN 访问服务端所在局域网）========"
    )
    return "\n" + header + "\n" + "\n".join(lines_out) + "\n"


def generate_server_conf(instance_name: str, config: dict) -> str:
    """
    生成完整的 OpenVPN server.conf 配置文件内容。
    所有配置通过文件管理，禁止命令行参数覆盖。

    必须包含当前项目要求的 OpenVPN 指令：
    - tls-crypt-v2, tls-crypt-v2-max-age 3650
    - status <file> 30, status-version 2
    - connect-freq 10 60
    - crl-verify, client-config-dir
    - management 127.0.0.1 <port>
    - client-connect, topology subnet
    """
    # 计算管理接口端口：基础端口 + 实例索引
    mgmt_port = config.get("mgmt_port", MGMT_PORT_START)

    # 基础目录
    base_dir = config.get("openvpn_conf_dir", "/etc/openvpn")
    pki_dir = config.get("pki_dir", f"{base_dir}/pki")
    ipp_file = (OPENVPN_ETC_DIR / f"ipp-{instance_name}.txt").as_posix()
    daemon_log = (OPENVPN_DAEMON_LOG_DIR / f"{instance_name}.log").as_posix()
    daemon_status = (OPENVPN_DAEMON_LOG_DIR / f"{instance_name}-status.log").as_posix()

    ccd_dir = CCD_DIR.as_posix()
    sn = str(config.get("server_network", "10.8.0.0"))
    sm = str(config.get("server_mask", "255.255.0.0"))
    push_block = _format_push_lan_routes_block(config.get("push_lan_routes"))

    conf = f"""# OpenVPN 服务端配置 — 实例 {instance_name}
# 由 OpenVPN 管理系统自动生成，请勿手动修改
# 生成时间: {datetime.now(timezone.utc).isoformat()}

# ======== 基础网络 ========
port {config.get('port', 1194)}
proto {config.get('proto', 'udp')}
dev tun
server {sn} {sm}
topology subnet{push_block}
# 允许已连客户端间互访对方隧道 IP；未加此项时两客户端虚拟地址互访会失败
client-to-client
# ======== TLS 安全 ========
ca {pki_dir}/ca.crt
cert {pki_dir}/issued/{instance_name}.crt
key {pki_dir}/private/{instance_name}.key
dh {pki_dir}/dh.pem

tls-crypt-v2 {base_dir}/tc2-server.key
tls-crypt-v2-max-age 3650
tls-version-min 1.2
data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305
cipher AES-256-GCM
auth SHA512

# ======== CRL 与证书验证 ========
crl-verify {pki_dir}/crl.pem
verify-client-cert require

# ======== 客户端连接脚本 ========
script-security 2
client-connect {base_dir}/scripts/device-bind.sh

# ======== 客户端专属配置目录（CCD）========
client-config-dir {ccd_dir}

# ======== IP 池持久化 ========
ifconfig-pool-persist {ipp_file}

# ======== 连接保活 ========
keepalive 10 120

# ======== 状态与日志（见 app.core.constants：/etc/openvpn/log/）========
status {daemon_status} 30
status-version 2
log-append {daemon_log}
verb 3

# ======== 管理接口 ========
management 127.0.0.1 {mgmt_port}

# ======== DoS 防护 ========
connect-freq 10 60
max-clients {int(config.get("max_clients", OPENVPN_DEFAULT_MAX_CLIENTS))}
# ======== 运行权限（状态数据在 /etc/openvpn，安装后 chown -R nobody:nobody）========
user nobody
group nobody
persist-key
persist-tun

# ======== UDP 优雅退出通知 ========
explicit-exit-notify 1
"""
    return conf


def write_server_conf(instance_name: str, config: dict, conf_dir: str = "/etc/openvpn") -> Path:
    """将生成的配置写入文件，返回文件路径"""
    merged = dict(config)
    if "push_lan_routes" not in merged:
        from app.core.config import load_config

        sys_cfg = load_config()
        merged["push_lan_routes"] = list(sys_cfg.get("push_lan_routes") or [])
    content = generate_server_conf(instance_name, merged)
    conf_path = resolve_server_conf_dir(conf_dir) / f"{instance_name}.conf"
    conf_path.parent.mkdir(parents=True, exist_ok=True)
    conf_path.write_text(content, encoding="utf-8")
    if os.name != "nt":
        from app.utils.posix_data_perms import apply_openvpn_runtime_permissions

        base = Path(str(config.get("openvpn_conf_dir") or conf_dir))
        pki_s = str(merged.get("pki_dir") or "").strip()
        pki = Path(pki_s) if pki_s else (base / "pki")
        apply_openvpn_runtime_permissions(base, pki)
    return conf_path


def regenerate_all_server_confs() -> None:
    """根据当前 load_config() 为所有已注册实例写入 server.conf（含 push_lan_routes、mgmt_port）。

    若与磁盘已有内容实质相同（忽略「生成时间」注释行），则跳过写入与 chown，减轻启动时重复写盘。
    """
    from ipaddress import IPv4Network

    from app.core.config import load_config

    cfg = load_config()
    subnet = str(cfg.get("global_subnet") or "").strip()
    if not subnet:
        logger.warning("global_subnet 未配置，跳过批量重写 server.conf")
        return
    try:
        network = IPv4Network(subnet, strict=False)
    except ValueError as exc:
        logger.error("global_subnet 无效，跳过批量重写 server.conf: %s", exc)
        return
    ovpn_base = str(cfg.get("openvpn_conf_dir") or "/etc/openvpn")
    from app.services.openvpn.script_sync import sync_packaged_openvpn_scripts

    try:
        sync_packaged_openvpn_scripts(ovpn_base)
    except OSError as exc:
        logger.error("同步 OpenVPN 钩子脚本失败: %s", exc)
        raise

    inst_map = cfg.get("instances") or {}
    if not inst_map:
        logger.warning("instances 为空，跳过批量重写 server.conf")
        return
    names_sorted = sorted(inst_map.keys())
    push_routes = list(cfg.get("push_lan_routes") or [])
    written = 0
    skipped = 0
    conf_dir_path = resolve_server_conf_dir(ovpn_base)
    for idx, inst_name in enumerate(names_sorted):
        conf_config = {
            "server_network": str(network.network_address),
            "server_mask": str(network.netmask),
            "port": cfg.get("port", 1194),
            "proto": cfg.get("proto", "udp"),
            "pki_dir": str(cfg.get("pki_dir") or ""),
            "openvpn_conf_dir": ovpn_base,
            "mgmt_port": MGMT_PORT_START + idx,
            "push_lan_routes": push_routes,
            "max_clients": int(cfg.get("max_clients") or OPENVPN_DEFAULT_MAX_CLIENTS),
        }
        new_text = generate_server_conf(inst_name, conf_config)
        conf_path = conf_dir_path / f"{inst_name}.conf"
        if conf_path.is_file():
            try:
                old_text = conf_path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("读取 %s 失败，将重写: %s", conf_path, exc)
                old_text = None
            if old_text is not None:
                if _normalize_server_conf_for_compare(old_text) == _normalize_server_conf_for_compare(
                    new_text
                ):
                    logger.debug("实例 %s 的 server.conf 无实质变化，跳过写入", inst_name)
                    skipped += 1
                    continue
        write_server_conf(inst_name, conf_config, conf_dir=ovpn_base)
        written += 1
        logger.info(
            "已重写实例 %s 的 server.conf（mgmt_port=%s）",
            inst_name,
            conf_config["mgmt_port"],
        )
    if written == 0 and skipped > 0:
        logger.info("全部 %d 个实例 server.conf 与当前配置一致，未写入磁盘", skipped)
    elif skipped > 0:
        logger.info(
            "server.conf：已更新 %d 个实例，%d 个未变化已跳过",
            written,
            skipped,
        )


def start_instance(name: str) -> bool:
    """启动指定 OpenVPN 实例"""
    try:
        subprocess.run(
            ["systemctl", "start", f"openvpn-server@{name}"],
            check=True, capture_output=True, text=True
        )
        return True
    except subprocess.CalledProcessError:
        return False


def stop_instance(name: str) -> bool:
    """停止指定 OpenVPN 实例"""
    try:
        subprocess.run(
            ["systemctl", "stop", f"openvpn-server@{name}"],
            check=True, capture_output=True, text=True
        )
        return True
    except subprocess.CalledProcessError:
        return False


def restart_instance(name: str) -> bool:
    """重启指定 OpenVPN 实例"""
    try:
        subprocess.run(
            ["systemctl", "restart", f"openvpn-server@{name}"],
            check=True, capture_output=True, text=True
        )
        return True
    except subprocess.CalledProcessError:
        return False


def is_instance_active(name: str) -> bool:
    """检测实例是否正在运行"""
    result = subprocess.run(
        ["systemctl", "is-active", f"openvpn-server@{name}"],
        capture_output=True, text=True
    )
    return result.stdout.strip() == "active"


def iter_all_openvpn_instance_names() -> list[str]:
    """config.instances 与官方 server 配置目录下 *.conf 并集（与服务管理页实例列表一致）。"""
    from pathlib import Path

    from app.core.config import load_config

    cfg = load_config()
    names = set((cfg.get("instances") or {}).keys())
    conf_dir = resolve_server_conf_dir(str(cfg.get("openvpn_conf_dir") or "/etc/openvpn"))
    if conf_dir.is_dir():
        for p in conf_dir.glob("*.conf"):
            names.add(p.stem)
    return sorted(names)


def any_openvpn_instance_active() -> bool:
    """任一已登记/已发现实例的 systemd openvpn-server@ 为 active 则返回 True。"""
    for name in iter_all_openvpn_instance_names():
        if is_instance_active(name):
            return True
    return False


def get_local_openvpn_instance_id() -> str:
    """本机 VPN 实例标识：优先 config.vpn_instance_id，否则取已注册/扫描到的实例名，默认 server。

    与 iptables ``ovpn-mgmt-* inst=<id>`` 注释一致，用于区分本机与未来远端节点下发的规则。
    """
    from app.core.config import load_config

    cfg = load_config()
    explicit = str(cfg.get("vpn_instance_id") or "").strip()
    if explicit:
        return explicit
    names = iter_all_openvpn_instance_names()
    if names:
        return names[0]
    return "server"


def _split_status_v2_line(line: str) -> list[str]:
    """OpenVPN status-version 2 单行字段分隔：官方默认为逗号；少数环境可能为制表符。"""
    if "\t" in line:
        return line.split("\t")
    return line.split(",")


def iter_instance_mgmt_ports() -> list[tuple[str, int]]:
    """与 regenerate_all_server_confs 相同顺序，返回 (实例名, management 端口)。"""
    from app.core.config import load_config

    names = sorted((load_config().get("instances") or {}).keys())
    return [(n, MGMT_PORT_START + i) for i, n in enumerate(names)]


def parse_status_file_path_from_server_conf(conf_path: Path) -> str | None:
    """从实例 server.conf 解析 `status <file> [interval]` 中的文件路径；无则返回 None。"""
    if not conf_path.is_file():
        return None
    try:
        text = conf_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("读取 conf 以解析 status 失败 %s: %s", conf_path, exc)
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "#" in line:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "status":
            return parts[1]
    return None


def resolve_status_log_path(instance_name: str, *, openvpn_conf_dir: str | None = None) -> Path:
    """OpenVPN status 文件路径：优先读 server.conf 中 status 指令；否则新目录、再 legacy。"""
    if openvpn_conf_dir:
        conf = resolve_server_conf_dir(openvpn_conf_dir) / f"{instance_name}.conf"
        parsed = parse_status_file_path_from_server_conf(conf)
        if parsed:
            return Path(parsed)
    preferred = OPENVPN_DAEMON_LOG_DIR / f"{instance_name}-status.log"
    legacy = Path(f"/var/log/openvpn-{instance_name}-status.log")
    if preferred.exists():
        return preferred
    if legacy.exists():
        return legacy
    return preferred


def get_status(name: str) -> dict:
    """
    读取 OpenVPN status 文件（status-version 2 格式），
    解析在线客户端列表、流量统计。

    返回 dict:
        clients: list[dict]  — 每个在线客户端的信息
        total_bytes_received: int
        total_bytes_sent: int
        connected_since: str | None  — 服务启动时间
    """
    from app.core.config import load_config

    conf_dir = str(load_config().get("openvpn_conf_dir") or "/etc/openvpn")
    status_file = resolve_status_log_path(name, openvpn_conf_dir=conf_dir)
    result = {
        "clients": [],
        "total_bytes_received": 0,
        "total_bytes_sent": 0,
        "connected_since": None,
    }

    if not status_file.exists():
        return result

    try:
        content = status_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("读取 status 文件失败 %s: %s", status_file, exc)
        return result

    # status-version 2：逗号或制表符分隔，HEADER 行标识各段
    section = None
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("HEADER"):
            parts = _split_status_v2_line(line)
            if len(parts) >= 2:
                section = parts[1]
            continue

        if section == "CLIENT_LIST" and not line.startswith("HEADER"):
            parts = _split_status_v2_line(line)
            # CLIENT_LIST 格式: Common Name, Real Address, Virtual Address,
            # Virtual IPv6, Bytes Received, Bytes Sent, Connected Since, ...
            if len(parts) >= 7 and parts[0] == "CLIENT_LIST":
                client = {
                    "common_name": parts[1],
                    "real_address": parts[2],
                    "virtual_address": parts[3],
                    "bytes_received": int(parts[5]) if parts[5].isdigit() else 0,
                    "bytes_sent": int(parts[6]) if parts[6].isdigit() else 0,
                    "connected_since": parts[7] if len(parts) > 7 else "",
                }
                result["clients"].append(client)
                result["total_bytes_received"] += client["bytes_received"]
                result["total_bytes_sent"] += client["bytes_sent"]

    return result
