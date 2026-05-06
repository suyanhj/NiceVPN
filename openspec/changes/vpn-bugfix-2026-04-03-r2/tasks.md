# Tasks: vpn-bugfix-2026-04-03-r2

## 组管理

- [x] 默认组子网改为全局子网（`init_wizard._create_default_group`）
- [x] 子网冲突检测跳过根组（`subnet.check_subnet_conflict` 中根组不参与重叠检测）
- [x] 根组永远不可删除（`GroupService.delete` 绝对禁止）
- [x] 根组修改增加子组检查 + `_sync_global_subnet`：更新 `global_subnet`、`instances[].subnet`、`write_server_conf` 重写、默认防火墙规则 `source_subnet` 与全量重建
- [x] 组编辑弹窗：根组改 CIDR 时跳过「必须在全局子网内」的前端校验（仅格式校验）
- [x] 组管理前端：新建提示改为子组提示、编辑弹窗显示根组修改条件、根组不显示删除按钮

## 用户管理

- [x] `UserService.toggle_status()` 方法：切换 active ↔ disabled
- [x] 启用/停用从标签改为 `toggle_on/toggle_off` 操作按钮
- [x] 在线/离线移入操作区（`user-actions`）
- [x] 新增连接时长显示（`_format_connection_duration` 计算时间差）

## 防火墙

- [x] SortableJS CDN 从 jsdelivr 换为 bootcdn（1.15.6）
- [x] 初始化默认规则 `source_subnet` 从 None 改为 `global_subnet`
- [x] 根组子网修改后自动同步默认防火墙规则的 `source_subnet`

## 其他

- [x] 修复 Group 模型 `firewall_rule_ids` 字段 description 乱码
