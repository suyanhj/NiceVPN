# NiceVPN 开发指南

面向 AI / 开发者的当前架构、边界与常用命令速查。**最后更新：2026-11-09**。

> 文档、注释和对用户说明使用中文；代码标识符使用英文。

## 企业 SSO 分支开发边界（2026-11-10）

- 开发分支：`feature/enterprise-sso-webauth`；PR #78 必须保持 Draft，用户明确验收后才允许合并 `main`。
- 详细架构与实机验收：[docs/enterprise-sso.md](docs/enterprise-sso.md)。
- `enterprise_sso_enabled` 默认关闭；登录身份只核实可信 OAuth 返回的企业 ID，**不要**要求钉钉/企业微信用户存在于 VPN 用户表。
- 证书 CN 原本所属用户/组/标签和防火墙权限保持不变；总开关仅提供 SSO 能力，组或标签编辑开关才强制 SSO。Linux 使用不属于强制 SSO 组/标签的证书，不新增证书豁免系统，也不能依据 `IV_PLAT` 自动绕过。
- 全局 `domains` 是一个按顺序排列的 http(s) 域名/IP:端口白名单；`download_base_url`、`enterprise_sso_public_base_url` 非空时须优先于全局值。API 只允许匹配名单内的 Host；OpenVPN SSO 初次跳转使用首项（没有 HTTP Host 可判断）。
- SSO 日志可记录企业 CorpId、应用标识及可用的员工 ID。钉钉不调用 `users/me`；组织名称按需通过官方组织基础信息接口读取，失败仅记录通用错误，不输出 Token、OAuth code、AppSecret 或完整响应体。
- 启用后由 root 限制的 Unix Management Socket 完成 `WEB_AUTH` 流程；公网仅提供 `/auth/sso` OAuth 回调，不开放管理端与 Management Socket。
- SSO 授权后设备指纹仅记录，其他证书按旧策略；统一钉钉应用只配置一套加密 OAuth 凭证，多企业使用 `enterprise_sso_allowed_corps` 白名单，支持 CorpId 和已由钉钉服务端核实的组织名称，CorpId 优先。未启用统一认证前保留旧 per-company 凭证；统一模式保存/导入后清理旧企业行。密钥主文件随完整备份保存。
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
- 对端 Mesh 路由支持 `mesh_route_visible_group_ids` 与 `mesh_route_visible_tag_ids`，两者 OR；用户直接标签 + 活动组继承标签参与匹配。
- 对端远端防火墙标签规则以逻辑 `tag_binding` 存于远端链工作副本；推送必须走 `materialize_remote_chain_plan` + `PeerService.apply_remote_peer_filter_chain_plan`，按每个对端独立的内容寻址 `hash:net` ipset 生成 set matcher，禁止回退为逐 IP 展开。
- 远端标签 ipset 使用 `nvpnpt_<peer-hash>_<content-hash>` 命名，先 staging/swap，再通过 `iptables-restore --noflush` 原子更新用户链；成功后只 GC 当前 peer 命名空间，手工原始规则引用的本 peer 集合要保留，不能触碰其他前缀。
- 对端站点链 `VPN_PEER_<hash>` 同时承载站点组网放行与远端用户策略；`role=fwd-global` 的 global_subnet ACCEPT 是兜底规则，必须位于用户/标签规则之后。拉取工作副本时过滤该系统规则，原子写回时从远端链读取并保留在末尾；站点规则刷新只能替换该兜底规则，禁止 flush 用户策略。
- 对端站点基础防火墙重下发只能更新 FORWARD 跳转、POSTROUTING SNAT 和用户链中的 `role=fwd-global` 基础放行规则；不得 flush/delete 整条 `VPN_PEER_*` 用户链，否则会误删远端防火墙规则与标签 ipset matcher。
- 标签成员变化通过后台 SSH 重建集合并刷新规则，失败持久化 `pending_sync`；首次使用时 Debian/RHEL 对端可自动安装 ipset。
- 标签删除必须同时检查用户/组成员、中心规则、Mesh 路由和远端防火墙引用；服务层与 SQLite repository 都要防止绕过 UI 直接删除。
- 组删除必须检查组内用户、group_tags 标签关联、owner_type=group 的中心防火墙规则，以及 peers.payload 中的 mesh_route_visible_group_ids；UI 当前页批量加载删除原因并禁用删除按钮，GroupService 与 SQLite repository 写事务重复校验，禁止级联删除仍在使用的 group_tags。
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

