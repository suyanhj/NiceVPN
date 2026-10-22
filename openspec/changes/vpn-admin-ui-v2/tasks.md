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
- [ ] 2.4 选择 3–5 个适合运维后台的 Inspira UI 组件；首个 InteractiveGrid 已完成本地适配并记录 MIT 来源。
- [x] 2.5 增加独立 frontend build/typecheck CI，确保 Vue/TypeScript/Tailwind/motion-v 在真实 Node 22 环境可构建。
- [x] 2.6 为生产镜像增加 Node 多阶段构建，并提供 frontend-assets 导出目标；运行镜像保持纯 Python。
- [x] 2.7 Python 组件优先加载正式 Vite 构建产物，并保留原生 SFC 作为未构建时的开发回退。
- [ ] 2.8 增加并提交可复现的前端依赖锁文件，再将 CI 从 npm install 切换到 npm ci。

## 3. 双部署模式

- [x] 3.1 根目录新增 `start.sh`，使用 Docker `frontend-assets` target 构建前端后启动 Python。
- [x] 3.2 systemd 服务切换为调用 `start.sh`，支持通过 `NICEVPN_BUILD_UI=0` 使用预构建 dist。
- [x] 3.3 Docker runtime 切换 Python 3.14，多阶段复制 Vue 构建产物且不携带 Node/npm。
- [x] 3.4 GitHub Actions Python 单测收敛到 3.14；Chrome smoke 在启动应用前构建正式 Vue dist。
- [x] 3.5 新增独立 `build-ui.sh`，只构建前端产物并支持自定义输出目录；`start.sh` 与 CI 统一复用该脚本。
