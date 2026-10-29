# Admin UI V2 基线与最终收口（2026-10-22 → 2026-10-29）

本文件最初用于记录 `feature/admin-ui-v2` 的迁移基线；V2 已完成并等价整合进入 `admin-ui`。当前内容同时记录最终架构边界，避免后续继续按早期“大单文件”状态理解代码。

## 结论

- V2 继续采用 **Python/NiceGUI 负责业务与交互，Vue 只做视觉增强** 的路线。
- 不引入 Vue Bits 与 TresJS；只有出现明确且现有栈无法低成本完成的需求时，再按组件级评估。
- Dashboard、Certs、Groups、Services、Peers、Users、Settings、Firewall 与初始化页均已完成 V2 迁移。
- Firewall 的视觉迁移与结构重构均已完成；没有整页 Vue 重写，RuleService / PeerService / SSH / iptables / cache 业务路径保持 Python 主导。
- 前端依赖已提交 `package-lock.json`，CI 与 Docker 前端构建统一使用 `npm ci`。
- PR scope 检测对 pull request 固定使用 base → head，避免 synchronize 事件因临时 merge ref 导致 Chrome smoke 被错误跳过。

## Firewall 最终模块边界

原 `app/ui/pages/firewall.py` 曾超过 4,500 行。结构重构通过多条独立 PR 逐步完成，当前职责拆分为：

- `app/ui/pages/firewall.py`：约 670 行；页面壳、Tabs、共享状态、跨 Tab 导入/批量调度与导出。
- `app/ui/pages/firewall_center.py`：约 650 行；中心 AG Grid、筛选、选择、操作入口与优先级拖动。
- `app/ui/pages/firewall_center_actions.py`：约 510 行；中心规则创建、编辑、删除与中心批量删除。
- `app/ui/pages/firewall_center_imports.py`：约 490 行；中心 backup / 简写导入、预检查与后台进度。
- `app/ui/pages/firewall_remote.py`：约 890 行；远端同步、Toolbar 状态、AG Grid、筛选/选择与拖动排序。
- `app/ui/pages/firewall_remote_actions.py`：约 660 行；远端单条规则创建、编辑、删除与工作副本写回。
- `app/ui/pages/firewall_remote_imports.py`：约 400 行；远端导入与批量删除/启停。
- `app/ui/firewall_helpers.py`：约 590 行；纯解析、字段转换、排序与 Grid 列定义。

停止拆分标准不是机械追求更少 LOC，而是每个模块已经具备清晰单一职责。后续若继续调整，应优先保持这些边界，而不是重新把中心/远端/导入/动作逻辑塞回页面壳。

## 其它页面规模与策略

Users、Peers、Services、Settings 等页面仍有较多 Python UI 回调，但已经复用共享 actions / toolbar / form / states / AG Grid 体系。当前没有继续拆分的阻塞性需求，后续按实际维护痛点独立评估，不与 V2 视觉层绑定。

## 自动架构约束

CI / 单元测试持续保证：

1. `app/services` 不得反向导入 `app.ui`；
2. Vue V2 源码不得直接使用 `fetch`、axios、WebSocket 或业务 API 路径；
3. 页面不得重新引入 SortableJS 或散落的浏览器脚本路径；
4. Firewall Center / Remote / Import / Actions 模块边界有静态契约，防止大块逻辑重新回流主页面；
5. Settings 主表单宽度、V2 文字按钮圆角、防火墙页脚与 AG Grid 拖动均有真实 Chrome 回归；
6. 桌面与窄屏主要路由检查横向溢出。

## 构建链

- Python CI：3.14；
- Node：22；
- 前端：Vite + TypeScript + Tailwind CSS + motion-v；
- 依赖：`package-lock.json` + `npm ci`；
- Docker：frontend-builder → frontend-assets → Python 3.14 runtime；
- systemd / 本地：复用 `build-ui.sh` 与 `start.sh`；
- 生产可通过 `NICEVPN_BUILD_UI=0` 使用预构建 dist，避免启动时现场构建前端；
- Docker production runtime build 已在 V2 最终收口 CI 中验证通过。

## 页面迁移状态

- Dashboard：V2 首页基线、KPI、图表与底部状态条。
- Certs：统一 AG Grid、状态摘要与共享操作。
- Groups：共享 V2 页面原语、列表与批量操作。
- Services：V2 Surface、Tabs、实例切换与日志视图。
- Peers：站点/远端运维视觉与共享操作。
- Users：高密度用户/会话 Grid、筛选、批量操作与弹窗。
- Settings：设置域 Tabs、共享 Form Section；有宽度和按钮圆角浏览器回归。
- Firewall：中心/远端规则 V2、AG Grid Community 拖动排序、模块化结构完成。
- First Run Setup：初始化流程同步 V2 视觉，业务流程保持原实现。

## 内容区页标题规则

V2 页面不重复显示当前页面名称或营销式副标题。页面身份由侧边栏高亮与 App Header 表达，内容区直接进入业务内容。

Dashboard 固定顺序：

1. 在线设备 / 服务状态 / 累计流量指标卡；
2. 两个主要图表；
3. 数据异常提示（仅异常时出现）；
4. 最底部实例 / 用户 / 用户组 / 防火墙 / 证书 / 健康度状态条。

继续禁止重新引入 `vpn-v2-page-intro`、重复页标题和无业务价值的页脚说明。
