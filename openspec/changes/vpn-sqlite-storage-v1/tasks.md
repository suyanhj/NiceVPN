# Tasks

## 1. SQLite 基础设施

- [ ] 1.1 新增 `data/nicevpn.db` 常量和 SQLite schema/version 管理。
- [ ] 1.2 新增 Users/Groups/Peers/Firewall repository API。
- [ ] 1.3 配置 WAL、foreign_keys、busy_timeout 与 NORMAL synchronous。
- [ ] 1.4 增加独立 SQLite 存储单元测试。

## 2. 自动迁移

- [ ] 2.1 启动时检测数据库，不存在时从旧 JSON 一次性导入。
- [ ] 2.2 使用 Pydantic 模型校验 Users/Groups/Peers/Firewall 数据。
- [ ] 2.3 校验导入数量与主键完整性；失败时保留 JSON 并拒绝启动。
- [ ] 2.4 成功后归档旧 JSON / lock / prepush 文件，不再作为运行时数据源。
- [ ] 2.5 新安装无 JSON 时自动创建空数据库。

## 3. Service 切换

- [ ] 3.1 UserService 切 SQLite。
- [ ] 3.2 GroupService 切 SQLite。
- [ ] 3.3 PeerService 切 SQLite。
- [ ] 3.4 FirewallRuleService 切 SQLite，并把 prepush 文件回滚改为数据库快照。
- [ ] 3.5 初始化向导与 Dashboard 去除对迁移 JSON 目录的运行时依赖。
- [ ] 3.6 BulkDownload 的用户元数据 fallback 改走 UserService。

## 4. 边界与验证

- [ ] 4.1 Device Binding / CCD / PKI / remote chain cache 保持文件。
- [ ] 4.2 更新旧 JSON 型单测到临时 SQLite。
- [ ] 4.3 增加 legacy JSON → SQLite → archive 集成测试。
- [ ] 4.4 Python 3.14、OpenSpec strict、Chrome smoke、Docker production build 全通过。
