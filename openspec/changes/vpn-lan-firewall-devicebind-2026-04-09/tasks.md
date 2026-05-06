# 任务清单 — vpn-lan-firewall-devicebind-2026-04-09

> 状态：**已完成**（与仓库当前实现一致，供归档与审计）

## 配置与模型

- [x] `SystemConfig`：`push_lan_routes`、`masquerade_out_interfaces`（**已废弃，仅兼容 JSON**）、`device_bind_mode`  
- [x] `allow_duplicate_cn` 评估后 **不采用**，相关代码已移除  

## OpenVPN 生成与同步

- [x] `generate_server_conf`：`push` 内网路由块；`write_server_conf` 合并 `push_lan_routes`  
- [x] `regenerate_all_server_confs`：`mgmt_port`、各实例 `server.conf`  
- [x] `init_wizard` / `group/crud`：`conf_config` 字段对齐  

## 设置页

- [x] 内网路由、保存 → 重写 `server.conf` + **`refresh_vpn_forward_only()`**（不整表重建 NAT）  
- [x] 设备绑定策略保存 → `sync_device_bind_mode_file`  

## 防火墙

- [x] `FORWARD` 钩子、`INPUT tun+` 放行、**中心 `POSTROUTING` MASQUERADE（`-s global_subnet`，无 `-o`/`-d`）**、`ip_forward`（均经 **`rebuild_iptables()`**）  
- [x] 规则管理页路径：仅 **`refresh_vpn_forward_only()`** 刷新 `VPN_FORWARD` + 规则引用 ipset  
- [x] 无启用规则时 `VPN_FORWARD` 默认 ACCEPT  
- [x] `FirewallRuleService.rebuild_iptables()`  

## 启动

- [x] `scheduler`：`device_bind_mode` 同步 + `rebuild_iptables` + `regenerate_all_server_confs`  

## 脚本与常量

- [x] `constants.DEVICE_BIND_MODE_FILE`  
- [x] `constants`：`OPENVPN_*`、`OVPN_PROFILES_DIR`、`USERS_DIR` 分界、`ensure_openvpn_runtime_dirs`  
- [x] `device_bind_policy.py`  
- [x] `device-bind.sh`：三模式、弱指纹分支、`OVPN_ETC=/etc/openvpn`、python3 写 JSON  

## 日志与 .ovpn 路径

- [x] `LOG_RETENTION_DAYS`、`logging_setup` 按天轮转  
- [x] `log_cleanup.cleanup_expired_logs` + `scheduler` 定时与启动执行  
- [x] `ovpn_gen` / `bulk_download` → `OVPN_PROFILES_DIR`  

## 文档 / OpenSpec

- [x] 本 change：`proposal.md`、`design.md`、`tasks.md`、`.openspec.yaml`  
- [x] 旧 change `vpn-data-perms-logs-2026-04-08` proposal 顶部迁移提示  

## 部署检查（人工）

- [x] 服务器上 `device-bind.sh` 与仓库一致；`/etc/openvpn/mgmt/device_bind_mode` 存在且与 UI 一致  
- [x] 升级后执行一次设置保存或重启管理端以触发 `server.conf` / iptables 同步  
