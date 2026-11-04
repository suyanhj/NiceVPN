# NiceVPN 声明式 ipset 生命周期（第一阶段）

## 目的与范围

本阶段仅重构中心侧 `VPN_FORWARD` 使用的 ipset，统一中心规则（rule）和中心对端（peer）的集合生命周期，预留 tag 名称。**没有创建 tags/user_tags/group_tags 表，也没有新增 owner_type=tag 业务功能。**

## 实现

- `IpsetManager.spec(owner_type, owner_id, purpose, entries)`：业务层仅声明 owner/purpose/成员，统一转换 IPv4 和 CIDR。
- `stable_name`：继续使用历史 `ovpnfw_` 与 `ovpnpeer_` 哈希命名，不迁移原有业务引用；`ovpntag_` 为下一阶段预留。
- `reconcile`：获取跨线程/进程锁，枚举内核，先写唯一 staging set，使用 `ipset swap` 切换稳定名称；原 stable 内容留在 staging，等待调用方确认。
- `commit`：iptables 成功之后销毁本事务的 staging，GC 不再出现在期望状态中的 NiceVPN stable 集合；GC 的 destroy 失败只记录告警、不破坏已经生效的规则。
- `rollback`：逆序 swap 回旧集合；本轮新建的 stable 集合恢复不存在状态，释放锁。
- `gc`：只遍历 `ovpnfw_` / `ovpnpeer_` / `ovpntag_` stable 集合，不触碰第三方前缀；无法销毁被引用集合时告警，不强删。全局 GC 故意不清理未知 staging，避免破坏其他活跃事务。
- `IptablesManager.refresh_vpn_forward_only` 与 `rebuild_rules` 使用同一套期望集合与事务接口，分别维持原有的链刷新边界和项目级恢复范围。

## 已知限制与部署前审计

1. Linux 下需要 `ipset` / `iptables` / `CAP_NET_ADMIN`；Windows 只允许运行导入与单元测试。
2. staging 和 iptables 的切换并非内核的单一跨子系统原子提交；失败时执行补偿式 rollback。极端内核或权限故障下仍需根据日志人工恢复。
3. 仅一个中心管理器应该具有 NiceVPN 自有命名空间的写权限。跨进程锁只协调使用本管理器的实例，外部手工 ipset 命令不会遵守该锁。
4. 目前未设计自动清除“崩溃后残留的 staging”功能：为防误删活动事务，必须在确认无进程运行时才可人工检查并清理。
5. 禁止在未检查现有 `ipset list -n`、`iptables-save -t filter` 以及做好备份前直接在生产测试。单元测试为内核模拟，不能替代真实 Linux 环境联调。
6. `VPN_FORWARD` 以外的远端 SSH 下发逻辑继续使用 iptables，不在本阶段改成远端 ipset。

## 后续阶段

第一阶段 Linux 实机冒烟通过后，第二阶段实现 SQLite 的 `tags`、`user_tags`、`group_tags`，标签到 IP/CIDR 的期望状态生成和 `owner_type=tag` 规则；禁止对 ipset 另建一套增量创建/删除路径。
