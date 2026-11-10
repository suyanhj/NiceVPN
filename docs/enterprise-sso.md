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
2. 在 `系统设置 → 企业认证 → 统一钉钉身份认证` 输入全局 Client ID / Secret，并为每家允许登录的企业填入 `CorpId | 企业名称`，一行一条。**企业名称仅用于展示，必须以 CorpId 精确验证**。保存后无需每家公司单独填写 AppKey/Secret。
3. 原有多企业配置仅在未启用统一登录前保留；完成统一登录配置保存/导入后会清除旧企业应用凭证。操作前请备份数据库。已有旧配置可点击「从旧多企业配置导入」，以第一套已启用钉钉应用凭证作为全局应用，同时导入已有公司 CorpId；操作完成后必须在钉钉实际验证该应用能否为其他企业登录，不能将导入视作已完成跨企业授权。
4. 在办公系统登记 OAuth 回调 URI：
   - 钉钉：`http://服务器IP:API端口/auth/sso/dingtalk/callback`（也支持 HTTPS 域名）
   - 企业微信：`http://服务器IP:API端口/auth/sso/wecom/callback`（也支持 HTTPS 域名）
5. 在企业认证设置中输入 `enterprise_sso_public_base_url`，例如 `http://192.168.1.10:8881`、`http://vpn.example.com:8881` 或 `https://vpn.example.com`（不含路径）。无需域名或反向代理也能由 NiceVPN HTTP API 提供回调。如果有反代，也支持 HTTP 或 HTTPS 转发。**不要将 NiceGUI 管理端公开暴露。**
6. 在**组管理或标签管理的新建/编辑弹窗**开启“启用企业认证（SSO）”。总开关打开但组与标签都不要求的证书保留原有逻辑。Linux 命令行证书不要放在启用 SSO 的组或标签内，不依据 `IV_PLAT` 自动豁免。
7. 完成 POC 和 OpenVPN 客户端测试后，才在管理端打开全局总开关。保存后还需要重启 NiceVPN、重新生成并重启 OpenVPN 实例；未生效前不应把界面状态当作已执行策略。

### 运行时与设备绑定

- SSO 总开关关闭时仍使用既有 `management 127.0.0.1 7505+index`，不改变现有 OpenVPN 管理方式。
- 总开关打开时，由 NiceVPN 管理每实例的 root 限制 Unix Management Socket；已有用户踢出（kill）功能转由同一控制器执行。
- **仅当组或标签要求 SSO** 才触发钉钉/企业微信网页认证；未启用 SSO 的用户按 OpenVPN 原有连接逻辑直接继续。
- 经过可信企业认证后，NiceVPN 仅对同实例、CN、客户端公网地址与端口发布 **120 秒的后置 client-connect 授权证明**；设备绑定脚本仅记录设备信息，**跳过设备指纹绑定校验**。没有通过 SSO 的连接继续执行原有设备绑定策略。不额外实现 CA、CRL、证书有效期校验，证书验证仍由 OpenVPN 原生完成。
- Unix Management 及 SSO grant 文件不对公网开放。客户端无 WEB_AUTH 能力时，**仅命中强制 SSO 组或标签**才拒绝连接，其余证书按原有连接行为处理。
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

SSO 接入时，`office_oauth` 会记录企业/应用标识、**钉钉 Token 换取返回的 CorpId**、是否与配置匹配，以及在具备权限时返回的 `unionId/openId`。**系统设置页不展示最近认证记录**。认证事件独立追加写入 `data/logs/sso-auth.jsonl`（JSONL），包含证书 CN、企业 ID、外部用户 ID、结果与原因代码，可在 **服务管理 → 本机实例 → 认证日志** 查看，复用既有的有限字节尾部读取、手动刷新和实时查看；按 UTC 日期轮转并保留 7 天。SQLite schema v9 自动将旧 `sso_auth_audit` 认证历史导出到私有 `data/logs/sso-auth-legacy.jsonl`，再删除旧表与无效索引。文件不包含 OAuth Code、Token 或密钥。

用户 Token 换取 200、`GET /v1.0/contact/users/me` 返回 403：很可能未获 `Contact.User.Read` 权限。日志仅提取明确的服务端错误 Code 和 `requiredScopes` 以便检查，**不记录 HTTP 响应正文、Token、OAuth code 或 AppSecret**。只要用户访问凭证中的企业 CorpId 与所选企业匹配，个人资料不可用不会阻止已验证的企业登录；对应审计记录里的外部用户 ID 可能为空。若需员工 ID，可在钉钉开发者后台申请该应用的个人权限，确认用户授权后再次测试。

## 域名、IP 和外部 API 端口的规范（2026-11-10）

对外地址按 **自动获取 < 全局 domains < 业务自定义** 覆盖，优先级从左往右升高。

