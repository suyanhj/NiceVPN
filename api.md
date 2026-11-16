# 公网 API

公网 API 由同一 NiceVPN 进程监听，默认地址为 `http://<主机>:8881`。管理界面默认使用 8880。

业务接口前缀：`/api/vpn`。API 端口不提供管理页面、Swagger 或 OpenAPI 文档。

## 认证

除下载和 SSO 浏览器回调外，所有业务接口使用 HTTP Basic。

凭据由初始化流程生成，保存在服务器的 `data/api_basic_credentials.json`。不要提交版本库或写入脚本。

公网使用时应通过 HTTPS/TLS 访问；HTTP Basic 在普通 HTTP 上不能保护密码。

## 接口

| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/api/vpn/users` | 创建用户并返回一次性下载链接 |
| POST | `/api/vpn/users/batch` | 批量创建，合并为一个 ZIP |
| POST | `/api/vpn/users/{username}/group` | 切换用户组 |
| DELETE | `/api/vpn/users/{username}` | 按用户名规则批量删除 |
| POST | `/api/vpn/users/{username}/reset-device-binding` | 按用户名规则重置设备绑定 |
| GET | `/download/{token}` | 下载 OVPN 或 ZIP |

系统未完成初始化时，业务接口返回 503。

### 创建用户

`POST /api/vpn/users`

```json
{
  "username": "alice",
  "group_name": "默认用户组",
  "count": 1
}
```

- `username`：必填，1–64 字符；不能包含斜杠。
- `group_name`：可选；省略时使用“默认用户组”，不存在则使用第一个组。
- `count`：1–500，默认 1。大于 1 时生成 `alice`、`alice_1`……
- 目标名称中只要有一个已存在，单次请求整单跳过，不重复创建。

响应字段：`created`、`usernames`、`download_url`、`message`。单用户返回 OVPN；多个用户返回 ZIP。

### 批量创建

`POST /api/vpn/users/batch`

请求体为 JSON 数组，数组长度 1–64，各项字段与单次创建相同；所有 `count` 之和不得超过 2000。

每个前缀独立处理：已存在的前缀跳过，其他前缀继续创建。所有本次新建的 OVPN 合并为一个 ZIP。全部跳过时 `download_url` 为 null。

### 切换用户组

`POST /api/vpn/users/{username}/group`

```json
{"group_name": "研发组"}
```

按目标组重新分配 CCD 虚拟 IP，并断开当前会话；客户端重连后生效。

### 删除用户

`DELETE /api/vpn/users/{username}`

路径参数按前缀匹配：只匹配用户名本身及 `前缀_数字`，例如 `alice`、`alice_1`；不匹配 `alice_backup`。无匹配用户时返回空数组。

### 重置设备绑定

`POST /api/vpn/users/{username}/reset-device-binding`

匹配规则与删除接口相同。返回 `username_prefix`、`reset_users` 和 `bindings_cleared`。

## 一次性下载

创建接口返回的 `download_url` 指向 `GET /download/{token}`：

- 不需要 Basic；令牌有效期 1 小时，成功下载后立即失效。
- 无效令牌返回 404；过期或已使用返回 410。
- 链接包含下载权限，不要公开转发或写入日志。
- 基址优先使用系统设置中的下载地址；未配置时自动使用 API 可访问地址。

## 错误

业务错误通常返回：

```json
{"detail": "错误说明"}
```

常见状态码：400 参数或业务校验失败；401 Basic 认证失败；404 资源不存在；410 下载令牌失效；500 服务端错误；503 系统未初始化或凭据不可用。

## SSO 回调

`/auth/sso/*` 是 NiceVPN 为 OpenVPN WebAuth 和管理员企业查询使用的浏览器流程，不是通用业务 API。回调不使用 Basic，依赖服务端生成的一次性事务与 OAuth state；不要手工构造或复用这些请求。
