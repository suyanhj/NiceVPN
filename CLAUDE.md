# NiceVPN 开发指南

面向 AI / 开发者的当前架构、边界与常用命令速查。**最后更新：2026-11-09**。

> 文档、注释和对用户说明使用中文；代码标识符使用英文。

## 企业 SSO 分支开发边界（2026-11-10）

- 开发分支：`feature/enterprise-sso-webauth`；PR #78 必须保持 Draft，用户明确验收后才允许合并 `main`。
- 详细架构与实机验收：[docs/enterprise-sso.md](docs/enterprise-sso.md)。
- `enterprise_sso_enabled` 默认关闭；登录身份只核实可信 OAuth 返回的企业 ID，**不要**要求钉钉/企业微信用户存在于 VPN 用户表。
- 证书 CN 原本所属用户/组/标签和防火墙权限保持不变；总开关仅提供 SSO 能力，组或标签编辑开关才强制 SSO。Linux 使用不属于强制 SSO 组/标签的证书，不新增证书豁免系统，也不能依据 `IV_PLAT` 自动绕过。
- 启用后由 root 限制的 Unix Management Socket 完成 `WEB_AUTH` 流程；公网仅提供 `/auth/sso` OAuth 回调，不开放管理端与 Management Socket。
- SSO 授权后设备指纹仅记录，其他证书按旧策略；多家子/分公司使用独立 CorpId 和 OAuth 应用、独立密钥、可读企业名称。AppSecret 加密主密钥随完整备份保存。
- 目前代码尚未通过真机 OpenVPN/WebAuth、钉钉/企业微信回调的端到端验收，CI mock 通过不能视为生产就绪。

## 当前架构

### 运行模型

NiceVPN 当前采用**单 Python 进程、单 systemd 服务、双 HTTP 监听端口**：

```text
openvpn-mgmt.service
        │
        └── Python main.py
              ├── NiceGUI / 管理端       0.0.0.0:8880
              └── PublicApiListener      0.0.0.0:8881
```

- `8880`：NiceGUI 管理界面。
- `8881`：独立 FastAPI/Uvicorn 公网 API，仅暴露 `/api/vpn/*` 和 `/download/{token}`。
- 两个监听器属于**同一进程**；不要重新拆回 `openvpn-mgmt-api.service`。
- 管理端口不再提供 `/api/vpn/*`。
- API 端口不提供 NiceGUI 页面、静态资源、Swagger / OpenAPI 文档。
- `ui.run(... reload=False)` 为生产运行方式。

### 数据存储

结构化业务数据统一使用 SQLite：

```text
data/nicevpn.db
```

包括：

- users
- groups
- peers
- firewall rules
- tags / user_tags / group_tags
- 其它 schema migration 管理的结构化数据

旧版 `data/users/*.json`、`data/groups/*.json`、`data/peers/*.json`、`data/firewall/*.json` **只用于启动迁移**，不是当前运行时主存储。

以下仍然是文件型数据：

- `config.json`
- PKI / CRL
- CCD
- OVPN
- device binding
- remote chain cache
- download token / bundle
- audit / runtime logs

不要为了“统一存储”把 OpenVPN 运行时必须直接访问的文件强行迁入 SQLite。

### 前端

- 管理壳：NiceGUI。
- 表格：AG Grid。
- Vue 正式运行产物：`app/ui/vue/dist/`。
- 生产部署直接使用仓库中的预编译产物，不依赖 Node/npm。
- 本地需要重新构建时使用 `./start.sh --build-ui` 或 `./build-ui.sh`。

## 活跃技术栈

- Python 3.10+；CI 基线 Python 3.14。
- NiceGUI 3.x。
- FastAPI。
- Uvicorn。
- Pydantic v2。
- python-box。
- SQLite。
- schedule。
- pytest / pytest-asyncio。
- AG Grid / Vue 前端产物。
- iptables + ipset。
- EasyRSA / OpenVPN Community。
- paramiko 等 SSH 能力。

版本以 `requirements.txt` 和实际代码为准，不在本文件硬编码次版本号。

## 目录结构

```text
main.py                    # Web 主入口；启动 8880 + 8881
cli.py                     # 独立 CLI 入口
app/
├── api/
│   ├── server.py          # PublicApiListener / 独立 API ASGI app
│   ├── vpn_ops.py         # /api/vpn/*
│   └── download.py        # /download/{token}
├── cli/                   # CLI parser / commands
├── core/                  # 配置、调度、初始化
├── models/                # Pydantic 模型
├── storage/               # SQLite、repositories、schema migration、旧 JSON 迁移
├── services/
│   ├── openvpn/
│   ├── easyrsa/
│   ├── cert/
│   ├── user/
│   ├── group/
│   ├── firewall/
│   ├── peer_instance/
│   ├── download/
│   ├── notify/
│   └── monitor/
├── ui/
│   ├── pages/
│   ├── components/
│   ├── adapters/
│   └── vue/
└── utils/
deploy/
├── systemd/
├── shell/
├── docker/
└── logrotate/
tests/unit/
README.md
cli.md
api.md
docs/overview.md
openspec/changes/
```

