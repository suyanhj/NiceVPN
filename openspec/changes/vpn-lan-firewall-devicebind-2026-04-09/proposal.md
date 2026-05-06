# vpn-lan-firewall-devicebind-2026-04-09

## 概述

将 **客户端访问服务端局域网（push 路由）**、**iptables/NAT 与重启一致性**、**设备绑定策略可配置**、**管理端启动时同步运行时文件** 等行为纳入 OpenSpec，与 2026-04 已实现代码对齐；并记录 **duplicate-cn 曾评估后未采用** 的产品结论。

## 背景与动机

- 分流场景：仅内网走 VPN，上网不走 VPN → 需在 `server.conf` 中 `push "route …"`，由管理员按实际局域网配置，而非写死错误网段。
- 生产上曾出现：`FORWARD` 默认 DROP 且未跳转 `VPN_FORWARD`、访问本机 eth0 IP 走 **INPUT** 而非 `VPN_FORWARD`、NAT 仅默认网卡等，导致客户端无法访问 LAN 或服务端本机地址。
- 重启后内核 iptables 清空，需从磁盘规则与配置 **自动重建**。
- 安卓 OpenVPN 2.x/3.x 对 `IV_HWADDR`、`IV_AUTO_SESS` 行为差异大，需 **可切换** 的设备绑定策略；脚本部署曾遗留 `__OPENVPN_ETC_DIR__` 未替换导致路径错误。
- **duplicate-cn** 与 CCD 固定 `ifconfig-push` 易冲突，且设备绑定已限制异机，产品决定 **不启用 duplicate-cn**（代码中不暴露该开关）。

## 范围（已实现）

1. **系统配置模型**（`app/models/config.py`）  
   - `push_lan_routes`  
   - `masquerade_out_interfaces`：**已废弃**，仅兼容旧 JSON，实现不读取  
   - `device_bind_mode`：`weak_log` | `weak_fingerprint` | `strict_hwaddr`  

2. **OpenVPN 服务端配置生成**（`app/services/openvpn/instance.py`）  
   - `push` 内网路由块、 `write_server_conf` 合并系统配置中的 `push_lan_routes`  
   - `regenerate_all_server_confs()`；实例 `mgmt_port` 按注册名排序递增  

3. **系统设置 UI**（`app/ui/pages/settings.py`）  
   - 内网路由、保存时重写 `server.conf` + **`FirewallRuleService.refresh_vpn_forward_only()`**（仅 `VPN_FORWARD`，不碰 NAT）  
   - 设备绑定策略保存 + `sync_device_bind_mode_file`  

4. **防火墙**（`app/services/firewall/iptables_mgr.py`、`rule_service.py`）  
   - `FORWARD` 钩子：RELATED/ESTABLISHED、`tun+` → `VPN_FORWARD`  
   - `INPUT`：`tun+` + 源为 `global_subnet` → ACCEPT（本机 eth0 等地址的入站）  
   - **中心 NAT**：`POSTROUTING` 对源 `-s global_subnet` → `MASQUERADE`，注释 `ovpn-mgmt-masq`；**无 `-o`、无 `-d`**  
   - `ip_forward` 运行时开启（完整重建路径）  
   - 无启用 JSON 规则时 `VPN_FORWARD` 链尾默认 ACCEPT（仅 `global_subnet`）  
   - **项目级**：`FirewallRuleService.rebuild_iptables()` → `IptablesManager.rebuild_rules`（含 hooks + NAT）  
   - **规则页 / 中心 JSON 变更**：`refresh_vpn_forward_only()` → `IptablesManager.refresh_vpn_forward_only`（仅 `VPN_FORWARD` + 所需 ipset）  

5. **组子网同步**（`app/services/group/crud.py`）  
   - 重写 `server.conf` 时携带 `push_lan_routes`、`mgmt_port`  

6. **初始化向导**（`app/core/init_wizard.py`）  
   - `conf_config` 含 `push_lan_routes` 等；部署后 `sync_device_bind_mode_file`  
   - 「启动服务」成功后在非 Windows 上 **`FirewallRuleService.rebuild_iptables()`**，与调度器启动路径一致（钩子 + NAT）

