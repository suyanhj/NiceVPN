# 数据模型：可视化 OpenVPN 管理系统

**功能分支**: `001-openvpn-mgmt-system`
**生成日期**: 2026-03-27
**来源**: spec.md 关键实体 + research.md 技术决策

---

## 实体总览

```
SystemConfig
    └── 1:N → Group
                └── 1:N → User
                            └── 1:1 → DeviceBinding
                            └── 1:1 → Certificate
                            └── 1:N → DownloadLink
Group
    └── 1:N → FirewallRule
User
    └── 1:N → FirewallRule（用户级规则）
AuditLog（独立，无外键关联）
```

---

## 实体详细定义

### SystemConfig（系统配置）

**文件路径**: `data/config.json`（单文件）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `initialized` | bool | 必填 | 首次运行引导是否已完成 |
| `global_subnet` | string | CIDR 格式，如 `10.8.0.0/16` | 全局 VPN 网段，初始化后不可修改 |
| `openvpn_bin` | string | 有效可执行文件路径 | OpenVPN 可执行文件路径（支持自定义） |
| `easyrsa_dir` | string | 有效目录路径 | EasyRSA 脚本目录（从 OpenVPN 安装目录自动定位） |
| `pki_dir` | string | 有效目录路径 | EasyRSA PKI 目录（CA 证书、用户证书存放位置） |
| `dingtalk_webhook` | string \| null | 可选，有效 URL | 钉钉机器人 Webhook 地址 |
| `download_base_url` | string | 有效 URL，如 `http://192.168.1.1:8080` | 下载链接的基础 URL |
| `created_at` | string | ISO 8601 | 系统初始化时间 |
| `updated_at` | string | ISO 8601 | 最后更新时间 |

**状态机**:
```
未初始化 → [完成引导] → 已初始化（全局网段 + OpenVPN 路径已配置）
```

---

### Group（用户组）

**文件路径**: `data/groups/{group_id}.json`（每组一个文件）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | string | UUID v4，不可变 | 唯一标识符 |
| `name` | string | 1~64 字符，项目内唯一 | 组显示名称 |
| `subnet` | string | CIDR，必须是全局网段的子网，且与其他组不重叠 | 绑定子网段 |
| `status` | enum | `active` \| `disabled` | 组状态（禁用时组内用户无法连接） |
| `user_count` | int | ≥0，只读（由系统维护） | 组内用户数量（用于删除前校验） |
| `firewall_rule_ids` | list[string] | 引用 FirewallRule.id | 绑定到该组的防火墙规则 ID 列表（有序） |
| `created_at` | string | ISO 8601 | 创建时间 |
| `updated_at` | string | ISO 8601 | 最后更新时间 |

**校验规则**:
- 创建/修改时：`subnet` 必须通过子网重叠检测（与 `global_subnet` 和所有其他组的 `subnet` 均不重叠）
- 修改 `subnet` 时：`user_count` 必须为 0
- 删除时：`user_count` 必须为 0

**状态机**:
```
active ↔ disabled
（active 状态下组内用户可连接；disabled 状态下防火墙规则仍存在但不生效）
```

---

### User（用户）

**文件路径**: `data/users/{username}.json`（以用户名为文件名）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `username` | string | 1~64 字符，字母数字+下划线，全局唯一 | 用户名（同时作为证书 CN） |
| `group_id` | string | 必填，引用 Group.id | 所属组 ID |
| `password_enabled` | bool | 默认 false | 是否启用账号密码认证 |
| `password_hash` | string \| null | bcrypt 哈希，password_enabled=true 时必填 | 密码哈希 |
| `status` | enum | `active` \| `disabled` \| `deleted` | 用户状态 |
| `ovpn_file_path` | string \| null | 有效文件路径 | 生成的 `.ovpn` 配置文件绝对路径 |
| `device_binding_id` | string \| null | 引用 DeviceBinding.id | 当前绑定设备的 ID（null 表示未绑定） |
| `cert_serial` | string \| null | 证书序列号 | 关联证书序列号 |
| `firewall_rule_ids` | list[string] | 引用 FirewallRule.id | 用户级防火墙规则 ID 列表（有序，覆盖组规则） |
| `created_at` | string | ISO 8601 | 创建时间 |
| `updated_at` | string | ISO 8601 | 最后更新时间 |

**状态机**:
```
active → [禁用] → disabled → [启用] → active
active / disabled → [删除] → deleted（触发证书吊销 + CRL 更新）
```

**删除流程（不可逆）**:
1. 吊销证书（EasyRSA `revoke` + `gen-crl`）
2. 更新 OpenVPN 服务端 CRL 文件
3. 删除设备绑定记录
4. 删除 `.ovpn` 配置文件
5. 将用户状态置为 `deleted`，JSON 文件保留用于审计

---

### DeviceBinding（设备绑定）

**文件路径**: `data/device_bindings/{binding_id}.json`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | string | UUID v4，不可变 | 唯一标识符 |
| `username` | string | 引用 User.username | 绑定的用户名 |
| `fingerprint` | string | SHA-256 哈希，非空 | 硬件指纹（machine-id 或其他标识符的哈希） |
| `fingerprint_source` | enum | `machine-id` \| `mac` \| `uuid` \| `manual` | 指纹来源类型 |
| `openvpn_unique_id` | string \| null | OpenVPN unique-id | OpenVPN 连接层唯一 ID（首次连接后填入） |
| `bound_at` | string | ISO 8601 | 绑定时间（首次成功连接时） |
| `last_seen_at` | string \| null | ISO 8601 | 最后一次连接时间 |

