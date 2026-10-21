# Admin UI 最终集成验证记录

验证对象：`feature/admin-ui-integration` → `admin-ui`，PR #17。

## 最终 CI

GitHub Actions run **#127**（head `ea4f2522c4befd5c35c63b97dc19bc98379416d0`）完成且全部成功：

- Python 3.10：**594 passed, 1 warning**；
- Python 3.14：**594 passed, 1 warning**；
- Chrome browser smoke：**4 passed**；
- OpenSpec strict validation：**success**。

现有唯一 pytest warning 为 FastAPI / Starlette TestClient 的上游弃用提示，不是本次 Admin UI 回归失败。

## 浏览器与响应式验证

真实 Chrome smoke 使用生产页面类、统一 App Shell 和主题，仅将系统服务替换为内存数据源，避免 systemd、iptables、EasyRSA、SSH 等真实运维副作用。

覆盖：

- 仪表盘；
- 用户管理；
- 用户组管理；
- 对端站点；
- 对端部署说明子页面；
- 防火墙；
- 证书；
- 服务；
- 系统设置；
- 初始化向导。

桌面 `1440×900` 与窄屏 `390×844` 均逐页检查 document 外层横向溢出；仪表盘通过 Selenium 确认 ECharts 已实际生成 canvas。另覆盖新建用户、新建对端、防火墙更多菜单与远端 Tab、证书高级操作、服务 Tab、设置页各 Tab、初始化安装分支等高价值交互。

## 规模回归

CI 使用代表性数据验证共享适配层与 AG Grid：

- 用户：5,000 条；
- 证书：5,000 条；
- 防火墙中心规则：2,000 条；
- 防火墙对端规则：2,000 条；
- 对端站点：100 个。

验证内容包括批量 row model 转换、分页、筛选匹配、稳定 row id、选择恢复、拖动列配置和大数据量表格构建。规模测试设置 12 秒宽松预算，仅用于阻止明显退化到逐行 I/O 或 O(n²) 路径。

## 异常隔离

新增路由级异常矩阵，覆盖用户、组、对端、对端辅助数据、防火墙、证书、服务、设置、仪表盘等数据源异常。页面要求保持可构建并显示明确错误状态，不允许单个数据源异常击穿整页。

本轮补齐的实际缺口包括：

- 用户组列表首屏读取失败隔离；
- 对端辅助组/SSH 数据失败隔离；
- 设置配置加载失败隔离。

## 样式清理与安全回退

删除旧样式前已保存：

`backups/admin-ui/legacy.css.pre-integration.bak`

已证明并删除的主要旧样式：

- 旧证书页 `cert-*` 主体；
- 旧用户页 header/list/card 等主体。

仍被当前页面使用或位于共享响应式组合 selector 中的规则继续保留，不做脆弱的字符串级强删。清理后 CSS 花括号平衡、UTF-8 与替换字符检查均通过。

详细引用审计见：

`docs/admin-ui-integration-audit.md`

## 可访问性

已执行并自动化锁定：

- 可点击卡片支持 `tabindex=0`、`role=button`、Enter/Space；
- 共享按钮、菜单、复制按钮、可点击卡片、确认/表单按钮具备统一 `:focus-visible` 焦点环；
- 状态徽标始终包含文字与图标，不仅依赖颜色；
- 普通文本 token 在主要深色表面上的 WCAG AA 对比度要求 ≥ 4.5:1；
- `--vpn-text-soft` 已从过暗值调整为 `#8393aa`；
- 危险操作继续使用影响说明、对象数量及必要时指定确认文本。

## OpenSpec 与仓库卫生

CI 固定使用 OpenSpec `1.14.2` 执行：

`openspec validate vpn-admin-ui --strict --no-interactive`

实际输出：

`Change 'vpn-admin-ui' is valid`

同一 job 还验证：

- `git diff --check origin/admin-ui...HEAD`；
- `app/ui`、单元/浏览器测试及 OpenSpec 变更文件均可按 UTF-8 解码；
- 不含 U+FFFD replacement character；
- OpenSpec 校验结束后 `git diff --exit-code`；
- 无未跟踪产物。

因此当前变更满足进入代码评审与部署验证的条件。
