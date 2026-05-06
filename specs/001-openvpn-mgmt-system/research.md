# Phase 0 研究报告：可视化 OpenVPN 管理系统

**功能分支**: `001-openvpn-mgmt-system`
**研究日期**: 2026-03-27（2026-03-27 更新：补充 OpenVPN 2.7.0 特性研究）
**输入来源**: spec.md 技术背景中的未知项 + 官方 changelog + 用户补充指令

---

## 1. OpenVPN 2.7.0 设备绑定机制（双重绑定）

**版本**: 更新于 2026-03-27，升级至 2.7.0 双重绑定方案

### 决策
采用 **tls-crypt-v2（第一层）+ client-connect 脚本硬件指纹（第二层）** 双重绑定，
所有配置通过 `.conf` 配置文件管理，不使用运行时命令行参数。

### 第一层：tls-crypt-v2 密钥绑定（2.7.0 内置，无需自定义代码）

OpenVPN 2.7.0 中 `tls-crypt-v2` 是标配安全特性。每个客户端在系统为其生成 `.ovpn` 时，
同时通过 EasyRSA 生成一个唯一的 `tls-crypt-v2` 客户端密钥，内联嵌入 `.ovpn` 文件：

```ini
# server.conf
tls-crypt-v2 /etc/openvpn/server-tc2.key
tls-crypt-v2-max-age 3650        # 2.7.0 新增：内置密钥过期控制（天数），过期自动拒绝
script-security 2
client-connect /etc/openvpn/scripts/device-bind.sh
```

```ini
# client.ovpn（内联块，无外部文件引用）
<tls-crypt-v2>
-----BEGIN OpenVPN tls-crypt-v2 client key-----
... （唯一客户端密钥）
-----END OpenVPN tls-crypt-v2 client key-----
</tls-crypt-v2>
```

**效果**: 即使攻击者知道证书 CN，若无对应的 tls-crypt-v2 密钥，连接请求在 TLS 握手前即被拒绝。
如果 `.ovpn` 被复制，密钥也随之复制 — 第二层硬件指纹解决此问题。

**`tls-crypt-v2-max-age`（2.7.0 新增）**: 内置密钥年龄校验指令，超过指定天数的密钥被服务端拒绝，
实现密钥轮换强制执行，**无需编写自定义密钥过期脚本**。

### 第二层：client-connect 脚本硬件指纹校验（仍需自定义脚本）

OpenVPN 的 `client-connect` 脚本仍是硬件指纹验证的必要手段。
服务端通过环境变量获取客户端信息：
- `common_name` — 客户端证书 CN
- `untrusted_ip` — 客户端公网 IP
- `username` — 通过 `auth-user-pass` 传递的指纹（若启用密码认证）

硬件指纹传递方式：客户端通过 OpenVPN `auth-user-pass` 机制，
将 `username=<cert_cn>` + `password=<hardware_fingerprint_hash>` 传递给服务端；
服务端在 `client-connect` 脚本中从 `$username` / `$password` 环境变量读取并比对。

硬件指纹方案（优先级排序）：
1. **`machine-id`**（Linux：`/etc/machine-id`；Windows：注册表 MachineGuid）— 稳定，推荐
2. **网卡 MAC 地址**（非随机化网卡，如有线以太网）— 备用
3. **设备序列号 / UUID**（移动端适用）— 降级方案

### 最终结论
- **第一层**（tls-crypt-v2）：配置文件内置，无需自定义代码，2.7.0 标配
- **第二层**（client-connect 脚本）：仍需编写，但逻辑极简（读取环境变量 → 比对 JSON 文件 → 返回 0 或 1）
- 两层均通过 `.conf` 配置文件指定，符合"配置文件优先"原则

---

## 2. OpenVPN 2.7.0 配置文件优先原则

**版本**: 新增于 2026-03-27

### 决策
所有 OpenVPN 配置通过 `.conf` 文件管理，禁止在启动命令中附加业务参数。
系统生成配置文件，通过 `openvpn --config <file>` 单一参数启动。

### 服务端 server.conf 推荐指令集（2.7.0）

