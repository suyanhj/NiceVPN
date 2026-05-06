# 实现计划：可视化 OpenVPN 管理系统

**分支**: `001-openvpn-mgmt-system` | **日期**: 2026-03-27 | **规格**: [spec.md](spec.md)
**输入**: 功能规格说明书 `specs/001-openvpn-mgmt-system/spec.md`

## 概述

开发一套基于 Python 的可视化 OpenVPN 管理系统，通过 NiceGUI + FastAPI 提供全图形化界面，
替代繁琐的命令行操作，实现 OpenVPN 服务、用户、证书及防火墙规则的全生命周期管理，
支持批量导入用户（CSV/TXT）和批量下载配置文件（ZIP 打包）。

核心技术路径：NiceGUI 负责 UI 渲染与拖拽交互，FastAPI 提供配置文件下发等 HTTP 接口，
EasyRSA（OpenVPN 内置组件）通过 subprocess 封装处理证书操作，iptables + ipset 执行防火墙规则重建。

## 技术背景

**语言/版本**: Python 3.10+（宪法要求最低 3.8，推荐 3.10+）
**主要依赖**: NiceGUI 1.4+、FastAPI 0.110+、uvicorn、pydantic v2、python-box、requests、schedule、packaging
**存储**: JSON 文件 + 本地文本文件（无外部数据库，宪法约束）
**测试**: pytest + pytest-asyncio
**目标平台**: Linux 服务器（CentOS 7+ / Ubuntu 20.04+ / Debian 10+），需 root/sudo 权限
**项目类型**: Web 应用（单进程，NiceGUI 内嵌 FastAPI 于同一 uvicorn 实例）
**OpenVPN 版本**: **2.7.0+**（当前最新稳定版，2026-02-11 发布）；配置全部通过 `.conf` 配置文件管理
**性能目标**: 仪表盘刷新延迟 ≤30s；防火墙规则变更生效 ≤5s；用户删除后 CRL 生效 ≤60s
**约束**: 无外部数据库；所有状态以 JSON 持久化；必须在三个 Linux 发行版上运行；禁止在 OpenVPN 启动命令中使用业务参数
**规模/范围**: 单机部署；典型场景 10~100 VPN 用户；支持多 OpenVPN 实例并行管理

## 宪法核查

*门禁：Phase 0 研究前必须通过。Phase 1 设计后再次核查。*

| 原则 | 状态 | 说明 |
|---|---|---|
| 一、安全优先 | ✅ 通过 | 设备绑定（FR-006）、CRL 自动更新（FR-018）、CA 密码验证（FR-019）、一次性下载链接 1 小时过期（FR-008）均已在规格中明确定义 |
| 二、全图形化操作 | ✅ 通过 | NiceGUI 覆盖所有生命周期操作；首次运行向导强制完成配置（FR-001）；拖拽调整规则顺序（FR-014）；在线编辑前自动备份（FR-024） |
| 三、状态一致性 | ✅ 通过 | 子网冲突检测（FR-010）、活跃用户保护网段修改（FR-011）、删组前清空用户（FR-012）、规则保存前三重校验（FR-015）、JSON 备份恢复（FR-016）均已定义 |
| 四、可观测性与可审计性 | ✅ 通过 | 追加写入审计日志（FR-023）、实时仪表盘四项指标（FR-022）、7 天证书到期告警（FR-017）、服务宕机自动告警（FR-021）均已定义 |
| 五、封装复用 | ✅ 通过 | EasyRSA 通过 subprocess 封装调用（FR-027）；iptables + ipset 重建规则（FR-014/015）；systemd 或进程控制管理服务（FR-020）；无自定义 PKI 逻辑 |
| 六、语言与文档规范 | ✅ 通过 | 规格、计划、宪法均已中文撰写；代码标识符将遵循 Python 英文命名惯例；复杂逻辑注释将使用中文 |

**门禁结论**：全部通过，无需填写复杂性追踪表。

**Phase 1 设计后复查**：数据模型与接口契约设计完成后，已确认无新增原则违例（见 data-model.md 和 contracts/ 中的约束注释）。

## 项目结构

### 文档（本功能）

```text
specs/001-openvpn-mgmt-system/
├── plan.md           # 本文件（/speckit.plan 命令输出）
├── research.md       # Phase 0 输出
├── data-model.md     # Phase 1 输出
├── quickstart.md     # Phase 1 输出
├── contracts/        # Phase 1 输出
│   ├── download-api.md    # 一次性下载链接 HTTP 接口契约
│   └── dingtalk-notify.md # 钉钉机器人通知契约
└── tasks.md          # Phase 2 输出（/speckit.tasks 命令，尚未生成）
```

### 源代码（仓库根目录）