## 开发边界

### systemd / 启动

- NiceVPN 正式服务名只有 `openvpn-mgmt.service`。
- 不要新增第二个 API systemd unit。
- `start.sh daemon` 必须保持幂等。
- Python 选择顺序必须优先项目 `.venv/bin/python`，且不得 realpath 到 uv 的基础解释器。
- systemd unit 中应固定实际项目目录和选定 Python 解释器路径。

### Public API

- API 与管理 UI 路由集合保持隔离。
- API Basic 凭据属于运行时数据，不提交版本库。
- Public API 原生支持 HTTP 与 HTTPS（例如 HTTP IP:端口进行开发测试），不能在业务代码中强制要求 HTTPS 或域名；HTTP Basic 在明文 HTTP 下可能泄露认证信息，公网生产建议 HTTPS/TLS。
- API 相关行为同步更新 [api.md](./api.md)。

### SQLite

- 新业务代码不得重新把 users/groups/peers/firewall 作为 JSON 主存储。
- 旧 JSON 读取只允许存在于明确的迁移 / 历史文件 fallback 边界。
- schema 改动必须走版本化 migration，不能在业务代码里临时 ALTER。
- repository 层优先承载 SQL，避免页面或 service 内散落内嵌 SQL。

### ipset / iptables

- ipset 使用声明式 reconcile，不恢复旧的 create/update/delete 递增式生命周期。
- stable name 统一由 `IpsetManager.stable_name` 等正式入口生成。
- 历史 iptables chain / hook 清理代码如果仍承担升级清理职责，不应当作死代码删除。
- 防火墙页面 CRUD 与系统级 rebuild 的边界保持清晰。

### UI

- 页面表格优先复用统一 AG Grid 组件。
- 服务端分页、过滤器确认后查询、弹窗样式保持一致。
- 不为了旧测试保留已经没有运行调用方的 UI wrapper；测试应直接验证正式 adapter/service。
- 正式前端产物由 CI 构建并提交，生产机不应强制 npm/docker build。

### 日志

OpenVPN/NiceVPN 的四个 append 日志由 logrotate 管理：

- `openvpn-device-bind.log`
- `openvpn-session-audit.jsonl`
- `openvpn-connection-audit.jsonl`
- `server.log`

前三个每次写入重新打开文件，可使用 rename + create；`server.log` 为 OpenVPN 长持有句柄，使用 `copytruncate`。

历史文件轮转后立即 gzip，不使用 `delaycompress`。

发行版 `logrotate.service` 若使用 `ProtectSystem`，通过 NiceVPN 安装的 drop-in 仅开放实际 OpenVPN 日志目录的 `ReadWritePaths`。

## CLI

完整 CLI 文档见 [cli.md](./cli.md)。

常用入口：

```bash
./start.sh cli --help
python main.py cli --help
python cli.py --help
```

新自动化优先使用分层命令 `group ...`、`user ...`；`add-group`、`add-user` 仅作为历史兼容入口保留。

## 常用命令

```bash
pip install -r requirements.txt

./start.sh
sudo -E ./start.sh daemon

./start.sh cli --help

python -m pytest tests/unit -q

./start.sh --build-ui
./build-ui.sh
```

## CI / 测试

GitHub Actions 当前主要校验：

- Python 3.14 unit tests；
- OpenSpec strict validation；
- whitespace / UTF-8 / shell entrypoint；
- 需要时 Docker build；
- 需要时 Chrome browser smoke。

修改测试契约时，优先修正测试去调用当前正式接口，不要为了旧测试重新引入死代码。

## 文档职责

- [README.md](./README.md)：项目入口、安装部署、文档导航。
- [docs/overview.md](./docs/overview.md)：产品功能与模块能力。
- [cli.md](./cli.md)：CLI 命令与自动化使用。
- [api.md](./api.md)：公网 API 契约。
- [CLAUDE.md](./CLAUDE.md)：当前开发架构、技术边界。
- `openspec/changes/`：功能变更设计与历史归档。

不要在 README、CLAUDE、OpenSpec 多处复制同一大段命令或 API 契约；详细内容放到对应单一文档，其他位置只做摘要和链接。

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