**核心约束**: 每个 `username` 在任意时刻只能有一条 `active` 绑定记录。
管理员解绑后旧记录标记为历史，新设备连接后创建新绑定。

---

### FirewallRule（防火墙规则）

**文件路径**: `data/firewall/rules.json`（按实例分组，key 为实例名）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | string | UUID v4，不可变 | 唯一标识符 |
| `owner_type` | enum | `group` \| `user` | 规则归属类型 |
| `owner_id` | string | 引用 Group.id 或 User.username | 规则归属实体 |
| `instance` | string | 引用 OpenVPN 实例名称 | 适用的 OpenVPN 实例 |
| `action` | enum | `accept` \| `drop` \| `reject` | 访问权限 |
| `source_subnet` | string \| null | CIDR 或 null（匹配所有） | 源网段 |
| `dest_ip` | string \| null | IPv4 地址或 CIDR 或 null | 目标 IP |
| `dest_port` | string \| null | 端口号(如`80`)、范围(`1024:65535`)或 null | 目标端口 |
| `protocol` | enum | `tcp` \| `udp` \| `any` | 协议类型 |
| `priority` | int | ≥1，同 owner 内唯一 | 规则优先级（数字越小优先级越高） |
| `enabled` | bool | 默认 true | 规则是否启用 |
| `description` | string \| null | ≤256 字符 | 规则描述（可选） |
| `created_at` | string | ISO 8601 | 创建时间 |
| `updated_at` | string | ISO 8601 | 最后更新时间 |

**校验规则**（保存前）:
- `dest_port`：若非 null，必须为有效端口（1~65535）或范围（起始 ≤ 结束）
- `source_subnet` / `dest_ip`（含 CIDR）：必须通过 CIDR 格式校验
- `priority`：同一 owner 内不允许重复

**拖拽排序触发的原子操作**:
1. 接收新顺序（ID 列表）
2. 重新计算 priority（按新位置分配 10/20/30…步长）
3. 更新 JSON 持久化
4. 触发 `iptables-restore` 原子替换

---

### Certificate（证书）

**文件路径**: 证书文件由 EasyRSA 管理（`pki_dir/`）；元数据存储于 `data/users/{username}.json` 扩展字段

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `serial` | string | EasyRSA 生成的十六进制序列号 | 证书序列号（唯一） |
| `common_name` | string | 与 User.username 相同 | 证书 CN |
| `issued_at` | string | ISO 8601 | 签发时间 |
| `expires_at` | string | ISO 8601 | 到期时间 |
| `status` | enum | `valid` \| `revoked` \| `expired` | 证书状态 |
| `revoked_at` | string \| null | ISO 8601 | 吊销时间（null 表示未吊销） |
| `crl_version` | int | 递增整数 | 吊销时关联的 CRL 版本号 |

**到期告警触发条件**: `expires_at - now() ≤ 7 天` 且 `status == valid`

---

### DownloadLink（下载链接）

**文件路径**: `data/download_links/{token}.json`

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `token` | string | `secrets.token_urlsafe(32)`，32 字节随机，全局唯一 | URL 令牌 |
| `username` | string | 引用 User.username | 关联用户 |
| `file_path` | string | 有效文件路径 | `.ovpn` 配置文件绝对路径 |
| `expires_at` | string | ISO 8601，生成时 + 3600 秒 | 过期时间 |
| `used` | bool | 默认 false，消费后置 true | 是否已使用 |
| `created_at` | string | ISO 8601 | 创建时间 |
| `used_at` | string \| null | ISO 8601 | 消费时间 |

**消费原子性**: 服务端先将 `used = true` 写入磁盘，再返回文件流，
确保即使客户端中断下载也不可重新获取链接。

---

### AuditLog（审计日志）

**文件路径**: `data/audit/audit-YYYY-MM-DD.jsonl`（JSONL 格式，按天追加写入）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | string | UUID v4 | 日志条目 ID |
| `timestamp` | string | ISO 8601（含毫秒） | 操作时间 |
| `operator` | string | 操作人标识（当前版本固定为 `admin`） | 操作人 |
| `action` | string | 动词短语，如 `create_user`、`revoke_cert` | 操作类型 |
| `target_type` | string | 操作对象类型，如 `user`、`group`、`firewall_rule` | 操作对象类型 |
| `target_id` | string \| null | 操作对象 ID | 操作对象标识 |
| `detail` | object | 结构化操作内容（操作前/后状态摘要） | 操作详情 |
| `result` | enum | `success` \| `failure` | 执行结果 |
| `error_message` | string \| null | 失败时的错误信息 | 错误详情 |
| `prev_hash` | string | 上一条日志条目的 SHA-256 哈希（首条为全零） | 哈希链锚点 |
| `entry_hash` | string | 本条日志内容（不含此字段）的 SHA-256 哈希 | 完整性验证 |

---

## 状态一致性约束汇总（宪法原则三对应）

| 约束 | 涉及实体 | 触发时机 |
|---|---|---|
| 全局子网唯一且格式合法 | SystemConfig | 初始化时一次性校验 |
| 组子网不与全局或其他组重叠 | Group | 创建/修改组时 |
| 活跃用户阻止修改组子网 | Group + User | 修改 Group.subnet 时 |
| 组内有用户时阻止删除组 | Group + User | 删除 Group 时 |
| 删除用户必须先吊销证书并更新 CRL | User + Certificate | 删除 User 时（原子事务） |
| 防火墙规则三重校验（端口/CIDR/优先级） | FirewallRule | 创建/修改规则时 |
| 下载链接一次性消费 | DownloadLink | GET /download/{token} 时 |
| 审计日志只追加不修改 | AuditLog | 所有操作后 |
