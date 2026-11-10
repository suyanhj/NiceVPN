# VPN 公网管理 API 说明

> 项目入口见 [README.md](./README.md)；本机命令行管理见 [cli.md](./cli.md)。

基础路径：`/api/vpn`（**同一 NiceVPN 进程内的独立 API 监听端口**，默认 `0.0.0.0:8881`；NiceGUI 管理端仍为 `8880`，不再提供 `/api/vpn/*`）

## 启动与网络隔离

- 生产 systemd：`sudo -E ./start.sh daemon` 只安装并启动 `openvpn-mgmt` 一个服务，同时监听 `8880` 与 `8881`。用 `systemctl status openvpn-mgmt` 查看状态。
- 本地手动运行：`./start.sh` 即启动两个监听端口，无需额外进程或终端。API 监听线程不重复启动 NiceGUI、调度器和 conntrack 监听器。
- API 端口可设置 `NICEVPN_API_HOST` / `NICEVPN_API_PORT`，Web 端口可设置 `NICEVPN_UI_HOST` / `NICEVPN_UI_PORT`；systemd 从可选的 `/etc/nicevpn/listen.env` 读取。
- API 端口只开放 `/api/vpn/*` 与一次性 `/download/{token}`。**管理端 8880 不再注册 `/download/{token}`**；API 端口没有管理页面、静态资源、`/docs`、`/redoc` 或 `/openapi.json`。
- **管理端 `8880` 建议仅内网/VPN 可访问。** 独立 API 端口 `8881` 原生支持 HTTP，反向代理也可选择 HTTP 或 HTTPS；开发和内网测试可以直接使用 `http://192.168.1.10:8881`，不需要域名或 SSL 证书。注意 HTTP Basic 在 HTTP 上传输可还原的凭据，公网正式使用推荐 HTTPS/TLS，但 NiceVPN 不以 HTTPS 作为功能开启的前提。
- `download_base_url` **可选**：填写时原样使用指定下载基址（IP 或域名）；不填写时使用服务启动时自动识别的本机 IP（优先 eth0）及 API 监听端口（默认 `8881`）。生成下载 URL 不依赖访问管理后台的 Host，也不要求域名。若 API 仅在回环地址监听，则需明确配置可通过反代访问的下载地址。
- 用户管理页的「复制链接」「推送链接」以及公网业务 API 返回的 `download_url` 全部使用独立下载基址，不能再使用管理端口。
- 令牌有效期为 1 小时、只允许原子消费一次；API Access Log 关闭以避免泄露 URL Token，应用审计仅记录 Token 哈希前缀。

## 认证

除另有说明外，以下创建/切组/删除/重置接口均需 **HTTP Basic**：

| 项 | 说明 |
|----|------|
| Header | `Authorization: Basic <base64(username:password)>` |
| 用户名 | 固定为 `vpn`（见服务器 `data/api_basic_credentials.json` 内字段） |
| 密码 | 同文件内 `password`，**首次初始化后生成，勿入版本库** |

校验失败常见状态码：`401`（未带或错）、`503`（凭据文件缺失/损坏）。

系统未完成初始化向导时，业务接口一律 **`503`**（`detail` 含「尚未初始化」类说明）。

---

## 1. 创建用户并返回下载链接

**请求**

