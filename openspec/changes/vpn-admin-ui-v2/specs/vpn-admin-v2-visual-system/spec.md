# Spec Delta

## Purpose

为 NiceVPN 管理后台提供不改变业务语义的现代基础设施控制台视觉层，并明确 Python/NiceGUI 与 Vue 视觉组件之间的边界。

## ADDED Requirements

### Requirement: V2 视觉层不得承载业务规则
系统 SHALL 将 Vue 组件限制为展示、动效和浏览器侧视觉交互；VPN 业务状态、服务调用、危险操作确认和敏感数据处理继续由 Python 层负责。

#### Scenario: 指标卡响应鼠标位置
- **WHEN** 管理员将鼠标移动到 Dashboard 指标卡
- **THEN** Vue 视觉组件可以更新 Spotlight 位置，但不得自行读取或修改 VPN 业务数据

### Requirement: 首页保持紧凑信息层级
系统 SHALL 继续由 App Header 展示页面名称，Dashboard 内容区不得重新增加占据整行的大页面标题。

#### Scenario: 打开 Dashboard
- **WHEN** 管理员进入首页
- **THEN** 内容区直接展示紧凑上下文、KPI、图表和状态条，不重复显示“仪表盘”大标题

### Requirement: V2 首页保持原有业务语义
系统 SHALL 保留在线设备、服务状态、累计流量、服务健康度、流量快照、证书、防火墙和健康度的既有数据来源与操作语义。

#### Scenario: 点击在线设备指标
- **WHEN** 管理员点击在线设备指标卡
- **THEN** 系统仍进入既有 `/users` 页面

### Requirement: 动效尊重降低动态效果偏好
系统 SHALL 为非必要视觉动画提供 `prefers-reduced-motion` 降级。

#### Scenario: 浏览器请求降低动态效果
- **WHEN** 操作系统启用 Reduce Motion
- **THEN** Spotlight 和过渡不得持续执行明显动画，且不影响任何业务操作