### OpenVPN / EasyRSA 初始化

- 初始化向导必须独立检测 OpenVPN 与 EasyRSA。检测到已有 OpenVPN 不代表 EasyRSA 已安装；PKI 初始化前若缺少 EasyRSA，应自动调用独立 EasyRSA 安装入口补齐。
- EasyRSA 单独安装必须复用 `openvpn-install-common.sh` 中的 release 下载/安装函数，但不能重新安装、升级或改写现有 OpenVPN 包、二进制、systemd unit。
- EasyRSA 安装成功后重新扫描并保存 `easyrsa_dir`，然后再进入 PKI 检测；已有完整 PKI 必须复用，残缺 PKI 必须拒绝覆盖。
- 安装过程在初始化页 PKI 后台任务中执行并实时输出；安装失败要明确提示并允许重试，不能静默跳过。
- 初始化检测 OpenVPN 时必须独立检查 `openvpn-server@.service` 模板单元；优先复用发行版已有 unit，缺失时安装 `deploy/systemd/openvpn-server@.service.in`，并将 `ExecStart` 指向当前检测到的 OpenVPN binary。
- systemd 补齐必须是独立脚本 `app/scripts/install/openvpn-install-systemd-fallback.sh`：只补 unit / NiceVPN 所需 drop-in，不编译、不安装、不替换 OpenVPN binary，不覆盖 `/usr/lib`、`/lib` 中发行版已有 unit。
- 自动编译安装仍使用 OpenVPN 源码自带 unit；手动编译/自定义路径则由初始化检测负责补齐缺失 unit。启动实例前还要做一次兜底检查。

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
- stable name 统一由 `IpsetManager.stable_name` 等正式入口生成；对端远端标签集合由 `remote_peer_ipset.py` 负责独立 peer 命名空间和生命周期。
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

- 全局地址覆盖优先级：自动获取 < domains < 业务自定义；域名默认 HTTP 80/HTTPS 443 且不追加 8881，裸 IP 在未写端口时补 `NICEVPN_API_PORT`（默认 8881），显式端口保持不变；浏览器自行遵循站点 3xx，不能强制 HTTPS。
- 钉钉网页登录官方 Scope 仅 `openid` 或 `openid corpid`；NiceVPN 统一认证始终请求 `openid corpid`，不调用 `users/me`，也不请求 `Contact.User.Read`。数据库中的旧个人资料标志仅为历史兼容字段。

- SSO WebAuth 受“开启认证”总开关控制；“所有人生效”打开时所有证书连接均要求 SSO，关闭时**仅对组或标签明确要求企业认证的连接触发**。其他连接经 Management 放行后继续 OpenVPN 证书、CCD 与 client-connect 流程。证书 CA / CRL / 有效期由 OpenVPN 原生验证，NiceVPN 不重复实现。
- 通过可信企业 SSO 的连接使用受限 120 秒 grant 在 client-connect 钩子里跳过设备指纹绑定，仅记录设备和会话审计；未完成 SSO 的连接继续现有设备绑定策略。客户端 IP/port 使用与 Management 事件一致的 trusted/untrusted 备用字段。
- SSO 审计改为专用文件 `data/logs/sso-auth.jsonl`（UTC 按天轮转，默认保留 7 天）；schema v9 在升级时将 SQLite 历史审计安全导出到 `data/logs/sso-auth-legacy.jsonl`（0600），然后删除废弃的审计表及索引。服务管理 → 本机实例 →「认证日志」复用有界日志窗口。
- 系统设置页说明一律使用 `render_form_section(description=...)` 或 `field_with_help` 小问号 Tooltip；不要新增多段常驻 `ui.label` 解释文案。字段帮助必须与输入控件处于同一行。

