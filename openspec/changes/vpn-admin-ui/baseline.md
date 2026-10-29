# VPN 管理后台改造基线

## 运行环境

记录日期：2026-10-20。

| 项目 | 版本 |
| --- | --- |
| Python | 3.14.4 |
| NiceGUI | 3.18.0 |
| FastAPI | 0.143.0 |
| Pydantic | 2.14.1 |
| pytest | 9.1.1 |

测试统一使用仓库根目录 `.venv`，工作目录为 `script/py/vpn`。直接调用系统 Python 会因为缺少项目依赖产生无效的收集错误，不计入项目基线。

## 路由与导航矩阵

| 路由 | 导航归属 | 页面 | 查询参数或分支 | 主要区域 |
| --- | --- | --- | --- | --- |
| `/` | 仪表盘 | `DashboardPage` / `InitPage` | 未初始化时进入引导 | 指标、状态摘要、告警；或初始化步骤 |
| `/users` | 用户管理 | `UsersPage` | 无 | 搜索、列表、分页、批量操作 |
| `/groups` | 组管理 | `GroupsPage` | 无 | 分组列表、选择、批量操作 |
| `/peers` | 对端站点 | `PeersPage` | 无 | 对端卡片、Mesh、远程操作 |
| `/peers/manual` | 对端站点 | `PeersPage.render_manual_page` | `peer` | 部署说明、命令复制、说明下载 |
| `/firewall` | 防火墙规则 | `FirewallPage` | 内部中心/对端页签 | 规则列表、导入导出、远程拉取与推送 |
| `/certs` | 证书管理 | `CertsPage` | 无 | 搜索、分页、批量续签/吊销、高级操作 |
| `/services` | 服务管理 | `ServicesPage` | `tab`、`peer` | 本机/对端实例、控制、日志、配置 |
| `/settings` | 系统设置 | `SettingsPage` | 内部页签 | OpenVPN、服务端、设备、SSH、下载、通知 |

所有非根管理路由均先检查初始化状态。`/peers/manual` 的活动导航仍归属于“对端站点”；`/services?tab=peers&peer=...` 可直接聚焦对端服务。

## 页面、弹窗与操作矩阵

| 页面 | 页面级操作 | 行级或对象级操作 | 弹窗、子视图和异步流程 |
| --- | --- | --- | --- |
| 用户 | 搜索、新建、批量导入、批量下载、批量重置绑定、分页与选择 | 启停、踢下线、切换分组、配置下载/编辑、一次性链接复制/推送、重置绑定、删除；序列号复制 | 新建进度、导入预检与结果、分组选择、配置编辑、危险确认 |
| 用户组 | 新建、全选、批量启用/停用/删除 | 编辑、启停、删除、复制组 ID；根组保护 | 新建/编辑表单、普通与危险确认 |
| 对端 | 刷新 Mesh、新建、帮助 | 编辑、删除、安装、服务、日志、推送、部署说明 | 新建/编辑表单、远程安装进度、推送进度、远程日志、帮助 |
| 部署说明 | 返回、复制全部、下载 Markdown | 单条命令复制、配置下载 | 不存在对端的错误状态 |
| 防火墙 | 中心/对端页签、搜索/筛选、新建、导入、导出、批量启停/删除 | 编辑、原始文本编辑、启停、删除、复制、排序 | 规则表单、导入预检与进度、对端选择、SSH 拉取、推送确认 |
| 证书 | 搜索、刷新、选择、批量续签/吊销 | 序列号复制、续签、吊销 | 高级操作、CRL 更新、CA 重建指定文本确认 |
| 服务 | 本机/对端页签、刷新 | 本机启停/重启、配置编辑、服务/设备/状态日志；对端电源、开机启动、日志 | 配置编辑器、日志快照与跟随、操作确认 |
| 设置 | 六个页签、各分区保存/验证/测试 | 路径验证、sysctl、下载代理测试、通知通道测试 | OpenVPN、服务端、设备、SSH、下载、钉钉/企微/Rocket.Chat 表单 |
| 初始化 | 检测、自动安装/指定路径、返回、继续、重试、完成 | PKI、网段、默认组、服务启动 | 安装日志、PKI 日志、步骤结果与恢复 |
| 仪表盘 | 当前仅展示快照 | 无 | 指标、运行摘要、证书预警、规则和告警 |

此矩阵是迁移验收清单。新页面只有在对应入口、状态限制、异常反馈和确认流程均有替代后，才允许删除旧实现。

## 自动化测试基线

命令：

```text
..\..\..\.venv\Scripts\python.exe -m pytest tests/unit -q
```

结果：`213 passed, 1 warning in 3.85s`。唯一警告为 Starlette `TestClient` 对当前 `httpx` 的弃用提示，属于既有依赖兼容提醒，没有测试失败。

新增的组件能力探针命令：

```text
..\..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_ui_component_capabilities.py -q
```

结果：`2 passed in 0.67s`。

## NiceGUI 组件能力结论

### AG Grid Community

- `modules="community"` 实际加载 `AllCommunityModule`，不需要 Enterprise 授权。
- Quartz 主题可用。
- 列排序、过滤、多行选择和 `cellClicked` 事件可配置。
- `update`、`get_selected_rows` 和 `run_grid_method` 可用于刷新、选中状态和网格方法调用。

### ECharts

- 暗色主题和透明背景可用。
- 空 `series` 可正常构建，适合作为空数据基线。
- 图表点选与组件点击事件可注册。
- `update` 与 `run_chart_method` 可用于数据刷新和实例方法调用。

## 页面构建基线

页面构建探针命令：

```text
..\..\..\.venv\Scripts\python.exe -m pytest tests/unit/test_ui_page_build_baseline.py -q
```

结果：`12 passed in 7.46s`。

空数据覆盖仪表盘、用户、用户组、对端、防火墙、证书、服务、设置和初始化页面；代表性记录覆盖用户、用户组、对端、防火墙、证书、仪表盘和服务；异常数据覆盖缺失可选字段、非法证书日期和不存在的配置目录。所有探针均通过服务端页面构建。证书页在没有配置 `pki_dir` 且直接读取真实服务时，会由既有 `CertService` 抛出“PKI 目录未配置”，这是改造前行为；后续页面状态组件必须把该异常明确展示，不能静默吞掉。

## 视觉基线

可复现命令：

```text
..\..\..\.venv\Scripts\python.exe tests\capture_ui_baseline.py
```

截图由只读示例服务生成，不写入真实 VPN 配置或业务数据：

| 文件 | 视口 | 覆盖内容 |
| --- | --- | --- |
| `evidence/baseline/dashboard-wide.png` | 1440×900 | 主导航、指标卡、摘要和告警区域 |
| `evidence/baseline/users-wide.png` | 1440×900 | 用户工具栏、代表性记录、选择与行操作 |
| `evidence/baseline/users-dialog-wide.png` | 1440×900 | 新建用户弹窗 |
| `evidence/baseline/log-dialog-wide.png` | 1440×900 | 日志弹窗和等宽日志内容 |
| `evidence/baseline/firewall-narrow.png` | 390×844 | 窄屏规则列表、批量工具栏和排序入口 |
| `evidence/baseline/settings-narrow.png` | 390×844 | 窄屏页签与设置表单 |

视觉检查确认当前宽屏信息区可用，同时记录了两个需要在新壳层中修复的既有问题：窄屏防火墙工具栏发生横向裁切；对话框受现有全局样式影响，内容尺寸和对比度不足。基线应用会在后续阶段复用，以相同数据和视口生成迁移后截图。
