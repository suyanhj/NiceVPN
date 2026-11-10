# 企业身份认证（SSO）：开发设计与验收清单

> 开发分支：`feature/enterprise-sso-webauth`；基线：`main`。未经用户验收不得合并。
> 默认完全关闭；本分支中独立的协议辅助模块**不是**已可启用的生产级 SSO。
> 跟踪：#72、#73、#74、#75、#76、#77。

## 已确认的业务规则

1. VPN 证书 CN 决定现有用户、组、标签与防火墙权限；**办公系统账号不需要存在于 NiceVPN 用户表**，也不自动创建/绑定。
2. 有效 .ovpn 文件 + 经钉钉/企业微信服务端核实属于后台允许企业的账号 + 相应证书必须通过 SSO 的策略，才允许连接；外部登录账号不改变 VPN 证书已有的网络权限。
3. 全局总开关默认关；组/标签可强制 SSO；证书级显式豁免优先，用于不支持网页授权的 Linux OpenVPN 2.x 等场景。
4. **不能**根据 `IV_PLAT=linux`、GUI 版本或客户端自称身份自动免认证；同一份证书在不同系统具有同样的授权策略。
5. 需要 SSO 的证书完成身份验证后，设备指纹/MAC 仅记录，不拒绝；原有不需要 SSO 的证书保留旧设备绑定行为。
6. WebAuth 缺失、不匹配企业、身份提供商不可用、超时、NiceVPN 意外退出时均不能让强制 SSO 的证书自动绕过认证。
7. NiceVPN 管理端口**不**对公网开放；复用现有 `PublicApiListener` 的第二端口用于 HTTPS OAuth 授权回调，Management 仅本机受控访问。

## 关键实现约束

- 当前 server.conf 自动生成：`app/templates/openvpn/server.conf.in`；`app/services/openvpn/instance.py`。Management 为 `127.0.0.1:7505+index`；客户端连接校验由 `device-bind.sh` 执行。
- 只有控制器成功启动、预检配置正确后才能将 `management-client-auth` 和 `auth-user-pass-optional` 写入实例配置；若启动失败，不能静默回退到旧认证。开启/关闭需要谨慎安排 OpenVPN 重启，且不能导致正在运行的认证控制器竞争连接。
- Management 事件：`>CLIENT:CONNECT/REAUTH,CID,KID`，后接若干 `>CLIENT:ENV,k=v`，以 `>CLIENT:ENV,END` 结束。不能以仅凭字符串中包含 `webauth` 判断客户端真实能力，应检查服务端收到的 `IV_SSO`；即使声明该能力仍应对具体客户端实测。
- `client-pending-auth CID KID WEB_AUTH:external:https://... TIMEOUT` 仅创建认证等待；认证成功后以同一实例、CID、KID 的 `client-auth-nt` 放行，否则 `client-deny`。
- 初次登录、REAUTH、断线重试、CID/KID 复用和管理端重启都要验证；认证事务不可跨实例串用。
- OAuth 由服务端生成高熵 `state`，固定供应商回调 URI，后端完成 code 换 token 与所属企业校验；HTTP 查询参数和浏览器的 CorpId 不可信。事务过期/取消/重复只能拒绝。
- 钉钉/企业微信登录接口必须采用所选应用类型支持的**企业身份验证流程**，不能把仅有 `unionId` 的个人网页登录结果误当成公司身份。
- 公网 HTTPS 入口无管理员 cookie 权限；隐藏授权码、token、client secret 和事务凭据，不得打印到 Uvicorn access log / audit log。
- 必须完成单元、集成、客户端实测，任何没有实机测试的兼容性只标记「待验收」。

## 分阶段任务与完成条件

| Issue | 任务 | 验收 |
|---|---|---|
| #72 | Management WebAuth POC / CONNECT / REAUTH | 真实客户端自动登录，回调正确放行，无 SSO 时旧连接不变 |
| #73 | SQLite schema / 提供商 / 组标签策略 / 证书豁免 | 自动迁移、冲突决策单测、默认关闭 |
| #74 | 钉钉、企业微信 OAuth | 服务端企业验证、State 防重放、无需 VPN 身份映射 |
| #75 | FastAPI 公网回调 + pending 事务 | HTTPS、CID/KID 绑定、过期与并发可控 |
| #76 | NiceGUI 管理 UI | 总开关、企业配置、组/标签、证书豁免、密钥脱敏 |
| #77 | 设备仅记录 / 审计 / 安装文档 / 回归 | 权限不变、旧配置兼容、异常 fail closed |

## 兼容性验收矩阵

- OpenVPN Connect 3.7+：Windows、macOS、Android、iOS。
- OpenVPN GUI Windows（OpenVPN 2.7.x 核心）：需核实实际 GUI 11.x 小版本。
- Tunnelblick macOS：当前最新版 WebAuth 跳转与恢复连接。
- Linux OpenVPN 2.6.x+ CLI：**不作为 WebAuth 必须支持项**；管理员为特定证书显式 SSO 豁免，避免被平台伪装绕过。

## 开发分支策略

全部子任务按依赖顺序提交到同一 `feature/enterprise-sso-webauth` 分支，创建 **Draft PR 指向 main**。PR 不应自动合并或在用户全部功能验收前转 Ready。
