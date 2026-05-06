# Design: vpn-bugfix-2026-04-03

## 1. CertService Box 类型修复

**文件**: `app/services/cert/cert_service.py`

`load_config()` 返回 `Box(default_box=True)`，访问不存在的 key 返回 `Box({})` 而非 `None`。
`_get_index_path` 直接 `Path(self._config.pki_dir)` 会 TypeError。

**修复**: 与 `_get_wrapper` 一致，先 `str(...or "").strip()` 转为字符串，空则抛 ValueError。

## 2. 用户卡片单行排版

**文件**: `app/ui/pages/users.py`, `app/ui/theme.py`

**现状**: `user-record-copy` CSS 为 `flex-direction: column`，BEM 修饰符 `--row` 无对应 CSS 规则。信息分三块（主区/侧栏/操作）。

**方案**: 
- 合并主区和侧栏为单个 `user-row-line` 容器，`display: flex; align-items: center; flex-wrap: wrap`
- checkbox / 用户名 / 时间 / SN / 启用状态 / 在线状态 / GID / 流量 / 连接信息全部在一行
- 删除 sparkline SVG（单行空间不够，且非历史数据意义不大）

## 3. 用户搜索修复

**文件**: `app/ui/pages/users.py`

**根因**: `render()` 中 `self.list_container = ui.column()` 后直接调 `_render_user_list()`，NiceGUI 中未用 `with` 包裹则元素创建在容器外。`_refresh_user_list` 清空容器并在内部重建，旧元素残留。

**修复**: 改为 `with self.list_container: self._render_user_list(...)`。

## 4. 虚拟 IP 分配

**文件**: `app/services/user/crud.py`

**现状**: `_write_ccd` 用 `group.user_count` 做偏移，删除用户后 count 递减但 IP 已写入 CCD 文件。

**方案**: 新增 `_collect_used_ips_in_group` 方法，扫描同组所有活跃用户的 CCD 文件收集已用 IP，在 `network.hosts()[1:]` 中找首个可用地址。

## 5. 组管理根组逻辑

**文件**: `app/services/group/crud.py`, `app/ui/pages/groups.py`

**方案**:
- `create()`: 如果已有组，取 `list_all()[0]` 为根组，新组子网必须 `is_subnet_of(new, root_subnet)`
- `delete()`: 去掉硬编码 `"默认用户组"` 检查，改为 "根组在有其他组时不可删除"
- 前端弹窗根据 `len(existing) == 0` 区分根组/子组提示

## 6. 防火墙新建规则

**文件**: `app/ui/pages/firewall.py`, `app/models/firewall.py`, `app/services/firewall/iptables_mgr.py`

**模型变更**:
- `FirewallRule.instance`: `Field(...)` → `Field(default="server")`
- 新增 `source_ips: Optional[list[str]]`（用户类型时的源 IP 列表）

**前端**:
- 移除 `instance_input`，自动从 config 检测
- 组类型 → `group_select` + `auto_src_switch` + `source_cidr_input`
- 用户类型 → `user_select`（multiple=True）+ `source_cidr_input`
- `dest_ip_input` placeholder 标注支持逗号分隔多个

**iptables_mgr**:
- `rebuild_rules`: 新增 `source_ips` 分支调用 `_ipset_ensure_add_multi`
- `_ipset_ensure_add_multi`: 创建 hash:net 集合写入多条 IP/32
- `_expand_rule_lines`: dest_ip 逗号分隔 → 展开为多条 iptables 行

## 7. 防火墙搜索 + 拖拽

**文件**: `app/ui/pages/firewall.py`, `app/ui/theme.py`

- 搜索从独立大面板（历史名 `fw-search-panel`）改为紧凑行：现为 `firewall-search-row` + `firewall-inline-search` + `firewall-search-shell`（**不再使用** `fw-search-panel` / `fw-filter-bar` 类名）
- `render()` 中加载 SortableJS CDN
- `_refresh_rules`: 单一归属下 DragList 即为规则列表（不再额外渲染 rule cards）

## 8. 服务日志查看

**文件**: `app/ui/pages/services.py`

- `_show_log_placeholder` → `_show_log_viewer`
- 解析 `{conf_dir}/{name}.conf` 中 `log-append` 行获取日志路径
- 回退路径: `/var/log/openvpn-{name}.log`（**已由变更 `vpn-data-perms-logs-2026-04-08` 更新**：默认 `data/logs/openvpn/{name}.log`，仍兼容旧 `/var/log`）
- 弹窗展示 `tail -200` 行，支持刷新