- 统一钉钉提供商 ID 为 `dingtalk_unified`，通过 `SsoProviderStore.save_unified()` 保存一份 Fernet 加密全局 Client ID/Secret。已配置时 `first_enabled()` 和 `enabled_metadata()` 优先且只返回统一应用，关闭时不自动回退到旧企业提供商。
- 允许企业白名单为 `SystemConfig.enterprise_sso_allowed_corps`，每项仅 `corp_id` / `name`，至少填写一项。同时填写只比较服务端 Token 返回的 CorpId；仅填写企业名称才查询钉钉 `contact/organizations/authInfos` 中经过服务端确认的 `orgName` 并精确匹配，无法获得组织权限或名称必须拒绝，不能信任浏览器传入的名称、CorpId。UI 使用可增删的双字段企业行。
- 系统设置页企业认证保留“开启认证 / 所有人生效”，在“统一认证”按认证方式展示钉钉或企业微信配置。钉钉使用一套全局 AppKey/SuiteKey + Secret + 企业白名单，不再逐公司设置 OAuth 密钥；支持显式导入旧版钉钉凭证及企业名单。切换认证方式只激活一个提供商并清除旧 OAuth 缓存；旧配置凭证兼容保留。
- SSO 认证回调中，`SsoFinishResult.DENIED` 对应 HTTP 403（不允许访问）、`EXPIRED` 对应 410（事务真正失效）、`UNAVAILABLE` 对应 503；旧 `finish()` 布尔 API 继续兼容。不得把明确的企业白名单拒绝显示为过期。
- v9 迁移先将旧审计表导出到权限 0600 的私有 JSONL 再删除；失败则回滚。只清理已启用统一钉钉时多余的旧 provider 行和旧待认证事务；保留加密全局凭证表及 WebAuth 事务表。

- **SSO 会话持久化（SQLite schema v12）**：`app/services/auth/sso_sessions.py` 的 `SsoSessionStore` 保存 1 小时**离线宽限期**的 OAuth 会话，活跃连接不断续期；键为 VPN 实例、证书 CN、OpenVPN Management 已验证证书的 `tls_digest_sha256_0`、OAuth 提供商 ID，同时绑定 provider App ID、钉钉 CorpId 和经服务端核实的企业名称。不要自己验签、查 CRL 或用 IV_HWADDR、HTTP 参数、只有 CN 的信息充当缓存证明。
- **新 CONNECT 复用**：在组/标签要求 SSO 的前提下，OpenVPN 已完成证书原生检查；NiceVPN 核对持久化有效期、应用是否仍启用、白名单，再为新源 IP/端口发布 client-connect 设备绑定豁免 grant，调用原 `client-auth-nt`。会话失效则重新钉钉 OAuth，不能静默绕过认证。
- **在线续期与 REAUTH**：Management 每个实例随机 1–5 秒一次 `status 3`；确认完整在线快照后，SQLite 约每 55 分钟批量原地更新现有行（比 1 小时有效期提前 5 分钟），绝不为心跳追加新行。正常 DISCONNECT 从断开时起保留 1 小时；NiceVPN 单独重启时凭 CID + 服务端连接创建时间恢复，不能凭 CN-only 恢复。REAUTH 仍检查持久化撤销；证书合法性仍由 OpenVPN 负责。
- **耗时诊断**：日志新增 OAuth 从 CONNECT 到 AUTH 放行的总时间，以及 AUTH 放行到 CLIENT:ESTABLISHED 时间。客户端隧道 TUN、CCD、路由、iptables 等耗时仍由 OpenVPN 自身处理。
- **会话安全**：持续在线可滚动续期，正常离线超过 1 小时失效；企业白名单/全局 App ID 变化阻止续期和后续 CONNECT/REAUTH 复用。复制 .ovpn 私钥可在同证书的授权有效期内复用 SSO；不要承诺设备级隔离。支持存储层显式 revoke，但未增加管理员 UI。管理状态查询失败时不得自行续期。
