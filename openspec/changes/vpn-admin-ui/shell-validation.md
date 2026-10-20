# 应用壳层阶段验收

## 自动化检查

- 静态资源测试验证 `static/css`、`static/js`、`static/vendor` 边界、CSS 注册顺序和敏感文件排除规则。
- 主题测试验证 Quasar 颜色、深色模式、组件默认值及四个本地 CSS 文件注册。
- 壳层测试验证 8 个既有一级 URL、子页面导航归属、活动项、桌面/窄屏导航入口，以及新壳层承载旧用户页。
- 页头测试覆盖有无说明、面包屑和操作区，以及长标题与多个操作共存。
- 截图脚本会直接请求四个 CSS 文件并校验 HTTP 200 与 `text/css`，同时把浏览器控制台错误和未捕获异常视为失败。

## 浏览器截图

截图位于 `evidence/baseline/`，由 `tests/capture_ui_baseline.py` 使用本地 Edge 和只读示例数据生成：

| 文件 | 视口 | 验收点 |
| --- | --- | --- |
| `users-wide.png` | 1440×900 | 新应用顶栏、常驻侧栏与旧用户页共存 |
| `users-navigation-collapsed-wide.png` | 1440×900 | 桌面侧栏收起后主要内容扩展且导航入口保留 |
| `users-dialog-wide.png` | 1440×900 | 新壳层与旧对话框共存 |
| `firewall-narrow.png` | 390×844 | 窄屏主要内容与导航触发器可见 |
| `firewall-navigation-narrow.png` | 390×844 | 临时抽屉完整展开且活动项正确 |
| `settings-narrow.png` | 390×844 | 新壳层与旧设置页签共存 |
| `dashboard-wide.png` | 1440×900 | 新壳层与待最后迁移的仪表盘共存 |
| `log-dialog-wide.png` | 1440×900 | 新壳层与旧日志对话框共存 |

截图流程还验证桌面侧栏可以收起后恢复、窄屏抽屉最终位置和宽度正确，并确保所有页面加载期间没有浏览器控制台错误。
