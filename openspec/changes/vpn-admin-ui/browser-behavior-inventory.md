# 浏览器行为盘点

本清单约束管理后台中只能在浏览器执行的行为。动态业务数据、权限判断、状态变更与异常处理继续保留在 Python；前端仅承担浏览器交互适配。

| 行为 | 现状与选择 | 能力层级 | 落点 | 状态 |
| --- | --- | --- | --- | --- |
| 防火墙规则拖拽 | 使用 NiceGUI `Element.make_sortable()`，复用其内置 SortableJS | NiceGUI | Python 组件配置 | 已迁移，重复的 `Sortable.min.js` 已备份并删除 |
| 文本复制 | 页面统一调用共享复制入口；安全上下文使用 `ui.clipboard.write()`，局域网 HTTP 使用固定版本、本地分发的 `clipboard.js` | NiceGUI + 成熟开源库 | Python 门面与 `static/vendor` | 已迁移；失败时提供只读文本框和手动复制说明 |
| 日志跟随滚动 | 使用 `ui.scroll_area` 的 `on_scroll` 与 `scroll_to` | NiceGUI/Quasar | Python 组件配置 | 已迁移，原 document 监听和滚动脚本已删除 |
| 输入焦点 | 优先使用 Quasar 对话框焦点管理、`autofocus`/`data-autofocus` 与组件 `run_method('focus')` | Quasar/NiceGUI | Python 组件配置 | 已复核并用于登录与手动复制回退，无独立焦点脚本 |
| 页面主题资源加载 | 由 `theme.py` 注册本地静态 CSS | NiceGUI | Python 静态资源入口 | 无行为型 JavaScript |

## 采用顺序

每项浏览器行为依次检查 NiceGUI/Quasar、浏览器标准 API、已有本地库、新增成熟开源库，只有均无法满足时才允许建立薄适配层并放入 `static/js`。不得在不同页面重复实现同一行为，也不得把业务规则或敏感数据固化进静态脚本。

当前 `static/js/clipboard-adapter.js` 只负责连接 NiceGUI 回调与本地 `clipboard.js`，不包含业务判断或数据；其成功、失败和手动回退路径均有测试。后续若新增文件，仍必须同时补充用途、调用方和测试。
