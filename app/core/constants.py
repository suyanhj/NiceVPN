# -*- coding: utf-8 -*-
"""全局常量定义"""
from pathlib import Path

# 项目根目录（相对于 main.py 运行位置）
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------- OpenVPN 运行时数据（与系统配置区一致，在 /etc/openvpn）----------
OPENVPN_ETC_DIR = Path("/etc/openvpn")
OPENVPN_SERVER_CONF_DIR = OPENVPN_ETC_DIR / "server"
OPENVPN_CLIENT_CONF_DIR = OPENVPN_ETC_DIR / "client"
OPENVPN_MGMT_DIR = OPENVPN_ETC_DIR / "mgmt"
# CCD、设备绑定、守护进程日志等由 OpenVPN/nobody 或 client-connect 直接操作
CCD_DIR = OPENVPN_ETC_DIR / "ccd"
DEVICE_BINDINGS_DIR = OPENVPN_MGMT_DIR / "device_bindings"
# client-connect 脚本读取，一行：weak_log | weak_fingerprint | strict_hwaddr（weak 见 device-bind.sh 头注释）
DEVICE_BIND_MODE_FILE = OPENVPN_MGMT_DIR / "device_bind_mode"
# 客户端 .ovpn 成品（非管理端 JSON，与 OpenVPN 分发物同属系统区）
OVPN_PROFILES_DIR = OPENVPN_MGMT_DIR / "ovpn"
# 经 SSH 下发到对端主机上的客户端配置固定路径（官方 openvpn-client@client.service 默认读取）
PEER_REMOTE_CLIENT_OVPN_PATH = "/etc/openvpn/client/client.conf"
OPENVPN_LOG_ROOT = OPENVPN_ETC_DIR / "log"
OPENVPN_DAEMON_LOG_DIR = OPENVPN_LOG_ROOT
# 设备绑定审计日志：由 device-bind.sh 的 log() 追加写入；部署时 script_sync 把该路径写入脚本的 BIND_LOG
DEVICE_BIND_LOG_FILE = OPENVPN_LOG_ROOT / "openvpn-device-bind.log"

# ---------- 管理端应用数据（Python 进程读写，放项目 data/）----------
DATA_DIR = BASE_DIR / "data"
# 用户业务 JSON 由 UserService / 界面读写；.ovpn 见 OVPN_PROFILES_DIR
USERS_DIR = DATA_DIR / "users"
GROUPS_DIR = DATA_DIR / "groups"
FIREWALL_DIR = DATA_DIR / "firewall"
# 远端 filter 用户链工作副本与同步状态（与 SSH 拉取/写回配合）
REMOTE_PEER_CHAINS_DIR = DATA_DIR / "remote_peer_chains"
# 对端站点实例（组网）元数据 JSON：data/peers/<peer_id>.json
PEERS_DIR = DATA_DIR / "peers"
DOWNLOAD_LINKS_DIR = DATA_DIR / "download_links"
# API 批量创建用户时生成的 zip 包（一次性下载令牌指向此目录下的文件）
DOWNLOAD_BUNDLES_DIR = DATA_DIR / "download_bundles"
AUDIT_DIR = DATA_DIR / "audit"
LOGS_DIR = DATA_DIR / "logs"
ALERTS_FILE = DATA_DIR / "alerts.json"
CONFIG_FILE = DATA_DIR / "config.json"
# 公网 VPN 管理 API（HTTP Basic）凭据 JSON：{"username":"vpn","password":"…"}，仅首次创建
API_BASIC_CREDENTIALS_FILE = DATA_DIR / "api_basic_credentials.json"

# 备份目录
BACKUPS_DIR = BASE_DIR / "backups"

# 脚本目录
SCRIPTS_DIR = BASE_DIR / "app" / "scripts"


def ensure_openvpn_runtime_dirs() -> None:
    """创建 /etc/openvpn 下运行时目录树（Linux 部署必需）。"""
    for p in (
        OPENVPN_ETC_DIR,
        OPENVPN_SERVER_CONF_DIR,
        CCD_DIR,
        OPENVPN_MGMT_DIR,
        DEVICE_BINDINGS_DIR,
        OVPN_PROFILES_DIR,
        OPENVPN_LOG_ROOT,
        OPENVPN_DAEMON_LOG_DIR,
        OPENVPN_ETC_DIR / "scripts",
    ):
        p.mkdir(parents=True, exist_ok=True)


# EasyRSA 标准搜索路径列表（按优先级排序）
EASYRSA_SEARCH_PATHS = [
    "/opt/easy-rsa/current/easyrsa",
    "/opt/easy-rsa/easyrsa",
    "/opt/openvpn/share/easy-rsa/easyrsa",
    "/usr/share/easy-rsa/easyrsa",
    "/usr/share/easy-rsa/3/easyrsa",
    "/usr/share/easy-rsa/3.0/easyrsa",
    "/usr/local/share/easy-rsa/easyrsa",
]

# Easy-RSA 证书有效期（天），由 EasyRSAWrapper 注入 EASYRSA_* 环境变量
# 官方默认叶子证书约 825 天；CA 默认约 8250 天。此处叶子约 10 年，CA 约 12 年（须晚于已签叶子失效日）
EASYRSA_CERT_EXPIRE_DAYS = 3650
EASYRSA_CA_EXPIRE_DAYS = 4380

# OpenVPN 标准搜索路径列表（按优先级排序）
OPENVPN_SEARCH_PATHS = [
    "/opt/openvpn/sbin/openvpn",
    "/opt/openvpn/bin/openvpn",
    "/usr/local/sbin/openvpn",
    "/usr/local/bin/openvpn",
    "/usr/sbin/openvpn",
    "/usr/bin/openvpn",
]

# 红帽系源码安装目录
OPENVPN_INSTALL_PREFIX = "/opt/openvpn"
EASYRSA_INSTALL_DIR = "/opt/easy-rsa"

# OpenVPN Management Interface 默认端口起始值
MGMT_PORT_START = 7505

# server.conf 中 max-clients 默认值（与 SystemConfig.max_clients 默认一致）
OPENVPN_DEFAULT_MAX_CLIENTS = 2048

# OpenVPN 最低要求版本
OPENVPN_MIN_VERSION = "2.7.0"

# 下载链接过期时间（秒）
DOWNLOAD_LINK_EXPIRE_SECONDS = 3600

# 证书到期告警提前天数
CERT_EXPIRY_WARN_DAYS = 7

# 运行日志、OpenVPN 日志、审计 JSONL 默认保留天数（超时删除或依赖轮转 backupCount）
LOG_RETENTION_DAYS = 7

# 服务状态检查间隔（秒）
SERVICE_CHECK_INTERVAL = 30

# 默认 Web UI 端口
DEFAULT_PORT = 8880
DEFAULT_HOST = "0.0.0.0"
