# Admin UI 最终集成审计

本文件记录 `vpn-admin-ui` 第 14 阶段的可恢复清理与集成约束。

## 1. 样式引用审计

当前主题统一由 `app/ui/theme.py` 加载：

- `tokens.css`：设计变量；
- `shell.css`：统一后台外壳与共享组件；
- `effects.css`：少量视觉效果；
- `legacy.css`：尚未完成迁移的页面特定兼容样式与 Quasar 深色修正。

本轮对 `legacy.css` 做了当前分支页面级引用扫描。

### 已证明无引用并删除

- 旧证书页 `cert-*`：当前所有页面零引用，整段删除；
- 旧用户页主体：旧 header/list/card/action 等 selector 已无页面引用，删除；
- 用户在线状态 `user-session-badge` 仍被共享视图使用，因此保留。

删除前完整备份位于：

`backups/admin-ui/legacy.css.pre-integration.bak`

### 暂时保留

- `group-*`：当前仍使用 `group-list-count`、`group-record-side`；
- `service-*`：当前仍使用 `service-name`、`service-proto`、`service-running-chip`、`service-status-shell`；
- 初始化、对端部署说明、Quasar 暗色控件修正仍依赖 `legacy.css`。

这些块不能整段删除；后续只允许在证明对应 selector 已无运行时引用后继续裁剪。

## 2. JavaScript 审计

业务页面不得自行注入业务 JavaScript。

当前允许的唯一运行时 JavaScript 桥接为：

- `app/ui/copy_clipboard.py`：局域网 HTTP 环境下调用本地 `clipboard-adapter.js`，只处理剪贴板兼容；
- `app/ui/theme.py`：统一加载静态 CSS 与剪贴板脚本。

页面源码不允许出现 `ui.run_javascript`、`new Sortable` 或直接 `<script>` 注入。

## 3. 清理安全约束

- 删除前先保存可恢复备份；
- 所有文本仍为 UTF-8，无替换字符；
- CSS 花括号必须平衡；
- 不修改中文文案编码；
- 页面级迁移继续使用共享 `page_header / actions / states / form / data_grid / charts` 组件；
- 页面特有 CSS 仅保留无法由共享组件表达的布局或第三方组件兼容规则。
