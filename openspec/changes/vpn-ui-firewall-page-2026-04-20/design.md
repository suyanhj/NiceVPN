# 设计：规则管理单页（`/firewall`）

## 1. 页面结构

- **路由**：`main.py` 注册 `@ui.page("/firewall")` → `FirewallPage().render()`（见 `app/ui/pages/firewall.py`）。
- **根布局**：`page-shell` + `mgmt-page` + `page-shell--firewall`（在纵向 flex 中吃满主内容区余高，见 `app/ui/theme.py`）。
- **顶区**：标题/描述行（`mgmt-header-row` + `mgmt-header-copy` + `mgmt-title` / `mgmt-desc`）＋ **策略 Tab 与工具栏**（`firewall-control-header` + `firewall-tab-bar` + `firewall-compact-tabs`；新建/导入/导出/批量等 `mgmt-toolbar-btn`）。
- **主区**：`ui.tab_panels`（`firewall-tabpanels`）下两块：
  - **中心策略**：按归属/关键词搜索（`firewall-search-row`；输入框 **`outlined` + `dense`**，**回车**提交跳转归属，无独立「搜索」按钮）→ **带边框列表**在 `mgmt-panel` + `mgmt-panel-list` + `firewall-center-shell` 内，规则区为 `mgmt-panel` + `mgmt-panel-flex` + `mgmt-panel-scroll`（`Sortable` 对 **中心 JSON 规则** 排序）。**`DRAG TO CHANGE PRIORITY` 与 `N RULES TOTAL`** 作为**页脚汇总行**，放在**上述卡片外侧**（与卡片兄弟节点，见 `firewall.py`），不再画在卡片边框内。
  - **对端远端**：对端节点选择与 SSH 拉取（`firewall-peer-remote` + `firewall-remote-*`），列表为对端机用户链的**工作副本**（与中心 JSON 不同源，见下节）；**页脚汇总行**与中心策略同构（卡片外底部）。

**样式专名**与**组管理**共用的 `mgmt-*` 清单见 **`vpn-css-refactor-2026-04-03/design.md`**, §「防火墙（专名 + 与 mgmt 共用）」.

## 2. 数据语义

### 2.1 中心策略

- **持久化**：`data/firewall/*.json` 等业务规则，经 **`FirewallRuleService`** CRUD、合并后写入本机 **`VPN_FORWARD`**。  
- **内核刷新两段路径**：  
  - **规则管理页**（创建/编辑/删除/启停/拖拽排序/备份恢复中心等）：**`FirewallRuleService.refresh_vpn_forward_only()`** → `IptablesManager.refresh_vpn_forward_only` —— **仅** `iptables-restore` 刷新 **`VPN_FORWARD`** 链，并为规则内 **多 IP ipset** 做 `ipset` 同步；**不** 调用 `_ensure_forward_hooks`、`_ensure_input_from_vpn`、`_ensure_vpn_nat_masquerade`。失败时仅尝试回滚 **`VPN_FORWARD`** 链规则快照。  
  - **项目级恢复**（管理端启动 `scheduler`、初始化向导「启动服务」、显式 `rebuild_iptables()`）：**`IptablesManager.rebuild_rules`** —— 全量 `VPN_FORWARD` + ipset + **`FORWARD`/`INPUT` 钩子** + **`ip_forward`** + **中心 `nat/POSTROUTING` MASQUERADE**；失败时 **`filter` 全表**快照回滚（见 **`vpn-bugfix-2026-04-03-r3`** §2.2）。  
- **UI**：列表展示、拖拽调优先级（`firewall_reorder` → `rule_service.reorder`）、启用/禁用、简写导入（`app/services/firewall/simple_rule_import.py` 等）——**不经过对端 SSH**。

### 2.2 对端远端

- **事实源在 SSH 对端机**：`PeerService.fetch_remote_peer_filter_chain_snapshot` 拉取对端 `filter` 上 **本对端 `VPN_PEER_*` 用户链** 等，经 **`remote_chain_cache.record_from_fetch`** 落**本地工作副本**（`app/services/peer_instance/remote_chain_cache.py`）。
- **工作副本**用于列表展示、行内编辑、写回前排序；**写回**仅通过 `PeerService.apply_remote_peer_filter_chain_rests`（及同类路径）**SSH 下发**，不得在 UI 中假定不拉取即与对端一致（**与 `firewall.py` 模块 docstring 同义**）。

### 2.3 与「对端实例 / Mesh」的关系

- 对端「站点」模型、**中心半幅**放行、SSH 能力清单见 **`vpn-peer-instance-mesh-2026-04-13`**。本条只强调：**同一 URL 的「对端远端」Tab 管的是对端机内核链工作副本，不是 `data/firewall` 的 center JSON。**

## 3. 后端要点（与 UI 对账）

| 能力 | 主要入口 |
|------|----------|
| 规则 CRUD、排序、启停、备份恢复 → **仅**刷新 `VPN_FORWARD` | `FirewallRuleService`（`_refresh_vpn_forward_only`）、`IptablesManager.refresh_vpn_forward_only` |
| 启动/向导等项目级 → 完整 iptables（含 hooks + NAT） | `FirewallRuleService.rebuild_iptables()`、`IptablesManager.rebuild_rules` |
| 中心全量 `rebuild_rules` 任一步失败时，**以 `iptables-save -t filter` 快照回滚**（避免仅 flush 后 restore 失败导致 filter 全空） | `IptablesManager.rebuild_rules`：详见 **`openspec/changes/vpn-bugfix-2026-04-03-r3/design.md` §2.2**（与源码一致；总览不重复长文） |
| 对端链拉取/落库/写回 | `remote_chain_cache`, `PeerService`, `remote_peer_iptables` |

## 4. 观测与体验（非功能）

- **运行日志**：策略 Tab 切换、对端拉取落库等部分为 **debug** 级别，避免刷屏；`nicegui` 中 *Event listeners changed* 类提示在 `setup_logging` 中过滤（见 `app/utils/logging_setup.py`）。

## 5. 变更索引（勿重复长文）

| 主题 | 变更目录 |
|------|----------|
| 规则模型、抽屉、搜索、多端口、批量、ipset 演进 | `vpn-bugfix-2026-04`, `vpn-bugfix-2026-04-03`, `vpn-bugfix-2026-04-07*`, `vpn-bugfix-2026-04-03-r2` / `-r3` |
| 主题与 `mgmt-*` / `firewall-*` | `vpn-css-refactor-2026-04-03` |
| 对端实例、SSH、中心/对端防火墙协同 | `vpn-peer-instance-mesh-2026-04-13` |
| 运行时目录、INIT、与防火墙重建同期行为 | `vpn-lan-firewall-devicebind-2026-04-09`（及引用链） |

## 6. 维护约定

- **规则页**新增/删减主要交互时，**至少**更新本条的 `design.md` §1–2，并在对应 **实现向** 变更里记 tasks。
- 若只改 **样式类名** 而交互不变，**可只**更新 `vpn-css-refactor-2026-04-03` 与本条 §1 的类名表（或从 css-refactor 指回本条一句话）。
