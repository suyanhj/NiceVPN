# 企业身份认证（SSO）：开发设计与验收清单

> 开发分支：`feature/enterprise-sso-webauth`；基线：`main`。未经用户验收不得合并。
> 默认完全关闭；本分支中独立的协议辅助模块**不是**已可启用的生产级 SSO。
> 跟踪：#72、#73、#74、#75、#76、#77。

## 已确认的业务规则

1. VPN 证书 CN 决定现有用户、组、标签与防火墙权限；**办公系统账号不需要存在于 NiceVPN 用户表**，也不自动创建/绑定。
2. 有效 .ovpn 文件 + 经钉钉/企业微信服务端核实属于后台允许企业的账号 + 相应证书必须通过 SSO 的策略，才允许连接；外部登录账号不改变 VPN 证书已有的网络权限。
3. 全局总开关默认关闭；开启只激活认证能力，并不强制全部 VPN 证书。仅组或关联标签勾选 SSO 的证书需要认证；未勾选的保留旧行为。Linux OpenVPN 2.x 客户端应使用不属于 SSO 组/标签的 VPN 证书。
4. **不能**根据 `IV_PLAT=linux`、GUI 版本或客户端自称身份自动跳过已要求的 SSO；同一份证书在不同系统始终执行同一组/标签策略。
5. 需要 SSO 的证书完成身份验证后，设备指纹/MAC 仅记录，不拒绝；原有不需要 SSO 的证书保留旧设备绑定行为。
6. WebAuth 缺失、不匹配企业、身份提供商不可用、超时、NiceVPN 意外退出时均不能让强制 SSO 的证书自动绕过认证。
7. NiceVPN 管理端口**不**对公网开放；复用现有 `PublicApiListener` 的第二端口用于 HTTP 或 HTTPS OAuth 授权回调，Management 仅本机受控访问。

## 关键实现约束

- 当前 server.conf 自动生成：`app/templates/openvpn/server.conf.in`；`app/services/openvpn/instance.py`。Management 为 `127.0.0.1:7505+index`；客户端连接校验由 `device-bind.sh` 执行。
- 只有控制器成功启动、预检配置正确后才能将 `management-client-auth` 和 `auth-user-pass-optional` 写入实例配置；若启动失败，不能静默回退到旧认证。开启/关闭需要谨慎安排 OpenVPN 重启，且不能导致正在运行的认证控制器竞争连接。
- Management 事件：`>CLIENT:CONNECT/REAUTH,CID,KID`，后接若干 `>CLIENT:ENV,k=v`，以 `>CLIENT:ENV,END` 结束。不能以仅凭字符串中包含 `webauth` 判断客户端真实能力，应检查服务端收到的 `IV_SSO`；即使声明该能力仍应对具体客户端实测。
- `client-pending-auth CID KID WEB_AUTH:external:http(s)://... TIMEOUT` 仅创建认证等待；认证成功后以同一实例、CID、KID 的 `client-auth-nt` 放行，否则 `client-deny`。
- 初次登录、REAUTH、断线重试、CID/KID 复用和管理端重启都要验证；认证事务不可跨实例串用。
- OAuth 由服务端生成高熵 `state`，固定供应商回调 URI，后端完成 code 换 token 与所属企业校验；HTTP 查询参数和浏览器的 CorpId 不可信。事务过期/取消/重复只能拒绝。
- 钉钉/企业微信登录接口必须采用所选应用类型支持的**企业身份验证流程**，不能把仅有 `unionId` 的个人网页登录结果误当成公司身份。
- 对外 HTTP / HTTPS 回调均不得暴露管理员 cookie 权限；不在日志中记录授权码、token、client secret 和事务凭据。HTTP 传输不加密，开发环境可直接用 IP:端口测试；真实公网部署建议启用 HTTPS。
- 必须完成单元、集成、客户端实测，任何没有实机测试的兼容性只标记「待验收」。

## 分阶段任务与完成条件