```ini
# ======== 基础网络 ========
port 1194
proto udp
dev tun
server 10.8.0.0 255.255.0.0         # 自动配置 mode/tls-server/ifconfig-pool
topology subnet                      # 推荐，避免 net30 浪费 IP

# ======== TLS 安全（内联块，无外部路径引用）========
<ca>
... CA 证书内容 ...
</ca>
<cert>
... 服务器证书内容 ...
</cert>
<key>
... 服务器私钥内容 ...
</key>
<dh>
... DH 参数内容 ...
</dh>

tls-crypt-v2 /etc/openvpn/server-tc2.key  # 服务端 tls-crypt-v2 主密钥（此项不内联）
tls-crypt-v2-max-age 3650                  # 2.7.0 新增：内置密钥有效期（天）
tls-version-min 1.2                        # 最低 TLS 1.2（2.6+ 已默认）
cipher AES-256-GCM
auth SHA512

# ======== CRL & 证书验证 ========
crl-verify /etc/openvpn/pki/crl.pem       # 证书吊销列表（EasyRSA 生成）
verify-client-cert require

# ======== 客户端连接脚本 ========
script-security 2
client-connect /etc/openvpn/scripts/device-bind.sh
client-disconnect /etc/openvpn/scripts/device-disconnect.sh

# ======== 客户端专属配置目录（CCD）========
client-config-dir /etc/openvpn/ccd       # 每用户一个文件，含 ifconfig-push（固定 IP）

# ======== 状态与日志（替代纯 Management Interface 轮询）========
status /var/log/openvpn-status.log 30     # 每 30 秒写入状态文件（在线用户、流量）
status-version 2                           # 结构化状态格式，易于解析
log-append /var/log/openvpn.log
verb 3

# ======== 管理接口（仅用于即时操作，如踢出用户）========
management 127.0.0.1 7505

# ======== DoS 防护（内置，无需自定义脚本）========
connect-freq 10 60                         # 最多每 60 秒 10 次新连接

# ======== 运行权限降低 ========
user nobody
group nobody                               # Linux 推荐
persist-key                                # 降权后需保留密钥
persist-tun
```

### 客户端 .ovpn 推荐内联格式

```ini
client
dev tun
proto udp
remote <SERVER_IP> 1194
nobind
resolv-retry infinite

cipher AES-256-GCM
auth SHA512
tls-version-min 1.2
verb 3

# 所有证书/密钥内联，无外部文件引用
<ca>
... CA 证书 ...
</ca>
<cert>
... 用户证书 ...
</cert>
<key>
... 用户私钥 ...
</key>
<tls-crypt-v2>
... 用户唯一 tls-crypt-v2 密钥 ...
</tls-crypt-v2>
```

### 2.7.0 新增可直接使用的内置特性（减少自定义代码）

| 特性 | 指令 | 替代自定义代码 |
|---|---|---|
| 客户端密钥过期控制 | `tls-crypt-v2-max-age` | 自定义密钥轮换过期脚本 |
| 状态文件定期写入 | `status <file> 30` + `status-version 2` | 持续轮询 Management Interface 的后台线程 |
| 连接频率限制 | `connect-freq 10 60` | 自定义 DoS 防护逻辑 |
| 固定 IP 分配 | `client-config-dir` CCD 文件 | `client-connect` 脚本中的动态 IP 分配 |
| 证书吊销检查 | `crl-verify` | 在 `client-connect` 脚本中手动检查 CRL |
| TLS 证书指纹校验 | `verify-hash` | `--tls-verify` 脚本 |
| 多地址监听 | 2.7.0 多 socket 支持 | 多进程/多实例变通方案 |
| TLS 1.3 支持 | 2.7.0 内置（配合 OpenSSL 3.x） | — |

---

## 3. OpenVPN 跨发行版检测与自动安装

### 决策
安装目标版本为 **OpenVPN 2.7.0+**。按发行版优先顺序：

```
检测：which openvpn 并通过 openvpn --version 验证版本号 ≥ 2.7.0
安装策略：
  Ubuntu/Debian → 从 OpenVPN 官方 apt 仓库安装（非发行版自带的旧版本）：
    curl -fsSL https://packages.openvpn.net/packages-repo.gpg | gpg --dearmor > /etc/apt/trusted.gpg.d/openvpn.gpg
    echo "deb https://packages.openvpn.net/openvpn3/debian ..." > /etc/apt/sources.list.d/openvpn.list
    apt install openvpn
  CentOS/RHEL 8+ → dnf install epel-release && dnf install openvpn
  CentOS 7 → yum install epel-release && yum install openvpn
发行版识别：读取 /etc/os-release 的 ID 和 VERSION_ID 字段
```

