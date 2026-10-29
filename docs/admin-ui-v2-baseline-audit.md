# Admin UI V2 基线审计（2026-10-22）

本轮以 `feature/admin-ui-v2` 当前实现为继续迁移基线，目标不是重复 V1 全量审计，而是把 V2 新增的 Vue/Vite、构建链和页面视觉边界固化为可持续约束。

## 结论

- V2 继续采用 **Python/NiceGUI 负责业务与交互，Vue 只做视觉增强** 的路线。
- 不引入 Vue Bits 与 TresJS；后续只有出现明确、不可由现有栈低成本完成的需求时再按组件级评估。
- 页面迁移已按 Dashboard → Certs → Groups → Services → Peers → Users → Settings → Firewall 顺序全部完成，初始化页也已同步 V2 视觉。
- Firewall 已完成 V2 迁移与后续结构拆分；保持单一 `FirewallPage` 入口，通过 remote/import/batch mixin 与纯 helper 分离职责，不做整页 Vue 重写。

## 当前页面规模

Firewall 从最初约 4,500 行的单文件拆为清晰边界：

- `app/ui/pages/firewall.py`：约 1,779 行，保留页面初始化、Tabs、公共状态与中心规则主逻辑；
- `app/ui/pages/firewall_remote.py`：约 1,446 行，对端远端交互；
- `app/ui/pages/firewall_imports.py`：约 813 行，中心/远端导入与进度流程；
- `app/ui/pages/firewall_batch.py`：约 267 行，批量启停与删除；
- `app/ui/firewall_helpers.py`：约 571 行，纯解析、排序与 AG Grid 列定义。

拆分保持 `FirewallPage` 唯一入口与原方法调用方式，不改变 SSH、iptables、RuleService、PeerService 或缓存语义；目标是降低单文件复杂度，而不是追求总 LOC 机械下降。

## 本轮新增的自动约束

CI 单元测试新增两条架构契约：

1. `app/services` 不得反向导入 `app.ui`；
2. Vue V2 源码不得直接使用 `fetch`、axios、WebSocket 或业务 API 路径。

已有契约继续保证：

- 页面不得重新引入 `ui.run_javascript`、手写 `<script>`、SortableJS；
- 静态 CSS/JS 统一由 `theme.py` 注册；
- 设计 Token 唯一且共享；
- 页面继续复用 toolbar/actions/states/form/data-grid/charts 等组件。

## 构建链状态

- Python CI 固定 3.14；
- Vue 构建使用 Node 22；
- Docker 使用 frontend-builder → frontend-assets → Python runtime 多阶段构建；
- systemd / 本地启动统一复用 `build-ui.sh` 与 `start.sh`；
- GitHub Actions push 范围扩展到 `feature/**`，V2 功能分支可直接获得完整验证。

## 构建链收口

前端已提交 `package-lock.json`，GitHub Actions 与 Docker frontend-builder 均切换为 `npm ci`。lockfile 固定完整依赖树并使用官方 `registry.npmjs.org` URL；构建阶段仍通过 `NPM_REGISTRY` 配置国内镜像，兼顾可复现性与镜像可切换性。


## 页面迁移进展

- Dashboard：V2 首页基线；
- Certs：4 项证书状态摘要，内容区无重复页标题，证书业务路径未改；
- Groups：复用同一 `vpn-v2-overview-grid`，内容区无重复页标题，保留现有列表、表单和批量操作实现。
- Services：复用共享 V2 Surface，内容区无重复页标题；本机 systemd、对端 SSH、实时日志状态仍由 Python/NiceGUI 管理，不创建虚假的汇总状态。
- Peers：站点/远端运维、日志与快捷操作统一 V2 视觉，SSH/安装状态仍由 Python 管理。
- Users：高密度用户/会话表格切到统一 AG Grid，批量动作、筛选与弹窗视觉完成收口。
- Settings：按设置域拆分 Tabs 与共享表单 Section，并增加宽度/按钮圆角的 Chrome 几何回归断言。
- Firewall：中心/远端规则完成 V2 迁移，Community AG Grid 支持优先级拖动；重复页脚和旧 Sortable 路径已清理。

V2 合并后已通过独立 PR 完成 Firewall 结构拆分，并以 Unit + Chrome smoke 锁定业务与视觉回归。


## 内容区页标题规则

V2 页面不再重复显示当前页面名称或营销式副标题。当前页面身份已经由侧边栏高亮和 App Header 左上角标题同时表达，因此内容区必须直接进入业务内容。

Dashboard 固定顺序为：

1. 在线设备 / 服务状态 / 累计流量指标卡；
2. 两个主要图表；
3. 数据异常提示（仅发生异常时出现）；
4. 最底部实例 / 用户 / 用户组 / 防火墙 / 证书 / 健康度状态条。

明确移除 `OVERVIEW`、`实时状态 · 安全接入 · 统一运维`、`最后更新 ...`，并通过单元测试禁止后续页面重新引入 `vpn-v2-page-intro`。
