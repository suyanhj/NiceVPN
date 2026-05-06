# vpn-bugfix-2026-04-03-r3 — 设计

## 1. 防火墙页面统一渲染

### 变更文件
- `app/ui/pages/firewall.py`

### 设计
- 移除 `DragList` 导入和调用
- `_render_rule_card(rule, show_owner=False)` 统一渲染：
  - 每个卡片加 `data-rule-id` 属性
  - filtered 模式（`show_owner=False`）显示拖拽手柄，类名现为 **`firewall-rule-drag`**（历史 `fw-drag-handle`）
  - `firewall-rule-line` 移入 `firewall-rule-card` div 内部
- filtered 模式给列表容器加 `id="firewall-rule-list"`，通过 `ui.run_javascript` 初始化 SortableJS
- `ui.on("firewall_reorder")` 监听拖拽事件

## 2. `iptables_mgr`：链、ipset 与 `filter` 表回滚

### 变更文件
- `app/services/firewall/iptables_mgr.py`

### 2.1 链与 ipset 顺序

- 新增 `_flush_chain()`：对 **`VPN_FORWARD`** 执行 `iptables -F`（先解除链对旧 ipset 的引用，再 `ipset` 才能安全销毁本模块管理的集合）。
- `rebuild_rules`（在**快照成功**之后，见下节）主路径顺序：**`_flush_chain()` → `_cleanup_managed_ipsets()` → 按合并结果创建/填充 ipset → 生成 `iptables-restore` 用规则文本 → `iptables-restore --noflush` 写入** → `_ensure_forward_hooks` / `_ensure_input_from_vpn` / `_ensure_ipv4_forward_enabled` / `_ensure_vpn_nat_masquerade` 等。
- **中心 NAT（`_ensure_vpn_nat_masquerade`）**：`nat` `POSTROUTING`，`-s <global_subnet> -j MASQUERADE`，注释 `ovpn-mgmt-masq inst=<id>`；无 `-o`、无 `-d`；与 **`refresh_vpn_forward_only`**（仅刷 `VPN_FORWARD`）**互斥职责**，后者不调用本函数。
- **`refresh_vpn_forward_only`**：在合并 ipset 就绪后 **仅** `flush` + `iptables-restore` **`VPN_FORWARD`**；失败回滚 **仅** 该链的 `-S` 规则快照，不保存/恢复整张 `filter` 表。

### 2.2 `filter` 全表快照与失败回滚（`IptablesManager.rebuild_rules`）

与源码 **一一对应** 的约定如下（以 `app/services/firewall/iptables_mgr.py` 为准）。

- **Windows**（`os.name == "nt"`）：`rebuild_rules` 直接 `return True`，不调用 `iptables` / `ipset`。
- **非 Windows，进入任何破坏性步骤之前**：
  - 先 `_iptables_save_filter_t_bytes()`：子进程 `iptables-save -t filter`（`timeout=30`），**失败**（异常或返回非零）则 **error 日志、return False、不对内核做 flush/cleanup**。
  - **成功**后内存中持有 **完整 `filter` 表**字节流 `saved`，再执行 `_flush_chain()` 与 `_cleanup_managed_ipsets()` 及后续步骤。
- **任一中途失败**时走内部 `_kernel_rollback_and_fail(reason)`：
  - **error** 记录 `iptables 重建失败: {reason}，正用快照恢复 filter 表`。
  - 用 **`iptables-restore` + stdin=快照字节** 恢复全表：`_iptables_restore_filter_t_bytes(saved)`（`timeout=60`）。成功则 return False 结束；**若回滚也失败**则 **critical**（`从快照恢复 filter 表也失败，请人工检查 iptables`）。
- **会触发上述回滚的失败点**（与当前实现一致，不限于 “flush” / “ipset” 字面上的两步）：`ipset` 相关 `RuntimeError`、合并规则文本生成与异常、`tempfile.mkstemp` 失败、对规则文件的 `iptables-restore --noflush` 非零/超时/OSError、`_ensure_forward_hooks` 或 `_ensure_input_from_vpn` 未就绪 等，凡导致 `_kernel_rollback_and_fail` 被调用的路径均 **整表**恢复 `saved`。
- **与 JSON/应用层 的区分**：`saved` 只覆盖 **本函数开始时** 的内核 `filter` 全表。失败时用它把内核一步拉回；**不**回滚 `data/firewall` 等 JSON 或 `FirewallRuleService` 的持久化状态，那是另一层责任。

## 3. SortableJS 本地化

### 变更文件
- `app/ui/static/Sortable.min.js`（新增）
- `main.py`
- `app/ui/pages/firewall.py`

### 设计
- 下载 `Sortable.min.js` 到 `app/ui/static/`
- `main.py` 中 `app.add_static_files("/static", ...)` 注册静态资源
- firewall.py 引用改为 `/static/Sortable.min.js`

## 4. 仪表盘清理

### 变更文件
- `app/ui/pages/dashboard.py`

### 设计
删除内容：
- 趋势图 SVG（`_build_curve_svg` 及调用）
- 摘要行假数据：SSL 证书余天 82 Days、今日阻断请求 `len(alerts)*7`、系统平均负载假公式
- 快捷操作卡片（只弹占位提示）

替换为真实数据：
- 在线设备 footer 改为 "共 N 个注册用户"
- 摘要行：最近证书到期（调用 `CertService.list_all()` 计算）、防火墙规则数、用户组数、注册用户数
- `_summary_row` 支持 `value_class` 参数，证书即将到期时高亮

## 5. 启用/停用生效

### 变更文件
- `app/services/user/crud.py`

### 设计
- `toggle_status` 新增：
  - `_update_ccd_disable(username, disable)`: 停用时在 CCD 文件首行写入 `disable`，启用时移除
  - `_kill_client_session(username)`: 停用时通过 socket 连接管理接口发送 `kill <username>`
- OpenVPN CCD `disable` 指令会拒绝该用户连接

## 6. 用户批量导入

### 变更文件
- `app/ui/pages/users.py`

### 设计
- `_show_import_dialog()`: 弹窗包含 textarea（粘贴内容）和 file upload（CSV/TXT）
- 格式：每行 `用户名 组名` 或 `用户名,组名`
- `_do_import()`:
  1. 解析每行，支持逗号和空格分隔
  2. 预检查：收集所有引用的组名/ID，验证全部存在，有缺失则整批拒绝
  3. 检查用户名重复
  4. 逐一调用 `UserService.create()`

## 7. 证书搜索

### 变更文件
- `app/ui/pages/certs.py`

### 设计
- 页面头部增加搜索输入框 + 搜索按钮
- `_render_cert_list()` 增加模糊过滤逻辑
- `_refresh_cert_list()` 清空容器后重新渲染
