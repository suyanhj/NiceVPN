# vpn-peer-instance-mesh-2026-04-13

## 概述

在现有「中心 OpenVPN 服务 + 用户证书 + `.ovpn` + CCD + 组防火墙（`VPN_FORWARD` / ipset）」之上，增加 **对端实例（站点）** 能力：对端以 **客户端** 身份接入，承载 **一段或多段后方内网**（物理网 + 第三段内网等），与中心及其它 VPN 用户组网；支持 **SSH 管理锚点**（自动装配 / 手动装配 + 程序维护规则）、**按 `peer=<id>` 定点清理** 的防火墙模型、**对端内网 CIDR 动态变更** 并同步本端数据面（CCD、ipset/iptables 等），**不承诺** 已连客户端路由热更新（仅界面提示重连）。

## 背景与动机

- 组网场景下对端运行在 **不同物理子网**，CIDR 可能变更（如 172.16.0.0/16 → 192.168.0.0/24），需在管理页 **一处修改** 并尽量 **自动同步** CCD、本端防火墙对象等，减少手工改多处。
- **防火墙默认策略（老规矩）**：创建对端实例后 **自动** 下发「宽松」放行规则，便于先打通再收紧；细则见 `design.md` §3。后续若需 **按组** 收紧，可通过 **额外规则 / 顺序** 在默认规则之上叠加（实现阶段定序）。
- **中心侧** 对端放行使用 **VPN_PEER + ipset（`ovpnpeer_*`）+ iptables**，注释带 **`peer=<对端实例 id>`**。**对端主机**：由管理端经 **SSH**（**配置推送**、`deploy_*` API）写入 **iptables**（**未** 在对端建 ipset）；注释含 **`ovpn-mgmt-peer`** 与 **`peer=`**，清理时 **不误删** 非本模块规则。
- **本机 VPN 服务实例** 的 iptables 注释已使用 `inst=`（`ovpn-mgmt-*`）；对端使用 **`peer=`** 区分命名空间。

## 目标（要做什么）

1. **对端实例实体**  
   - 稳定 **对端实例 id**（UUID 或等价唯一键）。  
   - 绑定 **专用用户**（证书 + `.ovpn`），与现有 `UserService` / PKI 流程一致或扩展字段。  
   - 维护 **后方内网 CIDR 列表**（至少一段，支持多段如 172.22.0.0/16 与 172.18.0.0/24「第三段内网」）。  
   - **SSH 管理主机**（地址、端口、用户名、认证）：**密码与私钥 PEM 可落库**（对端 JSON，须限制 `data` 目录权限）；私钥支持 **粘贴或上传**。**已实现**：SSH **安装 OpenVPN**（与 **`InitWizard`/`installer`** 同源策略）、**客户端探测**、**iptables 下发**、**.ovpn** **经由配置推送**、**启用 systemd client**（与向导对齐的官方模板）、**删除对端时尽力远端清理**。**本条收口**：不要求把所有步骤揉成单一按钮流水线 UI。

2. **OpenVPN 数据面（中心侧）**  
   - 该用户的 **CCD** 含 **`ifconfig-push`**（既有）及 **`iroute`**（每段后方内网一条或等价汇总），使中心内核 **经该隧道** 到达对端内网。  
   - **路由推送**：`push "route ..."` 可写在 **`server.conf`（全局）** 或 **该用户的 CCD 文件（仅对该客户端）**；二者 OpenVPN 均支持，见 `design.md` §4.3。对端 CIDR 变更时，凡 **依赖对端前缀** 的 push（无论落在 server 还是 CCD）需 **重算并落盘**。

3. **防火墙（中心侧）**  
   - 使用 **独立自定义链** + **独立 ipset 前缀**（与 `VPN_FORWARD` / `ovpnfw_` **区分**）；`FORWARD` 仍可沿用「跳转自定义链」范式。  
   - 规则与 **`peer=<对端实例 id>`** 注释关联，**按 id 定点删除**。  
   - **默认自动创建**「允许所有」语义的两类规则中的 **本地侧** 一条：**源为对端元数据中的内网 CIDR（多段则进 ipset 或等价）→ ACCEPT**，不限制目的地址（在 `FORWARD`/`VPN_PEER` 上下文中实现，见 design §3）；便于组网后立刻互通，再按需收紧。

4. **防火墙（对端侧，经 SSH）**  
   - **已实现（首期）**：（1）经 SSH 在 **对端本机** 写入 **iptables**（**filter/FORWARD** 与可选 **nat/POSTROUTING**），注释同时含 **`ovpn-mgmt-peer`** 与 **`peer=<对端实例 id>`**（与中心 id 一致），清理时按 `-S` 解析后 `-D`，避免误删。宽松 FORWARD：**`-s`** = 中心 **`global_subnet`** → **ACCEPT**（**不写 `-i`/`-o` 网卡绑定**，避免 tun 名漂移）；可选 **MASQUERADE**：**`nat` POSTROUTING** `-s global_subnet -j MASQUERADE`（目标不限），由 **`masquerade_on_peer`** / UI「配置推送」驱动；与 **`remote_peer_iptables.py`、`design.md` §3** 对齐。（2）客户端配置可通过 **配置推送** 写入远端 **`/etc/openvpn/client/client.conf`**（具体路径以实现为准）。  
   - **已实现（安装，与向导同源）**：对端经 SSH 执行 **`build_peer_openvpn_install_script`**：Debian 系与 **`installer._install_debian`** 一致；RHEL 系 repo 版过低时会 **源码编译** 以满足 **`tls-crypt-v2`** 等与中心配置对齐。  
   - **产品收口（本条）**：**systemd**、**.ovpn**、**iptables** 经 **安装流程 + 配置推送 + `auto_install_on_peer`** 组合落地；**对端侧** 规则 **未** 使用 **ipset**（直写 `iptables`）；更复杂的「对端 ipset」或「单一 UI 一键」另起变更时再评估。  
   - **触发方式**：管理页 **配置推送**，或运维按 **部署说明独立页** 手工配置；详见 **`vpn-peer-manual-page-2026-04-28`**。**创建/变更对端后不会无条件自动 SSH 重推**（CIDR 或 `global_subnet` 变更后需运维再次推送或手工改远端）。  
   - **firewalld**：下发前检测 **`systemctl is-active firewalld`**，若为 **active** 则 **仅告警**（不自动改 firewalld 规则）。  
   - **删除对端**：在具备 SSH 凭据时 **尽力** 远端删除带本 `peer=` 的上述规则；失败则日志告警，依赖手工清理。