| Issue | 任务 | 验收 |
|---|---|---|
| #72 | Management WebAuth POC / CONNECT / REAUTH | 真实客户端自动登录，回调正确放行，无 SSO 时旧连接不变 |
| #73 | SQLite 历史提供商（兼容保留）/ 组标签策略 | 旧配置不丢失，统一登录启用前不影响现有连接 |
| #74 | 钉钉、企业微信 OAuth | 服务端企业验证、State 防重放、无需 VPN 身份映射 |
| #75 | FastAPI 公网回调 + pending 事务 | HTTP/HTTPS 通用、CID/KID 绑定、过期与并发可控 |
| #76 | NiceGUI 管理 UI | 总开关、单套钉钉凭证、CorpId 白名单、组/标签开关、密钥脱敏 |
| #77 | 设备仅记录 / 审计 / 安装文档 / 回归 | 权限不变、旧配置兼容、异常 fail closed |

## 兼容性验收矩阵

- OpenVPN Connect 3.7+：Windows、macOS、Android、iOS。
- OpenVPN GUI Windows（OpenVPN 2.7.x 核心）：需核实实际 GUI 11.x 小版本。
- Tunnelblick macOS：当前最新版 WebAuth 跳转与恢复连接。
- Linux OpenVPN 2.6.x+ CLI：**不作为 WebAuth 必须支持项**；若使用无网页认证客户端，确保其专用证书不在任何启用 SSO 的组或标签中。

## 开发分支策略

全部子任务按依赖顺序提交到同一 `feature/enterprise-sso-webauth` 分支，创建 **Draft PR 指向 main**。PR 不应自动合并或在用户全部功能验收前转 Ready。

## 管理端配置（开发分支，未完成实机验收）

1. 在钉钉开放平台准备 **一套统一 OAuth 应用凭证**：Client ID（企业内部应用为 AppKey，第三方企业应用为 SuiteKey）和 Client Secret，并在应用后台登记登录回调地址。
2. 在 `系统设置 → 企业认证 → 统一认证` 选择认证方式：**钉钉**填写一套 Client ID / Secret，并在“允许企业”表单逐行添加企业，每行的 **CorpId / 企业名称至少填一项，两项同填以 CorpId 为准**；**企业微信**填写企业 CorpId、应用 AgentId 与 Secret。保存后只有选中的认证方式生效。
3. 原有多企业配置仅在未启用统一登录前保留；完成统一登录配置保存/导入后会清除旧企业应用凭证。操作前请备份数据库。已有旧配置可点击「从旧多企业配置导入」，以第一套已启用钉钉应用凭证作为全局应用，同时导入已有公司 CorpId；操作完成后必须在钉钉实际验证该应用能否为其他企业登录，不能将导入视作已完成跨企业授权。
4. 在办公系统登记 OAuth 回调 URI：
   - 钉钉：`http://服务器IP:API端口/auth/sso/dingtalk/callback`（也支持 HTTPS 域名）
   - 企业微信：`http://服务器IP:API端口/auth/sso/wecom/callback`（也支持 HTTPS 域名）
5. 在企业认证设置中输入 `enterprise_sso_public_base_url`，例如 `http://192.168.1.10:8881`、`http://vpn.example.com:8881` 或 `https://vpn.example.com`（不含路径）。无需域名或反向代理也能由 NiceVPN HTTP API 提供回调。如果有反代，也支持 HTTP 或 HTTPS 转发。**不要将 NiceGUI 管理端公开暴露。**
6. 在企业认证设置中打开“开启认证”；需要所有 VPN 证书使用企业登录时，再打开“所有人生效”。**不需要全员认证**时，关闭“所有人生效”，在组管理或标签管理的弹窗中对目标组/标签开启 SSO。没有 WEB_AUTH 能力的客户端不要纳入必须 SSO 的范围，不依据 `IV_PLAT` 自动豁免。
7. 完成 POC 和 OpenVPN 客户端测试后，才在管理端打开全局总开关。保存后还需要重启 NiceVPN、重新生成并重启 OpenVPN 实例；未生效前不应把界面状态当作已执行策略。

