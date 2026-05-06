# 设计：CSS 统一重构

## 通用 mgmt-* 组件体系

| 通用类名 | 替代的旧类名 | 用途 |
|---------|------------|------|
| `mgmt-page` | `group-page`, `user-page`, `cert-page`, `service-page`, `settings-page`；防火墙为 `page-shell` + `page-shell--firewall` + 本行 | 页面容器 |
| `mgmt-panel` | `*-header-panel`, `*-list-panel`；防火墙内列表区为 `mgmt-panel` + `mgmt-panel-flex` | 面板容器 |
| `mgmt-header-row` | `*-header-row`；防火墙与组管理同构 | 标题栏布局 |
| `mgmt-header-copy` | `*-header-copy`；防火墙已用本类（旧 `fw-header-copy` 已弃用） | 标题文字区 |
| `mgmt-title` | `*-title`；防火墙已用本类 | 页面标题 |
| `mgmt-desc` | `*-desc`；防火墙已用本类 | 页面描述 |
| `mgmt-toolbar` | `*-toolbar`；防火墙与 `firewall-tab-bar` 等组合使用 | 工具栏容器 |
| `mgmt-toolbar-btn` | `group-toolbar-btn`, `user-toolbar-btn`, `service-toolbar-btn` | 工具栏按钮 |
| `mgmt-list-head` | `*-list-head` | 列表头部（含分隔线） |
| `mgmt-kicker` | `*-list-kicker`, `settings-kicker` | 小标签 |
| `mgmt-record-list` | `*-record-list`, `cert-record-list`, `service-record-list` | 记录列表 |
| `mgmt-record-card` | `*-record-card`, `cert-row`, `service-row` | 记录卡片 |
| `mgmt-record-main` | `*-record-main`, `cert-main`, `service-main` | 卡片主体 |
| `mgmt-record-side` | `cert-side` | 卡片侧栏 |
| `mgmt-record-copy` | `*-record-copy`, `cert-copy`, `service-copy` | 文字区 |
| `mgmt-record-title` | `*-record-title`, `cert-name` | 记录标题 |
| `mgmt-record-meta` | `*-record-meta`, `cert-meta`, `service-meta` | 元信息行 |
| `mgmt-meta-item` | `*-record-meta-item`, `cert-meta-item`, `service-meta-item` | 元信息项 |
| `mgmt-actions` | `*-actions`, `cert-actions`, `service-actions` | 操作按钮区 |
| `mgmt-icon-btn` | `*-icon-btn`, `cert-icon-btn`, `cert-refresh-btn`, `service-icon-btn` | 图标按钮 |
| `mgmt-checkbox` | `group-checkbox`, `user-checkbox` | 复选框 |
| `mgmt-search-input` | `user-search-input` | 搜索输入框 |
| `mgmt-search-btn` | `user-search-btn` | 搜索按钮 |
| `mgmt-status-badge` | `group-status-badge` | 状态标签 |

## 保留的页面独有样式

- **用户**: `user-row-line`, `user-record-inline-meta`, `user-copy-chip`, `user-session-badge`
- **组**: `group-record-side`, `group-status-badge`, `group-uuid-badge`, `group-list-count`
- **证书**: `cert-status`, `cert-copy-chip`, `cert-advanced-*`, `cert-outline-btn`, `cert-danger-btn`
- **服务**: `service-status-shell`, `service-status-dot`, `service-name-row`, `service-name`, `service-proto`, `service-running-chip`, `service-stat-*`, `service-empty`, `service-list-meta`
- **设置**: `settings-panel`, `settings-panel-*`, `settings-stack`, `settings-toolbar`, `settings-btn`, `settings-status-text`
- **防火墙（专名 + 与 mgmt 共用）**  
  - 专名（`firewall-*` / `page-shell--firewall`）: `firewall-tab-bar`, `firewall-control-header`, `firewall-compact-tabs`, `firewall-btn-group`, `firewall-search-row`, `firewall-inline-search`, `firewall-search-shell`, `firewall-center-canvas`, `firewall-center-shell`, `firewall-tabpanels`, `firewall-search-submit`, `firewall-rule-drag`, `firewall-rule-card`, `firewall-rule-card-head`, `firewall-rule-line`, `firewall-peer-remote`, `firewall-remote-*` 等。  
  - 与**组管理等同构**的 `mgmt-*`: `mgmt-page`, `mgmt-header-row`, `mgmt-header-copy`, `mgmt-title`, `mgmt-desc`, `mgmt-panel`, `mgmt-panel-list`, `mgmt-panel-flex`, `mgmt-panel-scroll`, `mgmt-stretch`, `mgmt-list-head`, `mgmt-section-title` / `mgmt-section-sub`, `mgmt-dashed-empty` / `badge` / `title` / `copy`, `mgmt-page-foot`, `mgmt-page-footer-row`, `mgmt-toolbar-cjk-2`, `mgmt-meta-flow` 等。  
  - 已移除旧 **`fw-*`** 前缀与未使用块（如独立 `fw-search-panel` 等）。  
  - **规则管理单页总览**（双 Tab、数据流、模块索引）：见 **`openspec/changes/vpn-ui-firewall-page-2026-04-20/design.md`**。
- **仪表盘**: 全部保留（页面结构独特）

## 效果

- `theme.py`: 2981 行 → 2296 行（减少 685 行，约 23%）
- 删除废弃组件 `drag_list.py`
- 合并选择器简化（移除旧类名引用）
