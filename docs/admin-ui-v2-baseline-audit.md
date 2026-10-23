# Admin UI V2 基线审计（2026-10-22）

本轮以 `feature/admin-ui-v2` 当前实现为继续迁移基线，目标不是重复 V1 全量审计，而是把 V2 新增的 Vue/Vite、构建链和页面视觉边界固化为可持续约束。

## 结论

- V2 继续采用 **Python/NiceGUI 负责业务与交互，Vue 只做视觉增强** 的路线。
- 不引入 Vue Bits 与 TresJS；后续只有出现明确、不可由现有栈低成本完成的需求时再按组件级评估。
- 页面迁移顺序固定为 Dashboard → Certs → Groups → Services → Peers → Users → Settings → Firewall；当前 Dashboard、Certs、Groups、Services 已进入 V2。
- Firewall 继续放最后，并在迁移时同步拆分页面模块，不做整页 Vue 重写。

## 当前页面规模

按当前分支 Git tree 文件大小观察：

- `app/ui/pages/firewall.py`：约 171 KB；
- `app/ui/pages/peers.py`：约 70 KB；
- `app/ui/pages/users.py`：约 61 KB；
- `app/ui/pages/services.py`：约 48 KB；
- `app/ui/pages/settings.py`：约 47 KB；
- `app/ui/pages/certs.py`：约 21 KB；
- `app/ui/pages/dashboard.py`：约 9 KB。

因此 V2 迁移优先复用共享组件和视觉原语；Firewall 的目标是降低单文件复杂度，而不是追求总 LOC 机械下降。

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

## 待完成

前端依赖锁文件仍未提交，因此 Docker 构建当前仍使用 `npm install --package-lock=false`。后续生成并提交 `package-lock.json` 后，再统一切换为 `npm ci`，完成 OpenSpec 2.8。


## 页面迁移进展

- Dashboard：V2 首页基线；
- Certs：4 项证书状态摘要，内容区无重复页标题，证书业务路径未改；
- Groups：复用同一 `vpn-v2-overview-grid`，内容区无重复页标题，保留现有列表、表单和批量操作实现。
- Services：复用共享 V2 Surface，内容区无重复页标题；本机 systemd、对端 SSH、实时日志状态仍由 Python/NiceGUI 管理，不创建虚假的汇总状态。

后续 Peers 继续优先复用现有视觉原语，不新增新的前端框架或页面专用视觉依赖。


## 内容区页标题规则

V2 页面不再重复显示当前页面名称或营销式副标题。当前页面身份已经由侧边栏高亮和 App Header 左上角标题同时表达，因此内容区必须直接进入业务内容。

Dashboard 固定顺序为：

1. 在线设备 / 服务状态 / 累计流量指标卡；
2. 两个主要图表；
3. 数据异常提示（仅发生异常时出现）；
4. 最底部实例 / 用户 / 用户组 / 防火墙 / 证书 / 健康度状态条。

明确移除 `OVERVIEW`、`实时状态 · 安全接入 · 统一运维`、`最后更新 ...`，并通过单元测试禁止后续页面重新引入 `vpn-v2-page-intro`。
