# 数据与迁移

## 数据位置

结构化业务数据：

```text
data/nicevpn.db
```

SQLite 保存用户、组、对端、标签和防火墙等管理数据。**部署参数位于项目根目录 `config.yml`**（见 `config.example.yml`），长期运行设置写入 `data/config.json`；`data/` 由启动流程按需创建，还保存 SQLite、会话密钥、远端工作副本等；PKI/CRL、CCD、OVPN 与运行日志沿用相应文件路径。

旧版 `data/config.json` 在根目录尚无 `config.yml` 时自动提取部署字段，运行字段仍留在原 JSON；旧版本的 `data/users`、`data/groups`、`data/peers`、`data/firewall` JSON 仅用于启动时的一次性迁移，不是当前运行时主存储。迁移完成后，旧数据归档在 `backups/storage-migration-<UTC>/`。

## Schema 迁移规则

- 新增表、字段或索引必须增加版本化迁移。
- 已发布的历史 SQL 不修改；新变更使用新迁移文件。
- 迁移和版本记录在事务中完成；失败必须回滚。
- 程序遇到高于自身支持版本的数据库时应拒绝启动，不自动降级。
- SQL 放在 storage / repository 层，不在普通业务 CRUD 中改表。

## 备份与降级

备份时停止服务或使用项目支持的备份流程，确保数据库与关联文件一致。启用 SSO 时，`data/sso_provider_master.key` 必须与数据库一起备份；丢失密钥将无法解密已保存的身份提供商凭据。

降级到不支持 SQLite 的旧版本前，必须按对应版本从迁移归档恢复 JSON 数据；旧程序不会读取当前 SQLite 数据库。
