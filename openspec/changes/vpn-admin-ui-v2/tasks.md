# Tasks

## 1. V2 基线与首页视觉 PoC

- [x] 1.1 从 V1 稳定 head 创建独立 `feature/admin-ui-v2` 分支。
- [x] 1.2 审计页面规模、UI/Service 边界、CSS、CI 和 Docker 构建链。
- [x] 1.3 更新 V2 Design Tokens、App Shell 品牌区、背景与导航状态。
- [x] 1.4 新增无业务知识的 NiceGUI 自定义 Vue Spotlight 组件。
- [x] 1.5 将 Dashboard 三个 KPI 卡接入 V2 Spotlight，保持原业务数据和导航回调。
- [x] 1.6 重做 Dashboard 图表容器和紧凑状态条视觉，不恢复内容区大页面标题。
- [x] 1.7 运行 Python 3.14、Chrome browser smoke、V2 frontend build 与 OpenSpec strict validation。
- [x] 1.8 用户本地验收首页视觉方向并确认继续推进 V2。

## 2. 正式 Vue 构建层（首页视觉方向确认后）

- [x] 2.1 建立 Vite + TypeScript 子模块，并固定一层依赖版本。
- [x] 2.2 引入 Tailwind CSS，仅用于 Vue 构建层和基础布局工具。
- [x] 2.3 引入 motion-v 作为复杂 Vue 动效统一引擎。
- [x] 2.4 完成视觉组件选型收口：保留已适配的 InteractiveGrid，并以 SpotlightSurface 作为共享视觉增强原语；不为凑组件数量额外引入 Vue Bits/TresJS/更多第三方 UI。
- [x] 2.5 增加独立 frontend build/typecheck CI，确保 Vue/TypeScript/Tailwind/motion-v 在真实 Node 22 环境可构建。
- [x] 2.6 为生产镜像增加 Node 多阶段构建，并提供 frontend-assets 导出目标；运行镜像保持纯 Python。
- [x] 2.7 Python 组件优先加载正式 Vite 构建产物，并保留原生 SFC 作为未构建时的开发回退。
- [x] 2.8 提交 `package-lock.json`，CI 与 Docker 前端构建统一使用 `npm ci`；lockfile 使用官方 registry URL，运行时仍可按 `NPM_REGISTRY` 切换镜像。

## 3. 双部署模式

- [x] 3.1 根目录新增 `start.sh`，使用 Docker `frontend-assets` target 构建前端后启动 Python。
- [x] 3.2 systemd 服务切换为调用 `start.sh`，支持通过 `NICEVPN_BUILD_UI=0` 使用预构建 dist。
- [x] 3.3 Docker runtime 切换 Python 3.14，多阶段复制 Vue 构建产物且不携带 Node/npm。
- [x] 3.4 GitHub Actions Python 单测收敛到 3.14；Chrome smoke 在启动应用前构建正式 Vue dist。
- [x] 3.5 新增独立 `build-ui.sh`，只构建前端产物并支持自定义输出目录；`start.sh` 与 CI 统一复用该脚本。
- [x] 3.6 优化 Dockerfile 层数与国内构建链：华为公共基础镜像、中科大 Debian 源、npmmirror npm 源，并使用 BuildKit cache mount 避免下载缓存进入 runtime 镜像。


## 4. V2 页面迁移

- [x] 4.1 锁定 V2 页面视觉原语与架构边界；页名只显示在 App Header / 侧边导航，内容区不重复页标题。
- [x] 4.2 Certs：增加 V2 状态摘要与共享视觉层，不增加内容区页标题；保留证书续签、吊销、CRL、CA 操作原 Python 业务路径。
- [x] 4.3 Groups：复用共享 V2 页面原语与列表/状态组件，保留现有分组业务与批量操作路径。
- [x] 4.4 Services：升级信息层级、Tabs 与列表 Surface，不改本机/对端服务控制和日志业务路径。
- [x] 4.5 Peers：升级站点/远端运维视觉，保持 Python 状态管理。
- [x] 4.6 Users：升级高密度用户与会话视图，并统一 AG Grid、批量操作与对话框视觉。
- [x] 4.7 Settings：按设置域拆分视觉层级，统一共享表单与按钮样式，并补浏览器几何回归检查。
- [x] 4.8 Firewall：完成 V2 视觉迁移、AG Grid Community 拖动排序与页脚清理；业务逻辑仍由 Python/NiceGUI 管理。超大页面的结构拆分移至 V2 合并后的独立重构 PR，避免与视觉迁移耦合。
