# 设计：对端动态标签 ipset

## 集合命名与隔离

- 使用独立命名空间 `nvpnpt_<peer-hash>_<content-hash>`，名称不超过 ipset 31 字符限制。
- 名称摘要包含 peer ID、tag ID 和规范化成员快照；不同对端、不同标签或不同成员快照不会相互覆盖。
- 每个逻辑标签只创建一个 `hash:net family inet` 集合，多个规则共享。
- staging 集合使用独立前缀，并且仅清理当前 peer 命名空间。

## 更新事务

1. 解析标签当前活动用户 CCD IPv4、活动组 CIDR，折叠被父 CIDR 覆盖的成员。
2. 生成内容寻址的 ipset 期望集合。
3. SSH 检查 ipset；Debian/Ubuntu 使用 apt，RHEL 系使用 dnf/yum 安装缺失依赖。
4. 创建 staging 集合并写入完整成员，随后 swap 到 stable 内容寻址名称。
5. 使用 `iptables-restore --noflush` 在一个 filter 表事务内清空并重建本对端用户链。
6. iptables 更新成功后，只回收当前 peer 命名空间中不再被逻辑计划或保留的原始规则引用的集合。
7. iptables 更新失败时，旧链保持不变，只删除本轮新建且尚未被旧规则引用的集合。

## 手工规则兼容

- 普通原始规则继续保留原始 rest。
- 若管理员手动拉取并接管了引用 NiceVPN 对端标签集合的原始规则，GC 会识别 `--match-set` 并保留被引用集合。
- 不属于当前 peer 命名空间的集合永远不会被本功能 GC。

## 标签删除保护

- 服务层检查用户/组成员、中心防火墙规则、Mesh 路由和远端防火墙逻辑规则。
- UI 对任意使用状态禁用删除按钮并展示原因。
- SQLite 仓储层在同一写事务中再次检查 user_tags、group_tags 和中心规则，避免并发新增关联绕过保护。
- 外部 peer 配置和远端链缓存引用由 TagService 检查。

## 生命周期

- 删除 peer 时，先删除该 peer 的用户链和跳转，再清理该 peer 命名空间下的稳定集合与 staging 集合。
- 空标签不会生成集合或 ACCEPT 规则；成员移除后，旧集合在成功切换链后回收。
- 远端 SSH/iptables 操作按 peer 串行化，避免手动推送和后台标签刷新互相覆盖。