| 项目 | 值 |
|------|-----|
| 方法 / 路径 | `POST /api/vpn/users` |
| Header | `Content-Type: application/json`，以及 Basic |
| Body (JSON) | 见下表 |

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `username` | string | 是 | 用户名前缀；与控制台批量规则一致，1～64 字符，不可含 `/`、`\` |
| `group_name` | string \| null | 否 | 用户组**显示名称**（与控制台一致）。不传则选用「默认用户组」，若无此名则取列表第一个组 |
| `count` | integer | 否 | 创建数量，默认 **1**，范围 **1～500**。`1` 时仅创建 `username`；`>1` 时创建 `username`、`username_1`…`username_{count-1}` |

**说明（幂等语义）**

- 计划创建的账号中，**任一名称已存在**（非 `deleted`）：整单 **不创建**、不生成新下载链接，返回 `created: false` 与 `message` 说明已存在列表；**不要求**配置 `download_base_url`。
- 仅当所列名称**全部不存在**时，才走创建与生成链接逻辑（此时必须能解析下载基址）。

**成功响应** `200`，Body (JSON)：

| 字段 | 类型 | 说明 |
|------|------|------|
| `created` | boolean | `true` 表示本次新建了账号并生成链接；`false` 表示因已存在而跳过 |
| `usernames` | string[] | 本次请求**计划**涉及的用户名列表（与 `count` 展开一致） |
| `download_url` | string \| null | **一次性**下载地址；未新建时为 `null` |
| `message` | string \| null | 未新建时含「已存在」说明；新建时一般为 `null` |

**示例（新建成功）**

```json
{
  "created": true,
  "usernames": ["lisi", "lisi_1", "lisi_2"],
  "download_url": "https://your-host/download/xxxxx",
  "message": null
}
```

**示例（已存在，跳过）**

```json
{
  "created": false,
  "usernames": ["lisi"],
  "download_url": null,
  "message": "以下用户已存在，未重复创建: lisi"
}
```

- `count == 1` 且新建：`download_url` 指向 **单个 `.ovpn`**。
- `count > 1` 且新建：`download_url` 指向 **一个 zip**（内含各用户 `用户名.ovpn`；服务端文件名一般为 `{安全化前缀}_{UTC时间}.zip`）。

**常见错误**

| HTTP | 含义（`detail` 为 JSON 内字符串，此处摘要） |
|------|---------------------------------------------|
| 400 | 组不存在、用户名非法、无法解析下载基址（仅新建时）等 |
| 401 / 503 | 认证或系统未就绪 |
| 500 | 创建/打包/生成链接失败（看服务端日志） |

---

## 1b. 批量创建（JSON 数组，响应同 §1，合并为一个 zip）

一次请求内对**多个用户名前缀**分别按 §1 规则展开、逐项幂等；**响应 JSON 与 §1 完全相同**（`created` / `usernames` / `download_url` / `message`），客户端无需单独适配字段结构。

**请求**

| 项目 | 值 |
|------|-----|
| 方法 / 路径 | `POST /api/vpn/users/batch` |
| Header | `Content-Type: application/json`，以及 Basic |
| Body | **JSON 数组**（根节点即为 `[`…`]`），元素为对象，字段同 §1：`username`（必填）、`group_name`（可选）、`count`（可选，默认 1，每条 1～500） |

**限制**

- 数组长度：**1～64** 条。
- 所有元素的 **`count` 之和** 不得超过 **2000**（超出返回 `400`）。

**行为**

- 按数组**顺序**逐项处理；某一前缀下任一站内账号已存在则**跳过该前缀的新建**，继续后续条目。
- **只要有任意账号新建成功**：将所有本次**实际新建**用户的 `.ovpn` 打入 **同一个** zip；下载文件名为 **`vpns_YYYYMMDD_HHMMSS.zip`**（UTC，与 `build_ovpn_zip` 规则一致；重名时自动追加序号）。
- **`POST /users` 与批量**：单次创建在 `count==1` 时仍返回单文件链接、`count>1` 时返回该前缀的 zip；**仅批量接口**跨前缀合并为上述 `vpns_*.zip`。
- `usernames`：**全部**条目按请求顺序展开后的**计划用户名**列表（含因已存在而跳过的前缀所对应名称）。
- `download_url`：**至多一个**；全单均无新建时为 `null`，`created: false`，`message` 汇总跳过原因。部分跳过、部分新建时 `created: true`，`message` 可含已跳过前缀的说明（新建成功仍返回 zip 链接）。
- 某项在创建过程中发生不可恢复错误时，**整单 HTTP 失败**（已成功写入的用户不会自动回滚）。

**成功响应** `200`：形状见 §1 响应表。

**示例**

请求：

```json
[
  { "username": "alpha", "count": 2 },
  { "username": "beta", "count": 1, "group_name": "默认用户组" }
]
```

响应（全部为新建时示意）：

```json
{
  "created": true,
  "usernames": ["alpha", "alpha_1", "beta"],
  "download_url": "https://your-host/download/xxxxx",
  "message": null
}
```

其中 `download_url` 对应 zip，内含 `alpha.ovpn`、`alpha_1.ovpn`、`beta.ovpn`（仅为本次**实际新建**的文件）。

---

## 2. 切换单个用户所属组

切组后服务端会按目标组子网重新分配 CCD 固定虚拟 IP，并通过 OpenVPN management 踢掉该用户当前连接；用户重连后拿到新 IP 与新路由。证书 CN 不变，不需要重新下载 `.ovpn`。

**请求**

| 项目 | 值 |
|------|-----|
| 方法 / 路径 | `POST /api/vpn/users/{username}/group` |
| Header | `Content-Type: application/json`，以及 Basic |
| Path | `username`：单个 VPN 用户名 |
| Body (JSON) | 见下表 |

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `group_name` | string | 是 | 目标用户组**显示名称**（与控制台一致） |

**成功响应** `200`，Body (JSON)：

| 字段 | 类型 | 说明 |
|------|------|------|
| `username` | string | 已切组的用户名 |
| `old_group_id` | string | 原用户组 ID |
| `new_group_id` | string | 目标用户组 ID |
| `old_virtual_ip` | string \| null | 原 CCD 虚拟 IP |
| `new_virtual_ip` | string \| null | 新 CCD 虚拟 IP |
| `changed` | boolean | 是否实际发生切组 |

**示例**

```json
{
  "username": "lisi",
  "old_group_id": "old-gid",
  "new_group_id": "new-gid",
  "old_virtual_ip": "10.8.1.2",
  "new_virtual_ip": "10.8.2.2",
  "changed": true
}
```

**常见错误**

| HTTP | 说明 |
|------|------|
| 400 | 用户名非法、用户不存在、组不存在、目标组停用、目标组无可用 IP |
| 401 / 503 | 认证或系统未就绪 |
| 500 | 切组过程异常或 mesh 路由同步失败 |

---

## 3. 按前缀批量删除用户

路径参数中的字符串视为**用户名前缀**，匹配规则：**完全相同** 或 **`前缀_纯数字`**（如 `lisi`、`lisi_1`、`lisi_2`），与控制台批量命名一致；不匹配 `lisi_backup` 等。

**请求**

| 项目 | 值 |
|------|-----|
| 方法 / 路径 | `DELETE /api/vpn/users/{username}` |
| Header | Basic |
| Path | `username`：前缀（URL 编码，如含特殊字符） |

**成功响应** `200`，Body (JSON)：

| 字段 | 类型 | 说明 |
|------|------|------|
| `ok` | boolean | 固定 `true` |
| `username_prefix` | string | 请求使用的路径参数（去首尾空） |
| `deleted` | string[] | 实际删除的用户名列表；**无匹配用户时为空数组** |
| `message` | string \| null | 无匹配用户时说明「无需删除」；有删除时可为 `null` |

**示例**

```http
DELETE /api/vpn/users/lisi HTTP/1.1
Authorization: Basic xxx
```

```json
{
  "ok": true,
  "username_prefix": "lisi",
  "deleted": ["lisi", "lisi_1", "lisi_2"],
  "message": null
}
```

**示例（第二次删除 / 已无匹配用户，仍为 200）**

```json
{
  "ok": true,
  "username_prefix": "lisi",
  "deleted": [],
  "message": "没有符合前缀规则的活跃用户，无需删除"
}
```

**常见错误**

| HTTP | 说明 |
|------|------|
| 400 | 前缀为空或非法 |
| 401 / 503 | 认证或系统未就绪 |
| 404 | 删除过程中某一用户 `delete` 报不存在（少见） |
| 500 | 删除过程异常 |

---

## 4. 按前缀批量重置设备绑定

匹配规则与 **删除接口** 相同。

**请求**

| 项目 | 值 |
|------|-----|
| 方法 / 路径 | `POST /api/vpn/users/{username}/reset-device-binding` |
| Header | Basic |
| Path | `username`：前缀 |

**成功响应** `200`，Body (JSON)：

| 字段 | 类型 | 说明 |
|------|------|------|
| `username_prefix` | string | 路径参数 |
| `reset_users` | string[] | 匹配到并执行重置的用户名列表 |
| `bindings_cleared` | integer | 其中**原先存在绑定且已清除**的个数（无绑定则为 0） |

**示例**

```http
POST /api/vpn/users/lisi/reset-device-binding HTTP/1.1
Authorization: Basic xxx
```

```json
{
  "username_prefix": "lisi",
  "reset_users": ["lisi", "lisi_1"],
  "bindings_cleared": 1
}
```

**常见错误**

| HTTP | 说明 |
|------|------|
| 400 | 前缀为空或非法 |
| 404 | 没有匹配的活跃用户 |
| 401 / 503 | 认证或系统未就绪 |

---

## 下载链接说明（`GET /download/{token}`）

- 创建接口返回的 `download_url` 形如：`{download_base_url}/download/{token}`。
- **不需要** Basic；链接 **一次性使用**，约 **1 小时** 过期（与系统常量一致）。
- 成功时为文件流；错误常为 `404` / `410`，Body 为 `{"detail":"..."}`。

`download_base_url` 留空时使用启动时探测的本机 IP + API 端口（例如 `http://192.168.1.5:8881`）；填写后可用任意可访问的 IP 或域名作为下载地址（不能是管理端口）。无须配置域名才能使用。局域网 IP 链接只能在可路由网络内访问；公网下载应保证外部网络能到达 API 服务，并优先使用 HTTPS。