### 运行时与设备绑定

- SSO 总开关关闭时仍使用既有 `management 127.0.0.1 7505+index`，不改变现有 OpenVPN 管理方式。
- 总开关打开时，由 NiceVPN 管理每实例的 root 限制 Unix Management Socket；已有用户踢出（kill）功能转由同一控制器执行。
- 总开关打开后，“所有人生效”会对全部证书连接触发钉钉/企业微信网页认证；否则仅对要求 SSO 的组/标签生效，其他证书按 OpenVPN 原有连接逻辑继续。
- 经过可信企业认证后，NiceVPN 仅对同实例、CN、客户端公网地址与端口发布 **120 秒的后置 client-connect 授权证明**；设备绑定脚本仅记录设备信息，**跳过设备指纹绑定校验**。没有通过 SSO 的连接继续执行原有设备绑定策略。不额外实现 CA、CRL、证书有效期校验，证书验证仍由 OpenVPN 原生完成。
- Unix Management 及 SSO grant 文件不对公网开放。客户端无 WEB_AUTH 能力时，命中全员认证或强制 SSO 组/标签均拒绝连接，其余证书按原有连接行为处理。
- 登录事务最长 180 秒，state 和 WebAuth Token 原文不会入库；审计不含 OAuth code、access token、AppSecret。
- 对于 OpenVPN 2.x CLI 等无浏览器界面的客户端，管理员可使用不要求 SSO 的组和标签管理其专用证书；如果同一证书被包含在强制 SSO 的组或标签中，则不能因客户端是 Linux 而跳过。
- SSO 加密主密钥位于 `data/sso_provider_master.key`，文件权限须为 `0600`，完整备份中需随 SQLite 一同保存；恢复时不可缺失该文件。

### POC 与用户验收前不得省略

- **待实机验证**：OpenVPN 2.6/2.7 是否按当前配置正确打开 Unix Management Socket、正确上报 `IV_SSO`、`trusted_ip`/`trusted_port`，以及 `client-connect` 执行顺序。还须确认全局开启但未勾选组/标签时旧证书仍正常连接。
- **待实机验证**：OpenVPN Connect 3.7+、Windows OpenVPN GUI、Tunnelblick 的授权浏览器打开、取消、超时以及扫码后自动继续连接。
- **待真实企业应用测试**：钉钉/企业微信企业内部应用、授权回调、企业身份匹配及权限撤销。
- **待实机验证**：NiceVPN / OpenVPN 服务重启、多个实例同时认证、已有运维踢出功能以及设备指纹脚本记录模式。
- CI 仅运行不依赖真实办公系统或 OpenVPN 服务的单测；**CI 通过不代表 SSO 可直接投入生产**。

## HTTP 和 HTTPS 兼容（开发、内网与生产）

- NiceVPN 本身**不强制 HTTPS、不强制域名、不强制购买或安装 SSL 证书**。HTTP 与 HTTPS 均支持 IP、域名、可选端口。
- 示例：`http://127.0.0.1:8881`、`http://192.168.1.10:8881`、`http://10.10.0.8:8881`、`http://vpn.example.com`、`https://vpn.example.com`。
- 开启 SSO 所填写的认证基址必须完整包含 `http://` 或 `https://`；可在内网或开发环境直接填写 IP:API 端口，不能填写路径、URL 用户名密码、查询参数。
- WebAuth 等待链接和钉钉/企业微信的授权 `redirect_uri` 保留配置里的原协议，不做 HTTP → HTTPS 强制跳转。
- 钉钉、企业微信官方授权/API 服务地址仍为**官方提供的 HTTPS 地址**（这是供应商 API 入口，不是 NiceVPN 回调 HTTPS 限制）。供应商平台可能要求登记可信回调地址、域名或 HTTPS；NiceVPN 不阻止 HTTP 配置，但不能保证第三方平台接受所有测试地址。可用 Mock OAuth 完成本地 HTTP 链路测试。
- HTTP 会明文传输 WebAuth 票据和授权码；建议仅用于可信的开发/内网测试，公网真实员工登录更适合 HTTPS，但不强制。

