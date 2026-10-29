# Tasks

## 1. SQLite 基础设施

- [x] 1.1 新增 `data/nicevpn.db` 常量和 SQLite schema/version 管理。
- [x] 1.2 新增 Users/Groups/Peers/Firewall repository API。
- [x] 1.3 配置 WAL、foreign_keys、busy_timeout 与 NORMAL synchronous。
- [x] 1.4 增加独立 SQLite 存储单元测试。

## 2. 自动迁移

- [x] 2.1 启动时检测数据库，不存在时从旧 JSON 一次性导入。
- [x] 2.2 使用 Pydantic 模型校验 Users/Groups/Peers/Firewall 数据。
- [x] 2.3 校验导入数量与主键完整性；失败时保留 JSON 并拒绝启动。
- [x] 2.4 成功后归档旧 JSON / lock / prepush 文件，不再作为运行时数据源。
- [x] 2.5 新安装无 JSON 时自动创建空数据库。

## 3. Service 切换

- [x] 3.1 UserService 切 SQLite。
- [x] 3.2 GroupService 切 SQLite。
- [x] 3.3 PeerService 切 SQLite。
- [x] 3.4 FirewallRuleService 切 SQLite，并把 prepush 文件回滚改为数据库快照。
- [x] 3.5 初始化向导与 Dashboard 去除对迁移 JSON 目录的运行时依赖。
- [x] 3.6 BulkDownload 的用户元数据 fallback 改走 UserService。

## 4. 边界与验证

- [x] 4.1 Device Binding / CCD / PKI / remote chain cache 保持文件。
- [x] 4.2 更新旧 JSON 型单测到临时 SQLite。
- [x] 4.3 增加 legacy JSON → SQLite → archive 集成测试。
- [x] 4.4 Python 3.14、OpenSpec strict、Chrome smoke、Docker production build 全通过。

## 5. 数据库级查询分页

- [x] 5.1 Users 支持 SQL LIMIT/OFFSET、搜索、组/状态筛选与排序下推。
- [x] 5.2 Groups / Peers 列表接入 SQLite 当前页查询；Groups 摘要改为聚合 SQL。
- [x] 5.3 Firewall 将中心规则与 Peer 中心放行统一为 SQL 查询集合，支持搜索、分类筛选、排序与分页。
- [x] 5.4 AG Grid 使用服务端分页页脚与完整 facets，跨页选择保持；派生运行时字段在必要时正确回退。
- [x] 5.5 优先级拖拽仅在显式排序模式加载完整规则集合，普通浏览不做全量读取。
- [x] 5.6 增加 schema v2、分页、筛选、facets 与 UI 契约测试。
