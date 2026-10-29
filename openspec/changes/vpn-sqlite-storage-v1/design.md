# Design

## Context

当前管理元数据分散在 `data/users/*.json`、`data/groups/*.json`、`data/peers/*.json` 与 `data/firewall/*.json`。Users 与 Peers 是一对象一文件，Firewall 按 owner 分文件。目录扫描在数据量增大后会产生大量小文件 I/O，并使按字段查询和分页只能先全量加载。

Device Binding 虽然也是 JSON，但由 OpenVPN `client-connect` 运行时脚本直接读写，因此不属于本次迁移范围。

## Decisions

### 1. SQLite 是结构化管理元数据唯一事实源

数据库固定为 `data/nicevpn.db`。迁移完成后运行时代码不再读取 Users / Groups / Peers / Firewall 的旧 JSON。

### 2. 首版使用“索引列 + 完整 payload”

每张表保留查询和约束所需的结构化列，同时保存完整 JSON payload，以最大限度保持现有 Service dict 语义并降低一次性业务重写风险。

首版表：

- `users`
- `groups`
- `peers`
- `firewall_rules`
- `schema_migrations`
- `storage_meta`

### 3. SQLite 运行参数

- WAL journal
- synchronous=NORMAL
- foreign_keys=ON
- busy_timeout=5000ms

NiceVPN 是单节点控制器、低并发读多写少，SQLite 与其部署模型匹配。不得把同一个数据库文件放到多主机共享 NFS 上运行。

### 4. 启动迁移是一次性的

当 `nicevpn.db` 不存在：

1. 在临时数据库建立 schema；
2. 读取并严格校验旧 JSON；
3. 在 SQLite 写入并校验数量；
4. 原子替换为正式数据库；
5. 将旧 JSON 与 lock/prepush 文件移动到 `backups/storage-migration-<UTC>/`；
6. 后续仅使用 SQLite。

任何导入/校验错误都必须终止迁移并拒绝启动；原 JSON 保持不动。

### 5. 运行时文件继续保持文件形态

以下内容不迁 SQLite：

- `config.json`
- CCD
- Device Bindings
- EasyRSA PKI / CRL / index.txt
- OVPN 成品
- remote peer chain cache
- API/session secret
- audit / runtime logs

### 6. Firewall 回滚从文件备份变为数据库快照

中心规则写入前在内存保留 owner 旧规则快照。若 iptables/VPN_FORWARD 重建失败，立即把旧快照写回 SQLite。多 owner 排序/恢复同理按 owner 快照回滚，不再依赖 `*.prepush.bak`。

### 7. 查询分页下推到 SQLite

schema v2 为 Peer 与 Firewall 补充筛选/排序所需的反规范化索引列，并在升级时从 payload 自动回填。

- Users 使用 SQL `LIMIT/OFFSET`，用户名搜索、用户组/状态筛选和受支持排序直接下推；
- Groups / Peers 主列表只读取当前页，Groups 摘要使用聚合 SQL；
- Firewall 将中心规则与 Peer 中心放行虚拟行通过统一查询集合分页，关键词、归属、启停、协议、动作、类型与排序在 SQL 层处理；
- AG Grid 仅持有当前页 rowData，分类过滤器的可选值使用数据库 facets，而不是扫描当前页；
- 在线会话、设备来源等运行时派生字段无法直接由 SQLite 表达时，允许回退到 Python 全量计算以保持筛选语义正确；
- Firewall 优先级拖拽是明确例外：仅在用户进入排序模式时加载完整有序集合，普通浏览不全量读取。

## Non-Goals

- 本变更不把证书事实源改成 SQLite。
- 本变更不改 OpenVPN client-connect 的 Device Binding 文件协议。
- 不支持 JSON/SQLite 长期双写或运行时切换。