- `domains=xx.hj.com,192.168.1.2` => 分别为 `http://xx.hj.com` 和 `http://192.168.1.2:8881`（默认 API 端口）。
- `domains=https://xx.hj.com,192.168.1.2:80` => 分别为 `https://xx.hj.com` 和 `http://192.168.1.2:80`（显式端口不覆盖）。
- 若设置环境变量 `NICEVPN_API_PORT=18881`，没有写端口的 IP 会追加 `:18881`；域名不追加。域名未写协议默认走 HTTP 80，若服务器配置了 301/302 到 HTTPS，浏览器会按重定向切换到 HTTPS 443；**NiceVPN 不会自行强制升级为 HTTPS**。如果 HTTP 80 端口完全没有监听/代理，不能靠“允许重定向”自动进入 HTTPS，需显式选用 `https://` 或做好 80→443 转发。
- 所有业务自定义 URL 均高于全局 `domains`；未设置全局地址时使用系统自动检测的 API IP:端口（含 SSO），自动检测不到外部地址则提示配置。
- SSO 授权链接在客户端打开浏览器以前就由服务端生成，因此选择 **domains 第一项**；多域名并非自动轮询探活。登录跳转与钉钉回调还需确保对应域名在钉钉后台允许登记。

## 钉钉权限的最小授权与可选用户资料

在钉钉开发者后台 **开发配置 → 权限管理**，查询：

| 类型 | 权限点 | NiceVPN 用途 |
| --- | --- | --- |
| 基础 | `open_app_api_base` | 获取钉钉用户访问凭证；企业扫码登录所需 |
| 个人委托 | `Contact.User.Read` | 获取用户通讯录个人信息；**可选**，用于核对 UnionId/OpenId |

`openid corpid` 属于 OAuth 授权范围，要求登录时返回用户所选企业 CorpId，不是通讯录数据权限。`Contact.User.Read` 必须既在后台开通，又由**登录用户授权**才能成功读取 `GET /v1.0/contact/users/me`。不需要手机号、邮箱、部门列表、全部成员读取权限来完成当前的企业身份验证。

为避免影响未开通通讯录权限的企业，NiceVPN 的统一钉钉应用保留**可选读取员工个人资料**开关（默认关闭）。旧版每企业权限只在未迁移前保留；schema v9 在统一模式启用后清理旧企业配置：

- **关闭**：登录 Scope 只包含 `openid corpid`，仅核验用户选择的企业；不调用 `users/me`，不获取员工 ID。
- **开启**：登录 Scope **仍保持 `openid corpid`**（官方登录仅列出 `openid` 与 `openid corpid`）；服务端另行尝试调用 `users/me`，在具备相关权限与授权时记录 UnionId/OpenId；403 只记安全错误码，不泄露凭证，也不会因个人资料缺失否定已通过白名单的 CorpId。

仅在钉钉开放平台确认统一应用已具有用户资料读取授权之后才开启。用户资料接口可用性与成员信息范围取决于应用类型、用户授予的权限和企业授权情况。

## 统一钉钉登录与企业白名单（当前实现）

- 统一应用凭证加密存储于原 `sso_auth_providers` 表的 `dingtalk_unified` 记录，密钥文件仍为 `data/sso_provider_master.key`。原多企业凭证不删、不覆盖、不自动加入认证流量；未启用统一应用时仍支持历史认证链路。
- 允许企业保存于 `config.json` 的 `enterprise_sso_allowed_corps`：`[{ "corp_id": "dingAAAA", "name": "公司A" }]`。管理员可在界面逐行填写 `CorpId | 企业名称`，支持多家企业。不存在或不匹配一律拒绝；名称不参与授权。
- 钉钉网页登录固定 `scope=openid corpid`；钉钉负责提供扫码或账号登录 UI。NiceVPN 不收集钉钉账号密码；钉钉客户端是否能直接免登录由用户设备与钉钉会话决定，不在 NiceVPN 强制跳转到原生客户端。
- 登录统一应用后直接跳钉钉，不出现旧版多企业提供商选择器；服务端换回访问凭证并取得 `CorpId`，在 Management `client-auth-nt` 前校验 NiceVPN 精确企业白名单。禁止信任浏览器传入的 `CorpId`。
- 「从旧多企业配置导入」是**管理员主动操作**：复制第一套已启用的钉钉应用密钥到统一应用，并导入原启用钉钉公司名单，成功后清理无效的旧企业应用凭证。是否允许外部企业的 OAuth 授权必须在钉钉真实环境 POC 证实；导入后并不保证任意公司均能登录。
- 此版本只使用登录时返回的企业声明与本地白名单。**严格核验在职员工、读取外部企业通讯录可能需要企业管理员授权**，不应将 `openid corpid` 解释为已经获得外部企业所有通讯录权限。

## v9 认证回调与旧表清理

- 钉钉返回有效 Token，但本次 `CorpId` 不在允许企业白名单：发送 OpenVPN `client-deny`，网页显示 HTTP 403「当前企业未获授权，不允许访问 VPN」，认证日志原因码为 `enterprise_not_allowed`。
- 只有真正过期、重复使用或与已结束 VPN 会话关联的认证事务才显示 HTTP 410；认证服务故障显示 503。
- `sso_auth_audit` 表升级前按行导出到 `data/logs/sso-auth-legacy.jsonl`（0600），导出成功后再删除，失败时数据库迁移回滚。
- `sso_auth_providers` 表仍存储统一钉钉加密应用凭证，`sso_login_transactions` 表负责 WebAuth 单次使用，两表必须保留。schema v9 删除不再使用的旧审计表和旧索引；已经启用统一登录时，清理旧逐企业记录及其待认证事务。未启用统一应用则保留旧凭证直至主动迁移。
- 升级前建议备份 `data/nicevpn.db` 和 `data/sso_provider_master.key`。