7. **定时/启动**（`app/core/scheduler.py`）  
   - 已初始化且非 Windows：`sync_device_bind_mode_file`、`rebuild_iptables`、`regenerate_all_server_confs`  

8. **常量**（`app/core/constants.py`）  
   - `DEVICE_BIND_MODE_FILE`（`/etc/openvpn/mgmt/device_bind_mode`）  

9. **设备绑定策略同步**（`app/services/user/device_bind_policy.py`）  

10. **client-connect 脚本**（`app/scripts/device-bind.sh`）  
    - `OVPN_ETC=/etc/openvpn`（避免仅拷贝未替换占位符）  
    - 三模式逻辑；弱指纹：全体 `IV_HWADDR`；无 HW 时 iOS/Mac/Win 用 `UV_UUID`，安卓 `IV_PLAT|IV_PLAT_VER`，其它 `IV_PLAT_VER|IV_GUI_VER`  
    - 绑定 JSON 使用 **python3** 写入  

11. **运行时目录与数据分界**（`app/core/constants.py`、`ensure_openvpn_runtime_dirs`）  
    - OpenVPN 侧：`/etc/openvpn/ccd`、`mgmt/device_bindings`、`mgmt/ovpn`（`.ovpn` 成品）、`log/openvpn/`（守护进程 status/log）、`log/openvpn-device-bind.log`、`scripts/`。  
    - 管理端业务 JSON：`data/users/` 等；**用户 `.ovpn` 不在 `data/users`**，而在 `mgmt/ovpn`。  

12. **日志保留与清理**（与路由/防火墙同期基线，本条一并索引）  
    - `LOG_RETENTION_DAYS`（默认 7）；`logging_setup.TimedRotatingFileHandler`（按天 UTC、`backupCount` 对齐）。  
    - `app/utils/log_cleanup.py`：过期 `data/logs`、OpenVPN 日志目录、审计 JSONL 等；`scheduler` 每日 03:30 + **启动时**执行。  

13. **server.conf 其它生成项**（未单独开 change 的实现）  
    - `ifconfig-pool-persist`：`{OPENVPN_ETC_DIR}/ipp-{实例名}.txt`（与 CCD 固定 IP 并存；ipp 常空见运维说明）。  
    - 守护进程 `status` / `log-append` 指向 `OPENVPN_DAEMON_LOG_DIR`（`/etc/openvpn/log/openvpn/`）。  

## 非目标 / 明确不做

- **duplicate-cn**：不加入产品配置（曾与 CCD 固定 IP、运维复杂度权衡后移除相关代码）。  
- **自动推断用户局域网 push**：默认 `push_lan_routes` 为空，由管理员在设置页填写。  
- **sysctl 永久 ip_forward**：仍依赖运行时写入；永久化由运维在发行版配置。

## 运维说明（摘录）

- `ifconfig-pool-persist`：CCD `ifconfig-push` 固定 IP 时 **ipp 文件可为空**，不视为异常。  
- 用户被禁用时 CCD 内 **`disable`** 会导致 `AUTH_FAILED`（与设备绑定无关）。

## 与旧 OpenSpec 的交叉引用

- **`vpn-data-perms-logs-2026-04-08`**：其中「CCD/绑定在 `data/`」等描述已被 **`/etc/openvpn` 整树**替代；以 **`constants.py` + 本条** 为权威。旧文保留作历史，实施时勿照抄旧路径。  
- 其它 `vpn-bugfix-2026-04*`：防火墙 ipset/iptables 行为与本条 **FORWARD/INPUT/NAT** 叠加；冲突时以 **当前 `iptables_mgr.py`** 为准。
- **`vpn-pki-jsonlock-import-2026-04-09`**：删除用户时的 **PKI 磁盘清理**、**`read_json` 全目录扫描与 `*.json.lock` 残留根因及修复**、**批量导入上传（NiceGUI 3 `e.file`）**；与设备绑定目录相关时与本条配合阅读。
