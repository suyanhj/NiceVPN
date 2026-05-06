# 防火墙规则管理页 — 单页总览（OpenSpec）

## 背景

「规则管理」单页能力分散在多个历史变更（`vpn-bugfix-2026-04*`、`vpn-css-refactor-2026-04-03`、`vpn-peer-instance-mesh-2026-04-13` 等）中描述，**缺少一条以本页为视角**的说明：双 Tab 含义、中心 JSON 与对端 SSH 工作副本的边界、以及数据面与 UI 的对应关系。

## 目标

1. 在 `openspec/changes/` 下**新建独立变更**，集中说明 **`/firewall` 规则页**的**产品语义、数据流、关键文件与类名**。
2. 与 `firewall.py` 文件头**模块说明**、及其他变更**互链**（不重复实现细节，以代码与 design 为准）。

## 范围

- **在范围内**：`FirewallPage` 渲染结构、中心策略 / 对端远端 Tab、与 `FirewallRuleService` / `IptablesManager` / `remote_chain_cache` / `PeerService` 的协作关系；`theme` 中 `firewall-*` + `mgmt-*` 的用途索引；**中心侧** `rebuild_rules` 失败时 filter 表快照回滚的**行为级**说明。
- **不在范围内**：对端机 SSH 上 iptables 实现的逐行设计（见 `vpn-peer-instance-mesh-2026-04-13`）；`data/` 目录全树（见 `vpn-lan-firewall-devicebind-2026-04-09` 等）；重复抄写 `iptables_mgr` 每条规则展开逻辑。

## 非目标

- 不通过本条变更**修改**业务代码；仅**补充文档**。

## 与既有变更的关系

- **不替代**下列变更，仅**汇总并指向**  
  - `vpn-bugfix-2026-04*`：规则模型、侧栏、搜索、排序、ipset/批量 等**迭代**  
  - `vpn-css-refactor-2026-04-03`：`mgmt-*` / `firewall-*` 样式与**组管理同构**  
  - `vpn-peer-instance-mesh-2026-04-13`：对端实例、中心半幅放行、**对端远端** SSH 链  
  - `vpn-bugfix-2026-04-03-r3`：统一卡片渲染、Sortable 本地化、以及 **rebuild 顺序 / ipset 与链** 等后端片段  
- **权威细节**以 **`app/ui/pages/firewall.py` 顶部的模块 docstring** 与 **`design.md`（本条）** 中引用路径为准；冲突时**以代码为准**并应回头修正本条文档。