---

## 错误体格式

业务错误时 Body 多为 FastAPI 默认：

```json
{ "detail": "人类可读说明" }
```

校验类可能是 `detail` 数组（字段级错误），以实际响应为准。

---

## curl 示例

```bash
# 创建（请替换 USER:PASS 与主机）
curl -sS -u 'vpn:你的密码' -H 'Content-Type: application/json' \
  -d '{"username":"demo","group_name":"默认用户组","count":2}' \
  https://api.example.com/api/vpn/users

# 批量创建（Body 为 JSON 数组；响应与单次创建同结构，成功时 download_url 指向合并的 vpns_*.zip）
curl -sS -u 'vpn:你的密码' -H 'Content-Type: application/json' \
  -d '[{"username":"alpha","count":1},{"username":"beta","count":2,"group_name":"默认用户组"}]' \
  https://api.example.com/api/vpn/users/batch

# 批量创建（多行 JSON，便于阅读；与上一段等价）
curl -sS -u 'vpn:你的密码' -H 'Content-Type: application/json' -d @- \
  https://api.example.com/api/vpn/users/batch <<'EOF'
[
  { "username": "alpha", "count": 1 },
  { "username": "beta", "count": 2, "group_name": "默认用户组" }
]
EOF

# 切换用户 demo 到「研发组」
curl -sS -u 'vpn:你的密码' -H 'Content-Type: application/json' \
  -d '{"group_name":"研发组"}' \
  https://api.example.com/api/vpn/users/demo/group

# 删除前缀 demo
curl -sS -u 'vpn:你的密码' -X DELETE \
  https://api.example.com/api/vpn/users/demo

# 重置绑定
curl -sS -u 'vpn:你的密码' -X POST \
  https://api.example.com/api/vpn/users/demo/reset-device-binding
```

（下载链接用浏览器或 `curl -O -J` 跟随重试即可，注意单次有效。）