**版本检测逻辑**: `openvpn --version` 输出第一行，使用正则提取版本号，
与最低要求 `2.7.0` 做 `packaging.version` 比较；低于要求则触发升级或提示。

EasyRSA 标准路径（按优先级搜索）：
- `/usr/share/easy-rsa/easyrsa`
- `/usr/share/easy-rsa/3/easyrsa`
- `$(openvpn --version | 提取安装前缀)/share/easy-rsa/easyrsa`

---

## 4. NiceGUI + FastAPI 集成模式

### 决策
NiceGUI 1.4+ 原生在同一 uvicorn 实例内托管 FastAPI，`nicegui.app` 即为 FastAPI 实例：

```python
# main.py 核心模式
from nicegui import app, ui
from api.download import router as download_router

app.include_router(download_router, prefix="/download")
ui.run(host="0.0.0.0", port=8080, title="OpenVPN 管理系统")
```

### 拖拽排序实现
NiceGUI 的 `ui.list` + Sortable.js 集成，拖拽结束回调中更新优先级并触发 `iptables-restore`。

---

## 5. 一次性下载链接实现

### 决策
`GET /download/{token}` 端点，令牌 = `secrets.token_urlsafe(32)`，
状态持久化到 `data/download_links/{token}.json`（含 `expires_at`、`used` 字段）。
消费原子性：先将 `used=true` 写磁盘，再返回文件流。

---

## 6. OpenVPN 运行状态获取（双通道）

### 决策
**主通道（定期）**：读取 `status` 文件（`status-version 2` 格式），适用于仪表盘定期刷新。
**辅通道（按需）**：Management Interface TCP socket（`127.0.0.1:7505`），
用于需要即时操作的场景（踢出在线用户、发送 `SIGTERM`）。

服务运行状态检测：`systemctl is-active openvpn@<instance>`；
Management Interface 无响应时视为服务异常，触发自动重启。

多实例端口分配：`7505`、`7506`…（在 `server.conf` 中按实例编号配置）。

---

## 7. iptables 规则原子替换

### 决策
`iptables-restore` 临时文件原子替换，规则按优先级排序后一次性生效。
ipset 先于 iptables 更新（先 ipset swap，再 iptables-restore）。

---

## 8. 审计日志防篡改

### 决策
JSONL 追加写入 + SHA-256 哈希链 + 文件权限控制（仅 root 可写）。
日志按天分割：`data/audit/audit-YYYY-MM-DD.jsonl`。

---

## 9. 依赖版本锁定策略

| 包 | 版本约束 | 理由 |
|---|---|---|
| nicegui | >=1.4.0,<2.0 | 1.4 引入稳定 FastAPI 集成 |
| fastapi | >=0.110.0,<1.0 | 支持 Pydantic v2 |
| uvicorn[standard] | >=0.27.0 | WebSocket 支持（NiceGUI 依赖） |
| pydantic | >=2.0.0,<3.0 | 模型校验 |
| python-box | >=7.0 | 配置读取 |
| requests | >=2.31.0 | 钉钉 Webhook 推送 |
| schedule | >=1.2.0 | 证书到期定时检查 |
| packaging | >=23.0 | OpenVPN 版本号比较 |

---

## 研究结论汇总

| 未知项 | 决策 | 置信度 |
|---|---|---|
| 设备绑定机制 | tls-crypt-v2 密钥绑定（第一层）+ client-connect 脚本硬件指纹（第二层） | 高 |
| OpenVPN 版本 | 锁定 2.7.0+，从官方仓库安装 | 高 |
| OpenVPN 跨发行版安装 | 读取 `/etc/os-release` + 包管理器 + 官方仓库 | 高 |
| 配置管理方式 | 全部 `.conf` 配置文件，内联证书块，禁止命令行参数 | 高 |
| 2.7.0 新特性利用 | `tls-crypt-v2-max-age`、`connect-freq`、`status` 文件、CCD 目录 | 高 |
| NiceGUI + FastAPI 集成 | `nicegui.app.include_router()` 直接挂载 | 高 |
| 一次性下载链接 | `secrets.token_urlsafe` + JSON 令牌状态文件 | 高 |
| 实时运行状态获取 | status 文件（定期）+ Management Interface（按需） | 高 |
| iptables 原子替换 | `iptables-restore` 临时文件方案 | 高 |
| 审计日志防篡改 | JSONL 追加 + SHA-256 哈希链 | 中 |
