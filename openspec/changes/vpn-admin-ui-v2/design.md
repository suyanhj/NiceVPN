# Design

## Context

V1 已具备稳定的 NiceGUI/Quasar 功能 UI、Chrome smoke 和 OpenSpec 校验。审计确认 `app/services` 不依赖 `app.ui`，但 Users、Peers、Services、Settings 与 Firewall 页面包含大量 Python UI 回调，因此 V2 不采用整页 Vue 重写。

V2 已完成迁移与收口：Vue/Vite 只承担视觉增强；业务、状态、危险操作确认、表格与服务调用继续由 Python/NiceGUI 管理。

## Decisions

### 1. Vue 仅作为视觉增强层

自定义 Vue 组件不得直接调用 VPN 业务服务、读取敏感配置或替代 Python 侧危险操作确认。`VpnSpotlightSurface` 等组件只处理视觉效果，文本、状态、导航与业务回调继续由 NiceGUI 渲染。

### 2. 页面身份只保留一层

页面名称只显示在 App Header / 侧边导航，内容区不重复大标题和营销式副标题。Dashboard、管理页、Settings 和 Firewall 均遵循该规则。

### 3. 正式前端构建链可复现

Vite 子模块使用 TypeScript、Tailwind CSS 与 motion-v。运行镜像使用预构建产物，不在 Python runtime 安装 Node。

正式构建提交 `package-lock.json`，GitHub Actions 与 Docker 使用 `npm ci`。lockfile 使用官方 registry URL，构建时仍可通过 `NPM_REGISTRY` 切换国内镜像。

### 4. Firewall 视觉迁移与结构重构解耦

Firewall V2 视觉、AG Grid Community 拖动排序和旧交互清理先独立完成；随后通过多条小 PR 完成结构拆分，避免一次性搬动 4,500+ 行代码。

最终边界：

- `FirewallPage`：页面壳、共享状态、跨 Tab 调度；
- `FirewallCenterGridMixin`：中心 Grid / Filter / Selection / Reorder；
- `FirewallCenterActionsMixin`：中心创建 / 编辑 / 删除；
- `FirewallCenterImportMixin`：中心导入；
- `FirewallRemoteMixin`：远端同步 / Toolbar / Grid / Reorder；
- `FirewallRemoteActionsMixin`：远端单条规则动作；
- `FirewallRemoteImportBatchMixin`：远端导入 / 批量；
- `firewall_helpers.py`：无页面实例状态的纯 helper。

重构不修改 RuleService、PeerService、SSH、iptables、remote cache、导入格式、优先级和危险操作确认语义。

### 5. 保持业务和测试边界

Dashboard 数据采集、ECharts options、刷新动作和错误反馈保持 Python 主导。V2 浏览器断言验证主要路由真实挂载、桌面/窄屏无横向溢出、Settings 几何布局、文字按钮圆角与 Firewall 关键交互。

Pull request 的昂贵测试范围固定按 PR base → PR head 判断，避免 synchronize 事件引用临时 merge ref 而漏跑 Chrome smoke。

## Non-Goals

- 不把 Users、Firewall、Peers、Services 等业务页整页重写为 Vue。
- 不引入第二套表单/表格 UI Framework。
- 不为了技术栈数量引入 Vue Bits、TresJS、Three.js 或高强度粒子动画。
- 不为追求更小 LOC 继续拆分已经具备单一职责的 Firewall 模块。
