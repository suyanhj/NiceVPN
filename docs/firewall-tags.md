# NiceVPN 防火墙标签（开发分支）

## 数据模型

SQLite schema v4：`tags(id, name, description, created_at, updated_at)`、`user_tags(tag_id, username)`、`group_tags(tag_id, group_id)`。关联表存在联合主键和索引，用户/组删除时以 SQLite 外键级联清理关联。

## 运行逻辑

- 用户、组页面可以创建标签和给对象添加/移除多个标签。维护关联后刷新 `VPN_FORWARD`，失败时尝试恢复原关联。
- 中心防火墙规则支持 `owner_type=tag`、`owner_id=tag UUID`。持久化数据不存标签源地址快照；应用规则时读取 SQLite 关联和当前 CCD 地址，合并用户虚拟 IP、组 CIDR。停用用户跳过；停用组的 CIDR 跳过。
- 标签源集合名稳定为 `ovpntag_*`，同一个标签被多条防火墙规则引用时只创建一份 `hash:net` 集合，使用 stage/swap/rollback/gc 收敛。
- 若关联成员没有可用源地址，对应标签规则**不生成任何 iptables 行**，严禁变成无源匹配约束的 ACCEPT。
- 标签的地址集合随每次规则刷新重新计算。直接修改 CCD、修改用户组关联或状态不经过标签编辑时，可能需要主动刷新防火墙才能及时收敛；这属于后续自动同步联动的验收项，不能把“保存关联”与所有外部状态变更视为等价。

## 验收

1. 新建两个标签，给用户和组分别关联，确认 SQLite 表及 UI。
2. 创建同标签的两条中心规则，检查 `ipset list -n` 只有一个 `ovpntag_*`。
3. 给标签新增、删除关联，检查 `ipset list ovpntag_...` 的成员及时更新，删除的不残留。
4. 清空标签成员，确认 `VPN_FORWARD` 无该标签规则的无条件放行。
5. 模拟 `iptables-restore` 错误，确认标签关联回滚且原内核规则保留。
6. 删除最后的标签规则并刷新，确认 GC 清理废弃集合。

测试机验收前先备份 `iptables-save` 和 `ipset save`。