5. **对端 CIDR 动态变更**  
   - 保存新 CIDR 后：**更新持久化**、**重生成** 该对端用户 CCD、**更新** 本端引用该 CIDR 的 ipset/规则；若需刷新远端 iptables/client，运维使用 **配置推送**（或等价 SSH），**不会在每次保存后无条件自动 SSH**；**不承诺** 已建立隧道瞬间切换内核状态，**建议对端重连** 以收敛 `iroute` / 路由残留。

6. **路由推送与客户端**  
   - **不实现**「已连接客户端立即获得新 push route」；界面 **明确提示**：配置变更后需 **客户端重连** 后路由表更新。  
   - 可选后续增强：对指定会话 **断开以促重连**（非本条必须）。

7. **启停对端实例**  
   - 停止 / 删除时：**按 `peer=<id>`** 清理本端（及 SSH 对端，若可达）曾写入的 **iptables/ipset**；路由依赖 OpenVPN down / 运维文档；避免误删手工规则。

8. **服务管理 UI（随本需求一并交付）**  
   - **确认弹窗**：启停 / 重启实例的二次确认，**底部按钮居中**、版式简洁；**停止确认** 文案 **缩短**（详细清理说明依赖既有 `notify` / 文档，见 `design.md` §11）。  
   - **`server.conf` 变更**：与现网一致，**保存后需重启实例** 方可靠生效（见 `design.md` §10）。

## 非目标（本期不做或明确不承诺）

- **SD-WAN 级** 路由热推送、无重连即时全网收敛。  
- 替代 **firewalld/nft** 的全自动适配（首期可 **仅 iptables+ipset** 文档化；若检测到 nft backend，行为以设计说明为准）。  
- **duplicate-cn**、与 CCD 固定 IP 冲突的能力（延续既有产品结论）。  
- 无 SSH 凭据时的 **对端内核** 自动配置（仍依赖 **部署说明独立页** / 下载 Markdown + 中心侧同步；有凭据时用 **配置推送** 写入 iptables / 客户端配置）。  
- **交互式 sudo**：本期不承诺；**SSH** 以单次连接 + 失败显式反馈为准（详见代码）。**固定 known_hosts / 自动重试**：非本产品必达；生产可凭运维与跳板机满足，**另变更** 再增强时以 `tasks.md` 记录为准。

## 参考拓扑（需求讨论摘录）

- **A 本地**：物理 10.3.0.0/16，VPN 池 10.255.0.0/16。  
- **B 对端**：物理 172.22.0.0/16，第三段内网 172.18.0.0/24（均经 B 转发）。  
- 访问矩阵与 SNAT 结论见 `design.md` §流量与 SNAT。

## 与既有 OpenSpec 关系

- **`vpn-lan-firewall-devicebind-2026-04-09`**：`VPN_FORWARD`、`ovpn-mgmt-*`、`push_lan_routes`、**中心 `POSTROUTING` MASQUERADE（`-s global_subnet`，无 `-o`/`-d`）** 为本条 **叠加**；本条 **对端链/ipset** 须 **命名隔离**，清理逻辑 **按 `peer=`** 独立。  
- **`vpn-peer-manual-page-2026-04-28`**：**对端部署说明** 独立子页（`/peers/manual`）、结构化命令与 Markdown 下载；**不**承载对端 iptables 下发逻辑。  
- **`vpn-instance-id-inst-comment`**（若已归档）：本机 `inst=` 与对端 `peer=` **并存**，注释前缀不同，避免清理混淆。

## 审阅检查项（产品 / 开发）

- [x] 对端实例 **数据模型**（多 CIDR、SSH 主机/端口/用户、**落库密码/私钥 PEM/口令**、**`masquerade_on_peer`**、**`auto_install_on_peer`**、**`ssh_openvpn_binary`** 等）已落地，见 `app/models/peer_instance.py`（~~`peer_ssh_tun_interface`~~、~~`firewall_source_group_id`~~ 已按需移除）。  
- [x] **默认宽松规则** 与后续 **按组收紧**：中心 **VPN_PEER** 仍为宽松；mesh 收紧在 **CCD**；「关闭默认 VPN_PEER」**开关** 非本期必达，另需时单独立项。  
- [x] **CCD `iroute` 与多段内网** 生成规则已按实现认可（`ccd_merge`）。  
- [x] **手动说明**：`export_peer_manual_markdown` / **独立页** `/peers/manual`（可复制命令）；见 **`vpn-peer-manual-page-2026-04-28`**。  
- [x] 实施以 `tasks.md` 与本文为 **收口** 版本；历史上列为「进阶」的条目已在 `tasks.md` 标注 **产品收口** 并 **勾选**。
