# 设计说明 — vpn-lan-firewall-devicebind-2026-04-09

## 1. 内网路由推送（分流）

- **配置**：`SystemConfig.push_lan_routes`，多行 IPv4 CIDR；默认 **空**，避免错误默认网段。  
- **生成**：`generate_server_conf` 在 `topology subnet` 后追加 `push "route <net> <mask>"`（由 CIDR 换算）。  
- **持久化与生效**：设置页保存 → `save_config` → `regenerate_all_server_confs()` → 需 **重启 openvpn@实例** 并让客户端重连。  
- **不推** `redirect-gateway`，保证「仅所列网段走 VPN」。

## 2. NAT（中心侧 POSTROUTING）

- **数据面**：`nat` 表 `POSTROUTING` 一条：`iptables -t nat -A POSTROUTING -s <global_subnet> -m comment --comment "ovpn-mgmt-masq inst=<实例标识>" -j MASQUERADE`。  
  **不写 `-o`（出接口）**、**不写 `-d`/反选目标**，由内核按实际出向选源地址；多网卡与固定 `SNAT --to-source` 场景均不依赖额外表单字段。  
- **与旧配置的兼容**：`SystemConfig.masquerade_out_interfaces` 在模型中 **保留为已废弃**（兼容旧 `config.json`），**实现不再读取**；历史按 `-o` 写的多网卡 MASQUERADE、或固定 SNAT，在 **完整重建**（见 §4）时由 `ovpn-mgmt-masq` 清理逻辑删除后再写当前形态。  
- **`rebuild_iptables()` / `IptablesManager.rebuild_rules`**：在 `VPN_FORWARD` 与 ipset 写入成功后调用 `_ensure_vpn_nat_masquerade`，属 **项目级恢复**（与 FORWARD 钩子、INPUT tun+、`ip_forward` 同路径）。

## 3. iptables 数据面与 INPUT

- **问题根因**：  
  - `VPN_FORWARD` 无 `FORWARD` 跳转时链为 0 references，规则不生效。  
  - 访问 **本机在 eth0 上的 IP** 解密后从 **tun** 进入 → **INPUT**，不经 `VPN_FORWARD`。  
- **措施**：  
  - `FORWARD`：`RELATED,ESTABLISHED` ACCEPT；`tun+` → `VPN_FORWARD`。  
  - `INPUT`：`-i tun+ -s <global_subnet> -j ACCEPT`（comment `ovpn-mgmt-input-tun`）。  
- **空规则集**：无任何启用的防火墙 JSON 规则时，在 `VPN_FORWARD` 链尾追加 `-s global_subnet -j ACCEPT`，避免空链回落 FORWARD 被 DROP。**存在启用规则时不追加**，以免覆盖用户链尾默认拒绝策略。

## 4. 重启一致性与「规则页」边界

- 业务规则在 `data/firewall/*.json`；内核规则重启丢失。  
- **管理端启动**（`start_scheduler`，已初始化、非 Windows）：  
  1. `sync_device_bind_mode_file`  
  2. `FirewallRuleService().rebuild_iptables()`（完整：`VPN_FORWARD` + ipset + FORWARD/INPUT 钩子 + `ip_forward` + 中心 NAT）  
  3. `regenerate_all_server_confs()`  
- **防火墙规则管理页**（`/firewall` 及对等与 `FirewallRuleService` 的 CRUD、排序、启停、备份恢复中心 JSON）：仅 **`refresh_vpn_forward_only()`** —— 只刷新 **`VPN_FORWARD`** 链（及规则引用的 ipset），**不** 改写 `INPUT`、`FORWARD` 固定钩子、`nat` 表；与 UI 安全边界一致。  
- **初始化向导**「启动服务」成功后：在非 Windows 上同样可调用 **`rebuild_iptables()`**（与启动调度器一致），使钩子与 NAT 与磁盘规则一次对齐。  
- 仅跑 OpenVPN、不跑管理端时，需运维自行 **iptables-persistent** 或等价方案（本变更不实现）。