## 全局访问地址池（domains）

在 **系统设置 → 访问地址** 填写多个入口，例如：

```text
xx.hj.com,192.168.1.1:80,https://sso.example.com
```

也可以每行一个地址。支持裸域名、IP:端口、完整的 `http://` 与 `https://` URL。
裸域名/IP 默认采用 HTTP；多项按输入顺序保存。

地址优先级：

- **API 下载链接**：`下载配置 → 自定义下载基础 URL` 优先；留空时，若请求来源 Host 命中 `domains` 地址池则使用该条目，否则使用池中第一项；池为空时沿用原来的本机 API IP:端口自动探测。
- **企业认证**：`企业认证 → 认证地址` 自定义值优先；留空时采用 `domains` 第一项。由于 OpenVPN 在浏览器发起请求之前就要生成 WebAuth URL，无法根据未来浏览器的 Host 自动选择其他地址。要使用其他域名，调整列表顺序或填写自定义覆盖。
- **统一钉钉 OAuth** 只登记一套全局应用凭证（内部 AppKey 或第三方应用 SuiteKey），允许的企业由 NiceVPN 本地 `enterprise_sso_allowed_corps` 配置管理；旧的企业级独立应用仅在未切换统一登录前保留；新配置启用后自动清理。全局地址池不修改钉钉开发者后台登记的重定向 URL。
- 不对候选地址逐个发起外网探测；不允许未经 `domains` 白名单验证的 Host 充当对外链接，避免 Host 头注入。域名必须真实解析并在浏览器可访问。

修改地址池后，API 下载可在下次请求直接读取配置；运行中的 SSO 控制器使用启动时的地址快照，需要重启 NiceVPN 后才采用新的默认值。

## DingTalk 身份核对日志

SSO 接入时，`office_oauth` 会记录企业/应用标识和**钉钉 Token 换取返回的 CorpId**。钉钉不再额外读取个人资料，外部用户 ID 留空；企业微信仍使用官方必需的员工身份接口确认内部成员。**系统设置页不展示最近认证记录**。认证事件独立追加写入 `data/logs/sso-auth.jsonl`（JSONL），包含证书 CN、企业 ID、外部用户 ID（如提供商返回）、结果与原因代码，可在 **服务管理 → 本机实例 → 认证日志** 查看，按 UTC 日期轮转并保留 7 天。SQLite schema v9 自动将旧 `sso_auth_audit` 认证历史导出到私有 `data/logs/sso-auth-legacy.jsonl`，再删除旧表与无效索引。文件不包含 OAuth Code、Token 或密钥。

钉钉统一认证不请求个人资料 `GET /v1.0/contact/users/me`，因此不需要 `Contact.User.Read`。仅在配置“企业名称、无 CorpId”的名单时，额外调用组织基础信息接口。日志不会记录 HTTP 响应正文、Token、OAuth code 或 AppSecret。

## 域名、IP 和外部 API 端口的规范（2026-11-10）

对外地址按 **自动获取 < 全局 domains < 业务自定义** 覆盖，优先级从左往右升高。

- `domains=xx.hj.com,192.168.1.2` => 分别为 `http://xx.hj.com` 和 `http://192.168.1.2:8881`（默认 API 端口）。
- `domains=https://xx.hj.com,192.168.1.2:80` => 分别为 `https://xx.hj.com` 和 `http://192.168.1.2:80`（显式端口不覆盖）。
- 若设置环境变量 `NICEVPN_API_PORT=18881`，没有写端口的 IP 会追加 `:18881`；域名不追加。域名未写协议默认走 HTTP 80，若服务器配置了 301/302 到 HTTPS，浏览器会按重定向切换到 HTTPS 443；**NiceVPN 不会自行强制升级为 HTTPS**。如果 HTTP 80 端口完全没有监听/代理，不能靠“允许重定向”自动进入 HTTPS，需显式选用 `https://` 或做好 80→443 转发。
- 所有业务自定义 URL 均高于全局 `domains`；未设置全局地址时使用系统自动检测的 API IP:端口（含 SSO），自动检测不到外部地址则提示配置。
- SSO 授权链接在客户端打开浏览器以前就由服务端生成，因此选择 **domains 第一项**；多域名并非自动轮询探活。登录跳转与钉钉回调还需确保对应域名在钉钉后台允许登记。

