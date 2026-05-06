# vpn-bugfix-2026-04-03-r3 — 任务清单

## 任务

- [x] **T1 - 防火墙页面统一渲染**
  - 移除 DragList 依赖
  - `_render_rule_card` 增加 `show_owner` 参数和 `data-rule-id` 属性
  - filtered 模式加拖拽手柄 `firewall-rule-drag`（历史 `fw-drag-handle`）
  - `firewall-rule-line` 移入 `firewall-rule-card` div 内部
  - filtered 模式注册 SortableJS + firewall_reorder 事件

- [x] **T2 - ipset 清理顺序修复**
  - 新增 `_flush_chain()` 方法
  - `rebuild_rules` 先 flush chain 再 cleanup ipsets

- [x] **T2b - `filter` 表快照与回滚**（`design.md` §2.2 与 `IptablesManager.rebuild_rules` 对齐）
  - 先 `iptables-save -t filter`，失败则中止（不 flush）
  - 失败路径 `_iptables_restore_filter_t_bytes`（`iptables-restore` + stdin 整表、超时/日志与源码一致）
  - Windows 无 iptables 早退、回滚 `critical` 等要点写入设计说明

- [x] **T3 - SortableJS 本地化**
  - 下载 Sortable.min.js 到 `app/ui/static/`
  - `main.py` 注册 `/static` 路由
  - firewall.py CDN 引用改为 `/static/Sortable.min.js`

- [x] **T4 - 仪表盘清理假数据**
  - 删除趋势图 SVG 及 `_build_curve_svg`
  - 删除假数据摘要行（SSL 82 Days、今日阻断、平均负载）
  - 删除快捷操作卡片
  - 新增真实指标：证书最近到期天数、用户数、组数
  - `_summary_row` 支持 `value_class` 参数

- [x] **T5 - 启用/停用 CCD 生效**
  - `_update_ccd_disable()`: CCD 写入/移除 `disable`
  - `_kill_client_session()`: 管理接口踢人下线
  - `toggle_status` 调用两个新方法

- [x] **T6 - 用户批量导入**
  - `_show_import_dialog()`: textarea + file upload
  - `_do_import()`: 解析、预检查组存在、去重、逐一创建

- [x] **T7 - 证书搜索栏**
  - 页面头部加搜索输入框 + 按钮
  - `_render_cert_list()` 支持模糊过滤
  - `_refresh_cert_list()` 刷新方法
