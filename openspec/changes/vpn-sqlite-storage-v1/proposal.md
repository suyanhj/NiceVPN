# Proposal

## Why

NiceVPN 当前 Users、Groups、Peers 与中心 Firewall Rules 主要通过目录扫描和每对象 JSON 文件持久化。随着用户和规则数量增加，列表加载、唯一性检查、规则查找、排序与批量操作需要反复执行 glob/open/read/json decode，再在 Python 中过滤和聚合，已经成为管理后台明显的 I/O 瓶颈。

V2 已将页面层与 Service 层隔离，并完成异步首屏加载，因此现在适合把“管理系统自己的结构化元数据”迁移到 SQLite，同时保留 OpenVPN、EasyRSA 与 SSH 工作副本天然的文件真值。

## What Changes

- 新增 SQLite 存储层与 schema migration 基础设施。
- `data/nicevpn.db` 成为 Users、Groups、Peers、Center Firewall Rules 的唯一运行时事实源。
- 升级启动时若数据库不存在，自动从现有 JSON 导入 SQLite；校验成功后把旧 JSON 移入备份目录。
- 迁移失败时拒绝启动，不修改原 JSON，避免静默降级与双真值。
- Service 对外 dict / Pydantic 语义保持兼容，页面层不感知底层迁移。
- Device Bindings、CCD、PKI/CRL、OVPN 成品、远端 SSH chain cache、config 与日志继续保持文件形态。

## Capabilities

### New Capabilities

- `vpn-sqlite-storage`: NiceVPN 管理元数据 SQLite schema、自动 JSON 迁移、单一事实源和可演进 schema migration。

## Impact

- 主要影响 `app/storage/`、Users/Groups/Peers/Firewall Service、初始化向导、Dashboard 统计与少量服务测试。
- 不改变 OpenVPN/EasyRSA 文件格式，不迁移 `/etc/openvpn/ccd`、`/etc/openvpn/mgmt/device_bindings`、PKI、CRL 或远端链工作副本。
- 首版仍保留现有 Service API；后续 AG Grid 服务端分页可直接基于 SQLite 查询接口继续实现。
