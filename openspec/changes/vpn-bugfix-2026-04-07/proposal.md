# vpn-bugfix-2026-04-07

## 概述

本轮修复涵盖客户端配置优化、前端暗色主题修复、防火墙 ipset/优先级全局化改造、批量操作以及证书批量续签/吊销功能。

## 修复范围

### 1. 客户端 .ovpn 配置优化
- 参考 c2.conf 实际使用配置，为生成的 .ovpn 文件添加关键参数
- 新增：`persist-key`、`key-direction 1`、`tun-mtu 1400`、`mssfix 1360`、`reneg-sec 604800`、`push-peer-info`
- 调整：`auth SHA256`（与服务端对齐）
- **移除 `auth-user-pass` 指令**：服务端未启用用户名/密码校验时，保留该指令会导致客户端仍弹密码框（与 nopass 证书预期不符）

### 2. 前端暗色主题修复
- 弹窗 (dialog)、下拉菜单 (q-menu)、选择器 (q-select) 出现白色背景
- 输入框、textarea、chip、toggle、btn-toggle 等组件需要暗色主题覆盖
- 恢复了丢失的 theme.py 文件并添加全部暗色覆盖 CSS

### 3. 用户管理
- 离线状态：从 div 标签改为正式状态标签，在线/离线状态始终显示
- 踢下线按钮：仅在线时显示，功能独立于启用/停用
- 停用日志修复：`_kill_client_session` 日志从"踢下线"改为"断开连接"
- 单用户「下载 .ovpn」、批量打包下载 zip 文件名带时间戳；`bulk_download` 与磁盘上 `.ovpn` 路径一致
- 「编辑 .ovpn 配置」：弹窗编辑并写回用户对应 `.ovpn` 文件

### 4. 防火墙
- 多用户源IP：选择多个用户时，从 CCD 文件读取虚拟 IP 作为源 IP，使用 ipset 管理
- 目标多IP：多个目标 IP 时使用 ipset（性能优化），单个时直接 iptables -d
- 优先级全局化：去除 per-owner 独立维护，改为全局唯一优先级校验
- ipset 命名：`_rule_ipset_name` 支持 src/dst 方向参数
- 批量删除：新增批量选中+删除功能

### 5. 证书管理
- 新增批量续签按钮
- 新增批量吊销按钮
- 每行证书增加复选框用于批量选择
