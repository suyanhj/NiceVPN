# 任务列表

- [x] 客户端 .ovpn 配置优化：添加 persist-key、key-direction、tun-mtu、mssfix、reneg-sec、push-peer-info 等参数
- [x] 客户端 .ovpn：移除 `auth-user-pass`，避免误弹密码输入
- [x] 用户管理：单用户下载 .ovpn、批量 zip 时间戳命名、bulk_download 路径与 `ovpn_gen` 一致
- [x] 用户管理：编辑 .ovpn 配置（读写到磁盘）
- [x] 前端暗色主题修复：为 q-dialog、q-menu、q-select、q-field 等 Quasar 组件添加暗色背景覆盖
- [x] 恢复 theme.py 并补充 mgmt-* 通用管理页面组件 CSS + flex 布局修复
- [x] 用户管理：离线状态改为始终显示的状态标签（在线/离线），踢下线按钮仅在线时显示
- [x] 用户管理：修复停用操作日志文案（"踢下线" → "断开连接"）
- [x] 防火墙：多用户选择时从 CCD 文件读取虚拟 IP 作为源 IP，使用 ipset 管理
- [x] 防火墙：多个目标 IP 时使用 ipset 优化性能
- [x] 防火墙：优先级从 per-owner 改为全局唯一校验
- [x] 防火墙：_rule_ipset_name 支持 src/dst 方向参数
- [x] 防火墙：新增批量删除功能（复选框 + 批量删除按钮）
- [x] 证书管理：新增批量续签按钮
- [x] 证书管理：新增批量吊销按钮
- [x] 证书管理：每行证书增加复选框用于批量选择