## 钉钉权限的最小授权

在钉钉开发者后台 **开发配置 → 权限管理**，查询：

| 类型 | 权限点 | NiceVPN 用途 |
| --- | --- | --- |
| 基础 | `open_app_api_base` | 获取钉钉用户访问凭证；企业扫码登录所需 |

`openid corpid` 属于 OAuth 登录授权范围，企业身份由钉钉返回的 CorpId 与 NiceVPN 白名单匹配。**统一认证不提供读取个人资料开关**，不调用 `users/me`，不需要 `Contact.User.Read` 或手机号、邮箱、部门等个人通讯录权限。仅使用名称匹配时须在钉钉开放平台为应用授权**通讯录组织基础信息读权限**，才能通过 `GET /v1.0/contact/organizations/authInfos?targetCorpId=...` 读取可信的 `orgName`。接口无权限、失败或返回空名称均拒绝名称匹配；部分跨企业应用可能无法取得其他企业名称，应改用 CorpId。数据库内旧个人资料字段为历史兼容而保留。

## 统一钉钉登录与企业白名单（当前实现）

- 统一应用凭证加密存储于原 `sso_auth_providers` 表的 `dingtalk_unified` 记录，密钥文件仍为 `data/sso_provider_master.key`。原多企业凭证不删、不覆盖、不自动加入认证流量；未启用统一应用时仍支持历史认证链路。
- 允许企业保存于 `config.json` 的 `enterprise_sso_allowed_corps`：`[{ "corp_id": "dingAAAA", "name": "公司A" }, { "corp_id": "", "name": "公司B" }]`。界面为可添加/删除的双字段企业行。每项至少填一个：有 CorpId 时只按钉钉 Token 中的 CorpId 判断，名称不影响结果；没有 CorpId 时才调用钉钉组织信息 API 并与已验证的名称**完全相等**才允许。不支持模糊匹配；企业名称可能重名、改名，安全性及稳定性不及 CorpId。
- 钉钉网页登录固定 `scope=openid corpid`；钉钉负责提供扫码或账号登录 UI。NiceVPN 不收集钉钉账号密码；钉钉客户端是否能直接免登录由用户设备与钉钉会话决定，不在 NiceVPN 强制跳转到原生客户端。
- 登录统一应用后直接跳钉钉，不出现旧版多企业提供商选择器；服务端换回访问凭证并取得 `CorpId`，必要时使用它查询并验证官方组织名称，再在 Management `client-auth-nt` 前校验 NiceVPN 精确企业白名单。禁止信任浏览器传入的 `CorpId` 或企业名称。
- 「从旧多企业配置导入」是**管理员主动操作**：复制第一套已启用的钉钉应用密钥到统一应用，并导入原启用钉钉公司名单，成功后清理无效的旧企业应用凭证。是否允许外部企业的 OAuth 授权必须在钉钉真实环境 POC 证实；导入后并不保证任意公司均能登录。
- 此版本只使用登录时返回的企业声明与本地白名单。**严格核验在职员工、读取外部企业通讯录可能需要企业管理员授权**，不应将 `openid corpid` 解释为已经获得外部企业所有通讯录权限。

## v9 认证回调与旧表清理