```text
app/
├── core/
│   ├── config.py          # 系统配置加载（python-box，JSON 持久化）
│   ├── init_wizard.py     # 首次运行初始化状态机
│   └── constants.py       # 全局常量（默认路径、超时时间等）
│
├── models/                # Pydantic 数据模型（校验）+ JSON 读写
│   ├── config.py          # 系统配置模型（全局网段、OpenVPN 路径）
│   ├── group.py           # 用户组模型（名称、子网、状态、成员列表）
│   ├── user.py            # 用户模型（用户名、组、密码可选、指纹、证书状态）
│   ├── device.py          # 设备绑定模型（指纹、unique-id、绑定时间）
│   ├── firewall.py        # 防火墙规则模型（归属、源/目标、权限、优先级）
│   ├── cert.py            # 证书模型（有效期、吊销状态、CRL 版本）
│   ├── download_link.py   # 下载链接模型（过期时间、是否已用）
│   └── audit.py           # 审计日志模型（操作人、时间戳、内容、结果）
│
├── services/
│   ├── openvpn/
│   │   ├── detector.py    # OpenVPN 安装检测（which/find 跨发行版）
│   │   ├── installer.py   # 自动安装最新 OpenVPN（apt/yum/dnf）
│   │   └── instance.py    # 多实例启停、状态查询、配置生成
│   ├── easyrsa/
│   │   └── wrapper.py     # EasyRSA subprocess 封装（init-pki、build-ca、
│   │                      # gen-req、sign-req、revoke、gen-crl、renew）
│   ├── firewall/
│   │   ├── ipset_mgr.py   # ipset 集合管理（创建、更新、删除）
│   │   └── iptables_mgr.py# iptables 规则重建（按优先级排序后原子替换）
│   ├── user/
│   │   ├── crud.py        # 用户 CRUD（含创建时证书生成、删除时证书吊销）
│   │   ├── ovpn_gen.py    # .ovpn 配置文件生成
│   │   └── device_bind.py # 设备指纹绑定与验证
│   ├── group/
│   │   ├── crud.py        # 组 CRUD（含子网冲突检测）
│   │   └── subnet.py      # 子网重叠检测工具函数
│   ├── download/
│   │   └── link_mgr.py    # 一次性下载链接生成、过期检测、单次消费
│   ├── cert/
│   │   └── cert_service.py # 证书生命周期管理（到期告警、吊销、续签）
│   ├── monitor/
│   │   └── service_monitor.py # 服务状态监控与自动重启
│   ├── config_editor/
│   │   └── config_backup.py # 配置文件在线编辑前自动备份
│   └── notify/
│       └── dingtalk.py    # 钉钉机器人 Webhook 推送
│
├── ui/
│   ├── pages/
│   │   ├── init_page.py   # 首次运行引导页（OpenVPN 检测/安装 + 网段配置）
│   │   ├── dashboard.py   # 仪表盘（实时指标、告警卡片）
│   │   ├── users.py       # 用户管理页
│   │   ├── groups.py      # 组管理页
│   │   ├── firewall.py    # 防火墙规则页（含拖拽排序组件）
│   │   ├── certs.py       # 证书管理页
│   │   ├── services.py    # 服务管理页（多实例控制、在线配置编辑）
│   │   └── settings.py    # 系统设置页（OpenVPN 路径、钉钉、下载 URL）
│   └── components/
│       ├── drag_list.py   # 可拖拽规则列表组件
│       ├── confirm_dialog.py # 二次确认弹窗（敏感操作）
│       └── alert_card.py  # 告警卡片组件
│
├── api/
│   └── download.py        # FastAPI 路由：GET /download/{token}（一次性文件下载）
│
└── utils/
    ├── cidr.py            # CIDR 格式校验与重叠检测
    ├── file_lock.py       # JSON 文件写入锁（防并发覆写）
    └── audit_log.py       # 审计日志追加写入工具

app/scripts/
└── device-bind.sh         # client-connect 设备绑定脚本（初始化时部署到服务器）

data/                      # 运行时 JSON 数据（.gitignore 中排除敏感内容）
├── config.json
├── groups/
├── users/
├── device_bindings/
├── firewall/
├── download_links/
└── audit/

backups/                   # 配置文件在线编辑前的自动备份副本

tests/
├── unit/                  # 单元测试（CIDR 校验、规则排序、JSON 读写、链接过期）
└── integration/           # 集成测试（EasyRSA 调用、iptables 重建、下载链接流程）

main.py                    # 入口：启动 NiceGUI + FastAPI（uvicorn）
requirements.txt
deploy/
├── systemd/openvpn-mgmt.service  # systemd 服务单元文件
└── docker/Dockerfile             # Docker 部署选项
```

**结构决策**：采用单项目布局（Python Web 应用）。NiceGUI 在同一 uvicorn 进程内托管 FastAPI，无需独立前端目录。数据以 JSON 文件按实体类型分目录存储，无外部数据库依赖，符合宪法约束。

## 复杂性追踪

> 宪法核查全部通过，无需填写此表。
