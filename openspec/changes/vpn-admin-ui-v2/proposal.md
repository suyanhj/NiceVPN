# Proposal

## Why

现有 V1 管理后台已经完成业务流程、响应式壳层、共享组件和测试基线，但视觉仍偏标准 NiceGUI/Quasar 后台。V2 需要在不重写业务服务和高风险运维流程的前提下，引入受现代 SaaS / Infrastructure Console 启发的视觉层，并验证 NiceGUI 自定义 Vue 组件能否稳定承载高级视觉效果。

## What Changes

- 从 V1 稳定提交独立创建 `feature/admin-ui-v2`，不污染正在验收的 V1。
- 第一阶段只改 App Shell 与 Dashboard，保留全部 Dashboard 数据采集、ECharts 和操作语义。
- 使用 NiceGUI 自定义 Vue SFC 承载无业务知识的 Spotlight 等视觉效果；业务数据和事件仍由 Python 控制。
- 将设计令牌升级为更克制的深色基础设施控制台风格，增加柔和蓝青强调色、玻璃 Surface、弱网格与低强度 Glow。
- 首页视觉方向确认后，再引入 Vite + Tailwind CSS + motion-v，并按需迁入少量 Inspira UI 源码组件。
- Vue Bits 与 TresJS 保持可选，不进入首页首版依赖。

## Capabilities

### New Capabilities

- `vpn-admin-v2-visual-system`: NiceVPN V2 的视觉令牌、App Shell、Dashboard 视觉组件、Vue 边界和动效约束。

## Impact

- 第一阶段主要影响 `app/ui/components/app_shell.py`、`app/ui/components/cards.py`、`app/ui/pages/dashboard.py`、`app/ui/static/css/` 与新增 `app/ui/vue/`。
- `app/services/`、`app/models/`、`app/api/`、数据格式和运维行为不变。
- 第一阶段无需 Node 即可运行，便于快速视觉验收；正式 V2 构建层将在方向确认后增加 Vite/Tailwind/motion-v。