- 钉钉返回有效 Token，但本次 `CorpId` 不在允许企业白名单：发送 OpenVPN `client-deny`，网页显示 HTTP 403「当前企业未获授权，不允许访问 VPN」，认证日志原因码为 `enterprise_not_allowed`。
- 只有真正过期、重复使用或与已结束 VPN 会话关联的认证事务才显示 HTTP 410；认证服务故障显示 503。
- `sso_auth_audit` 表升级前按行导出到 `data/logs/sso-auth-legacy.jsonl`（0600），导出成功后再删除，失败时数据库迁移回滚。
- `sso_auth_providers` 表仍存储统一钉钉加密应用凭证，`sso_login_transactions` 表负责 WebAuth 单次使用，两表必须保留。schema v9 删除不再使用的旧审计表和旧索引；已经启用统一登录时，清理旧逐企业记录及其待认证事务。未启用统一应用则保留旧凭证直至主动迁移。
- 升级前建议备份 `data/nicevpn.db` 和 `data/sso_provider_master.key`。

## 持久化 SSO 在线续期与自动重连（schema v12）

OpenVPN 负责 CA、CRL、证书有效期、TLS 握手、重启和客户端自动重连。NiceVPN **不再次验证客户端证书**，仅使用可信 Management CONNECT/REAUTH 环境变量 `tls_digest_sha256_0`，将已经通过 OAuth 的会话与同一份证书绑定；该指纹由 OpenVPN 输出，不来自浏览器或客户端自定义 IV_* 字段。

- **首次登录**：全员认证或组/标签要求 SSO → OpenVPN 证书验证 → 办公系统 OAuth 身份校验 → Management `client-auth-nt` → SQLite `sso_auth_sessions` 为已验证证书创建一条 **1 小时**有效的会话。同一份证书的公网 IP、源端口、CID/KID 改变都不阻止有效期内复用。
- **在线续期**：每个 OpenVPN 实例的 Unix Management 连接随机间隔 1–5 秒发送一次 `status 3`（**每实例一次全量查询，不按客户端逐个查询**）；只把完整响应中能够以 CID、CN、连接创建时间匹配到已认证 SSO 会话的客户端视为在线。每条在线会话约每 **55 分钟**批量原地 `UPDATE last_used_at/expires_at`，预留 5 分钟余量避免整点时间竞态，不添加心跳行或审计行。正常 DISCONNECT 时从断开时起重置 1 小时离线宽限期；异常断线未收到 DISCONNECT 时，从最近一次已确认在线的续期时间起最多保留 1 小时。定时删除过期行以防表膨胀。查询异常时不续期、不据此踢人。
- **NiceVPN 单独重启时**：原 OpenVPN 通道尚在线，管理进程从 SQLite 已记录的 CID + OpenVPN `Connected Since (time_t)` 绑定恢复可信会话，继续原地续期。仅有相同 CN 或只有相同 CID 均不能恢复认证身份；真正的 OpenVPN 重启则通过新 CONNECT 和可信证书指纹复用宽限期内授权。
- **重新 CONNECT**（OpenVPN 重启、网络中断或客户端主动断开后重连）：NiceVPN 使用来自本次 Management 事件的实例、CN、SHA256 证书指纹查找 SSO 会话，再核对已启用 OAuth 应用与企业白名单；命中即重新发布短期设备指纹豁免授权，直接向 OpenVPN 发送 `client-auth-nt`，无需浏览器重登。原有 OpenVPN client-connect、CCD、IP 分配和防火墙逻辑完全不变。
- **TLS REAUTH**：当前活跃连接仍要检查持久化授权记录与撤销状态；长期持续在线的记录因在线确认而续期，不会仅因首次 OAuth 过去 1 小时就要求重新扫码。
- **缓存失败/过期**：继续原来的钉钉 WebAuth，不退化为仅凭 CN 放行。没有 `tls_digest_sha256_0` 或指纹格式无效时不创建或复用跨重启缓存；当前活跃 TLS 连接仍沿用已授权的内存会话。
- **安全边界**：管理员禁用 SSO 组标签策略后回到原连接流程；企业从白名单删除、全局 OAuth App ID 切换、缓存过期或管理员显式删除会话时，不再复用缓存；不支持 WEB_AUTH 的全新连接仍不能绕过强制 SSO。仅操作 SQLite 撤销方法不会踢掉已连接隧道，用户页的“重置企业认证”则同时发送 Management 踢线指令。
- **安全权衡**：同一份 .ovpn 客户端证书+私钥若被复制到另一台设备，在会话有效期间也可能复用 SSO；在线持续续期意味着只要持续连接，授权仍可超过 1 小时。因为企业认证成功后按产品需求跳过设备绑定，所以必须严格保护私钥；如需区分物理设备，应另外设计设备级令牌，而不是在 NiceVPN 再做 CA 证书校验。

