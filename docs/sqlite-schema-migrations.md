# NiceVPN SQLite Schema 与 Repository 分层

## 设计边界

NiceVPN 继续使用 Python `sqlite3`，不引入 ORM，不修改 v4 数据库格式、业务接口或 JSON → SQLite 的历史迁移行为。

- `app/storage/sqlite_store.py`：连接、WAL、事务、权限、初始化，继续提供兼容的 `SqliteStore` 公共接口。
- `app/storage/schema_manager.py`：版本检查、SQL 文件读取、显式事务与失败回滚。
- `app/storage/schema_migrations.py`：v2/v3/v4 的历史迁移代码；v3 包含 Python CIDR 数据转换。
- `app/storage/sql/*.sql`：不可变的历史建表/索引 SQL（v1、v2、v4）。
- `app/storage/repositories/`：用户、组、对端、标签、防火墙五个 CRUD 模块。
- `app/storage/repository_helpers.py`：JSON payload、筛选与索引字段规范化。
- `app/storage/migration.py`：既有 JSON → SQLite 一次性导入流程，保持独立。

## 新的数据库升级规则

1. 不修改已经发布的历史版本 SQL 内容；新表或索引使用新的版本迁移文件。
2. 每个迁移版本只执行一次，写入 `schema_migrations`。
3. 在一条 `BEGIN IMMEDIATE` 事务中完成 Bootstrap、升级及版本记录。
4. SQL 语句逐条执行，不使用会隐式提交前置事务的 `executescript()`。
5. 如果任一语句或数据回填失败则回滚，禁止错误地标记版本完成。
6. 数据库版本高于程序支持版本时拒绝启动，不自动降级。
7. `*.sql` 是应用运行所需资源，Docker 使用 `COPY . .` 将其包含在镜像中。

## 新版本迁移开发流程

新增 `005_<feature>.sql` 并注册至 `schema_manager._KNOWN_SQL`，
在 `schema_migrations.py` 中定义迁移函数，由 `ensure_schema` 按版本执行。
若升级包含动态条件或 JSON 数据转换，保留在 Python 迁移函数中。

不要在普通 Repository CRUD 中执行建表/改表语句。

## 回归验证

- 空数据库创建 v4 Schema。
- 从历史 v1/v2/v3 升级，验证旧 JSON payload、索引列和外键。
- 重复启动幂等、较新 Schema 拒绝旧程序。
- DDL 中途出错时回滚结构和版本号。
- SQL 文件缺失或非法文件名明确报错。
- 常规用户/组/标签/防火墙 CRUD 和分页业务测试。
