# Design

## Context

V1 已具备稳定的 NiceGUI/Quasar 功能 UI、89 个单元测试文件、Chrome smoke 和 OpenSpec 校验。审计确认 `app/services` 不依赖 `app.ui`，但 Users、Peers、Services、Settings 与 Firewall 页面仍包含大量 Python UI 回调；Firewall 单页超过 4,300 行。因此 V2 不适合整页迁移 Vue。

## Decisions

### 1. Vue 仅作为视觉增强层

自定义 Vue 组件不得直接调用 VPN 业务服务、读取敏感配置或替代 Python 侧危险操作确认。首个组件 `VpnSpotlightSurface` 只处理鼠标位置和视觉 Glow，文本、状态、导航与回调继续由 NiceGUI 渲染。

### 2. 首页先做视觉 PoC

第一阶段只升级 App Shell 和 Dashboard，验证深色 Surface、导航状态、背景网格、指标卡 Spotlight、图表容器和状态条。页面名称继续只显示在 App Header，不恢复内容区大标题。

### 3. 分两步接入正式前端构建链

PoC 使用 NiceGUI 官方支持的原生 Vue SFC，保证拉取分支后无需 npm 即可运行。视觉方向确认后，再建立 Vite 子模块并引入 Tailwind CSS、motion-v 与选定 Inspira UI 组件；运行镜像使用预构建产物，不在生产容器安装 Node。

### 4. 保持业务和测试边界

Dashboard 数据采集、ECharts options、刷新动作和错误反馈保持原实现。V2 新增浏览器断言验证 Vue 组件真实挂载及桌面/窄屏无横向溢出。

## Non-Goals

- 不重写 Users、Firewall、Peers、Services 等页面业务逻辑。
- 不引入第二套表单/表格 UI Framework。
- 首页 PoC 不引入 Vue Bits、Three.js 或高强度粒子动画。