Schema v10 创建 `sso_auth_sessions` 表，v11 给原表增加 `active_client_id` / `active_connected_at`，v12 再增加 `enterprise_name` 列，以便重连时安全复核名称名单；旧记录的名称为空，只能继续按 CorpId 白名单复用。会话仍以 `(instance_id, common_name, cert_fingerprint, provider_id)` 为唯一主键，在线刷新不增加行数。NiceVPN 服务重启只清理内存状态，不清理 SQLite 缓存。生产部署后仍须实测不同 OpenVPN 版本的 Management `status 3` 输出、组织权限和长期运行稳定性。

### 管理员重置认证会话

在用户管理页点击单人操作中的 **重置企业认证** 图标，或勾选多位用户后点击工具栏 **批量重置企业认证**。确认后针对每个账号：删除全部 VPN 实例与证书指纹对应的持久认证缓存；把未完成的网页登录事务标记为拒绝；清理 NiceVPN 进程中的认证状态；向 OpenVPN Management 发送 `kill <common_name>`。该操作不删除用户、证书或组配置。

- **在线用户**：当前连接会被要求断开；支持自动重连的客户端下次 CONNECT 应进入全新的 OAuth 登录流程。界面仅保证发出 Management 命令，具体踢线结果需以 OpenVPN 状态为准。
- **离线用户**：没有可主动唤醒的 VPN 客户端，但旧缓存已撤销，因此客户端下次主动连接时必须重新进行 OAuth。即使此前留下多个实例的缓存，也会一并撤销。
- **重复重置**：没有缓存时仍属于正常操作，便于管理员确认旧会话不会再被复用；已经完成的历史审计记录不会被删除。

### 公网 API 的 Basic 认证边界

独立公网 API 端口挂载 `/api/vpn/...`、`/download/{token}` 和 `/auth/sso/...`，**不挂载 NiceGUI 管理端页面**。`/api/vpn/...` 的增删改操作必须通过 HTTP Basic；一次性下载链接用不可预测、仅一次有效的令牌授权；浏览器访问的 `/auth/sso/start` 和第三方 OAuth 回调**不使用 HTTP Basic**，否则浏览器自动跳转及办公平台回调会被 Basic 挑战阻断。

SSO 的授权边界由短期一次性 `t/state`、服务端保存的连接实例/CID/KID、钉钉/企业微信官方令牌校验、企业白名单及原生 OpenVPN 证书校验构成；无正确事务令牌的公网请求不能单独放行 VPN。公网部署应使用 HTTPS，限制公网防火墙只放行必要 API 端口，禁止把管理员端口或 Management Socket 暴露到公网；勿在反向代理日志中记录 OAuth 授权码或 URL 一次性令牌。

### NiceVPN 可观测耗时

在 App 日志区分 `SSO OAuth flow connect_to_auth_allow_ms`（含用户浏览器操作时间）和 `SSO OpenVPN establishment auth_allow_to_established_ms`（OpenVPN Management 接到 AUTH 允许之后到 ESTABLISHED 的时间）。后者只用于判断瓶颈在 OpenVPN 后续连接阶段，不改变隧道流程，也不会让 NiceVPN 接管 TLS、路由或网络协商。
