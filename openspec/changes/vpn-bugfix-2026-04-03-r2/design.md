# Design: vpn-bugfix-2026-04-03-r2

## 1. 默认组 = 全局组

**文件**: `app/core/init_wizard.py`

`_create_default_group()` 原来取全局子网的第一个 /24 子网作为默认组子网。

**修复**: `default_subnet = global_subnet`，默认用户组就是全局组。

## 2. 默认规则带全局 source_cidr

**文件**: `app/core/init_wizard.py`

`_start_service()` 中创建默认防火墙规则时 `source_subnet: None`。

**修复**: `source_subnet: global_subnet`。单条 CIDR 规则直接 iptables，不需要 ipset。

## 3. 子网冲突检测跳过根组

**文件**: `app/services/group/subnet.py`

**根因**: `subnets_overlap(10.244.1.0/24, 10.244.0.0/16)` 返回 True，子组在根组范围内被误判为冲突。

**修复**: 在遍历现有组时，如果 `ip_network(group_subnet) == ip_network(global_subnet)`（即该组为根组），跳过重叠检测。子组在根组范围内是合法的预期行为，同级子组之间的重叠仍然会被检测到。

## 4. 根组 CRUD 约束

**文件**: `app/services/group/crud.py`

- `delete()`: 根组（第一个组）永远不可删除，移除"有其他组时不可删除"的条件，改为绝对禁止
- `update_subnet()`: 根组修改增加前提「不存在子组」；修改后调用 `_sync_global_subnet()` 同步：
  1. 更新 `config.global_subnet`
  2. 若 `instances` 元数据中 `subnet` 仍为旧全局 CIDR，逐项改为新 CIDR
  3. 对每个已注册实例调用 `write_server_conf()`，按新网段重写 `server` 指令（与 `config.json` 一致）
  4. 查找默认防火墙规则（`description == "初始化默认放行"`），若 `source_subnet` 为旧值则更新为新全局子网并 `_rebuild_all_rules()`

## 5. 组管理前端

**文件**: `app/ui/pages/groups.py`

- 新建弹窗：移除"第一个组是根组"提示（根组由初始化向导创建），改为"子网必须在根组范围内"
- 编辑弹窗：根组显示子组检查条件；根组修改成功后提示同步信息
- 编辑弹窗：**根组**修改 CIDR 时，前端校验不再要求「新子网必须落在当前全局子网内」（根组即全局池，否则会把合法的全局改网段误判为越界）；子组仍校验 `is_subnet_of(子网, 根组子网)`
- 卡片渲染：根组不显示删除按钮

## 6. 用户启用/停用按钮

**文件**: `app/services/user/crud.py`, `app/ui/pages/users.py`

- `UserService.toggle_status()`: 切换 active ↔ disabled
- 前端：原"启用/停用"标签 → `toggle_on/toggle_off` 图标按钮，放入 `user-actions` 区域
- 原"在线/离线"标签也移入 `user-actions` 区域

## 7. 连接时长

**文件**: `app/ui/pages/users.py`

新增 `_format_connection_duration()`: 从 `connected_since` 计算到当前的时间差，显示为 `X天X时X分` 格式。

## 8. SortableJS CDN

**文件**: `app/ui/pages/firewall.py`

CDN 从 `cdn.jsdelivr.net` 改为 `cdn.bootcdn.net`，版本升级到 1.15.6。

## 9. Group 模型乱码修复

**文件**: `app/models/group.py`

`firewall_rule_ids` 字段的 description 存在 mojibake（`关联的��火墙`），修复为正确中文。