## 4.1 项目停止与清理（约定）

- **停止管理进程**或**停止单个 OpenVPN 实例** 时 **不自动删除** 本机 iptables：OpenVPN 可能仍在运行，清理会导致隧道与 LAN 访问异常。  
- **显式维护**（如 `remove_ovpn_mgmt_iptables_hooks`）仅作排障/卸载场景人工使用，不挂在默认停止路径上。

## 5. 设备绑定三模式

| 模式 | 行为 |
|------|------|
| `weak_log` | 只写 `openvpn-device-bind.log`，不拒绝，不写绑定 JSON |
| `weak_fingerprint` | 全体 `IV_HWADDR`；无 HW 时 iOS/Mac/Win 用 `UV_UUID`，安卓 `IV_PLAT\|IV_PLAT_VER`，其它 `IV_PLAT_VER\|IV_GUI_VER`；全无则仅证书放行 |
| `strict_hwaddr` | 无 `IV_HWADDR` → `exit 1`；有则按指纹 JSON 比对 |

- **运行时文件**：`/etc/openvpn/mgmt/device_bind_mode` 单行 ASCII，由 `device_bind_policy.sync_device_bind_mode_file` 写入；与 `config.json` 中 `device_bind_mode` 一致。  
- **脚本**：`device-bind.sh` 内 `OVPN_ETC=/etc/openvpn`；绑定文件用 **python3** 写 JSON，避免特殊字符破坏格式。  
- **弱指纹局限**：`PLAT|GUI` 在同机型、同应用版本上易重复，无法区分多机。

## 6. duplicate-cn

- 曾实现 `allow_duplicate_cn` 与 `server.conf` 中 `duplicate-cn`，后与 **CCD 固定 IP**、运维复杂度权衡，**已从代码移除**；OpenSpec 记录为「不采用」。

## 7. 与历史 OpenSpec 的关系

- `vpn-data-perms-logs-2026-04-08` 等变更中部分路径描述（如 `data/` 下 CCD）可能已被后续 **整树迁至 `/etc/openvpn`** 的实现对齐；**以当前 `app/core/constants.py` 为准**。  
- 本变更 **不修改** 旧 change 正文，仅在 `vpn-data-perms-logs-2026-04-08/proposal.md` 顶部增加 **迁移提示**；本条为 2026-04-09 能力基线。

## 8. 路径布局（权威速查）

| 用途 | 路径 |
|------|------|
| CCD | `CCD_DIR` → `/etc/openvpn/ccd` |
| 设备绑定 JSON | `DEVICE_BINDINGS_DIR` → `/etc/openvpn/mgmt/device_bindings` |
| 设备绑定策略文件 | `DEVICE_BIND_MODE_FILE` → `/etc/openvpn/mgmt/device_bind_mode` |
| 用户 `.ovpn` 成品 | `OVPN_PROFILES_DIR` → `/etc/openvpn/mgmt/ovpn` |
| 用户业务 JSON | `USERS_DIR` → `data/users` |
| OpenVPN 守护 status/log | `OPENVPN_DAEMON_LOG_DIR` → `/etc/openvpn/log/openvpn/` |
| device-bind 日志 | `DEVICE_BIND_LOG_FILE` → `/etc/openvpn/log/openvpn-device-bind.log` |
| 地址池持久 | `OPENVPN_ETC_DIR/ipp-{instance}.txt` |

- `ovpn_gen` / 批量下载读写的 `.ovpn` 路径与上表一致。

## 9. 日志轮转与过期删除

- **管理端文件日志**：`data/logs/app.log` 等，`TimedRotatingFileHandler`，`backupCount=LOG_RETENTION_DAYS`。  
- **过期删除**：`cleanup_expired_logs()` 按 mtime/日期清理超保留期的日志与审计文件；调度见 `scheduler.start_scheduler`。  
- 与 **内核 iptables** 重建（§4）独立：前者清磁盘日志，后者恢复 netfilter。
