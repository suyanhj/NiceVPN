# 设计说明 — vpn-peer-instance-mesh-2026-04-13

## 1. 术语

| 术语 | 含义 |
|------|------|
| **中心 / 本地** | 运行本管理端与 OpenVPN **服务端** 的一侧（如 A）。 |
| **对端实例** | 逻辑对象：绑定一名 **VPN 客户端用户** + 后方 **内网 CIDR（可多段）** + **SSH 管理锚点** + 可选 **对端 SNAT 策略**。 |
| **本机 VPN 服务实例** | `openvpn@<name>`；iptables 管理钩子上 **`inst=`**（或 `ovpn-mgmt-*` 注释内带 `inst=`）。 |
| **peer id** | 对端实例唯一 id；iptables/ipset 注释 **`peer=<peer id>`**。 |

## 2. 链、ipset、注释约定

### 2.1 与本机隔离

- **自定义链**：建议新增例如 **`VPN_PEER`**（名称以最终实现为准），**不**与 **`VPN_FORWARD`** 混写业务规则，避免 `rebuild_rules` 全量刷链时误伤对端静态策略（若实现上仍走同一 `FORWARD` 跳转，须在任务中明确 **重建范围**）。  
- **ipset 前缀**：例如 **`ovpnpeer_`**，与现有 **`ovpnfw_`** 区分。  
- **注释**：凡本模块写入的对端相关规则，**必须**带 **`peer=<peer id>`**，清理时 **仅匹配该子串 + 本链/本表约定**，不误删无注释规则。

### 2.2 与 `inst=` 的关系

- **`inst=`**：本机 OpenVPN **服务实例** 维度（FORWARD/INPUT/nat 管理钩子）。  
- **`peer=`**：**对端站点实例** 维度（**中心侧** `VPN_PEER` + ipset；**对端侧目标设计**与中心一致：按地址类型选择 `iptables` 或 `ipset+iptables`）。  
- 二者 **不得** 共用同一注释键名，以免清理脚本混淆。

## 3. 默认防火墙规则（老规矩：自动创建「允许所有」语义）

产品约定：**创建对端实例后** 在中心侧自动维护 **宽松** 规则（见上表「中心」行）；**对端主机** 侧规则由 **配置推送（含 iptables）** 或 **运维按部署说明手工** 配置，详见 **`tasks.md`**。**对端 FORWARD** 采用 **`-s <global_subnet>`** 匹配 **源为中心 VPN 地址池**，便于 VPN→LAN 转发命中链路。**不写 `-i`/`-o` 绑定 tunnel**，以降低 **隧道接口改名** 导致的规则遗漏。可选 **POSTROUTING MASQUERADE**：`-s global_subnet -j MASQUERADE`（目标地址不设限定）。

| 位置 | 语义（逻辑） | 说明 |
|------|----------------|------|
| **对端主机** | **`-s` = 中心 VPN 全局地址池**（与 `SystemConfig.global_subnet` 一致）**→ ACCEPT** | 放行 **源为 VPN 网段** 的转发。**落地**：对用户链 **`-s <global_subnet>`**，FORWARD **跳转到自定义链**，注释 **`ovpn-mgmt-peer peer=<id> role=fwd-global`**；**不写 `-i`/`-o` 网卡**（避免 tun 名变更）。NAT 可选 **POSTROUTING**：`-s <global_subnet> -j MASQUERADE`（目标不限）；由 **`masquerade_on_peer`** 与 UI「配置推送」控制；详见 **`remote_peer_iptables.py`**。 |
| **中心（本地）** | **`-s` = 对端元数据中的内网 CIDR**（多段则用 **ipset** 聚合或多条规则）**→ ACCEPT** | 放行 **从对端内网经隧道进入中心** 的转发（同上，**不额外限制目的**）。 |

- **收紧**：后续若需 **按组** 限制，可 **在默认规则之前** 插入更细规则，或提供产品选项 **关闭/删除** 默认宽松规则后仅走 `VPN_FORWARD` JSON 规则（实现时二选一或并存，任务单定）。  
- **CIDR 变更**：对端元数据 CIDR 变化时，**更新** 中心侧这条规则的 **源匹配**（ipset 或规则文本）；对端侧 **VPN 全局 `-s`** 一般不变，除非中心调整 `global_subnet`。

### 3.1 对端地址匹配策略（与中心侧一致）

- **源与目标同等处理**：任一侧只要出现**多个具体 IP（非 CIDR）**，即使用 **`ipset + iptables`**。  
  - 源多 IP：`-m set --match-set <set> src`  
  - 目标多 IP：`-m set --match-set <set> dst`  
  - 源、目标都为多具体 IP：可同时引用两个 set。  
- **仅当源和目标都不是“多具体 IP”** 时，使用直接 `iptables` 匹配。  
  - CIDR（单段或多段）直接写 `-s/-d <cidr>`（按规则展开多条）。  
  - 单个具体 IP 直接写 `-s/-d <ip>`。  
- **清理约定**：`ipset` 与 `iptables` 规则均带 `peer=<id>` 关联信息，删除对端时按 `peer=` 定点清理，不做模糊全表删除。

## 4. OpenVPN：CCD 与路由

### 4.1 对端绑定用户 CCD

- **`ifconfig-push`**：保持与现网一致（固定客户端隧道地址）。  
- **`iroute`**：对 **每一** 后方内网 CIDR 写一条（或实现认可的汇总方式），使 **中心** 将去往该前缀的流量 **下一跳关联到该客户端会话**（OpenVPN 语义以官方文档为准）。  
- **多段内网**：如 172.22.0.0/16 与 172.18.0.0/24 **分别** `iroute`（或合并为更大前缀仅在 **不泄露** 路由前提下采用，需产品确认）。

### 4.2 其它用户的路由

- 访问 **中心物理网**：继续依赖 **`push_lan_routes`** 等既有机制（通常在 **`server.conf`** 中生成 `push "route ..."`）。  
- 访问 **对端内网**：依赖 **对该客户端** 下发的 **`push "route <对端前缀>"`**；**对端 CIDR 变更** 后需 **重算** 所有 **依赖该前缀** 的 CCD/server 片段并落盘。  
- **生效**：**不承诺** 已连接客户端不重连即更新内核路由；UI **固定文案** 提示重连。

### 4.3 `push "route ..."` 写在 `server.conf` 还是 CCD？

**可以写在 CCD 里。** OpenVPN 的 **`client-config-dir`** 下、与客户端 **Common Name** 同名的文件中，除 `ifconfig-push`、`iroute` 等外，仍可写 **`push "route <网络> <掩码>"`**（与 `server.conf` 中语法相同）。区别：

| 位置 | 作用范围 |
|------|----------|
| **`server.conf`** | 对该实例 **所有** 客户端连接时推送（全局默认）。 |
| **某用户的 CCD 文件** | **仅对该用户** 在连接建立时推送；适合「只有部分客户端需要到达某对端网段」、或 **按用户维护** 与对端相关的路由，而不改全局 `server.conf`。 |

实现上可 **优先** 把「到对端内网」的 `push` 放在 **需要访问对端的用户的 CCD** 中，便于与对端 CIDR 变更 **联动重算**；全局 `push_lan_routes` 仍只表达中心侧 LAN。

### 4.4 CCD 会不会「挂在组下面」？按组推路由时用户从哪来？

- **OpenVPN 原生**：`client-config-dir` 下文件名 = 客户端 **Common Name（CN）**，与本项目一致即 **`username`**。CCD **不按组建目录**，**一条用户一条 CCD 文件**（`CCD_DIR/<username>`）。  
- **组**只存在于 **业务数据**：`User` 模型已有 **`group_id`**，用户 JSON 在 `data/users/`，组在 `data/groups/`。**不需要** 再维护一份独立的 `{ "组ID": ["用户", ...] }` JSON 作为主数据源——否则与用户增删改 **双写易不一致**。  
- **推荐做法**：提供 **`list_usernames_by_group(group_id)`**（或等价查询）：**扫描 `USERS_DIR` 下用户 JSON**，过滤 `group_id`（及 `status == active` 等）。对端策略变更后，对「授权访问某对端」的 **每个组** 展开为用户列表，再 **逐用户重写 CCD** 中的 `push "route ..."`（或删除不再需要的 push 行）。  
- **可选优化**：若用户量极大，可再增加 **派生缓存**（启动时或变更时重建内存索引）；**首期** 全表扫描即可。  
- **默认「所有人都要到对端」的路由**：用 **`server.conf` 全局 `push "route <对端前缀>"`** 即可，**不必** 给每个用户写 CCD；**删除默认、改按组** 后，从 server.conf 去掉该全局 push，改为只对选中组展开用户并写 **CCD**。

### 4.5 CCD 合并稳定性（增补）

- **实现**：`app/services/peer_instance/ccd_merge.py`。  
- **约定**：同一 CCD 内若并存 **对端专有块**（例如带 `ovpn-peer` 注释的 **`iroute`**）与 **`ovpn-mesh-peer-routes`** 等 **mesh / 推送块**，合并时 **写入顺序固定**（对端专有块在前、mesh 块在后等与实现一致），避免仅因块顺序漂移产生无意义字节差，从而误判「已变更」反复落盘同步。

### 4.6 绑定用户名唯一（增补）

- **产品**：同一时刻，一个 **`bound_username`（VPN 用户）至多绑定一个** 对端实例。  
- **实现**：服务端在创建/更新对端时校验；与 UI 错误提示一致。

## 5. 流量与 SNAT（与讨论稿一致）

约定：**SNAT** 指改源地址（常见 `MASQUERADE` 在 **POSTROUTING** 出隧道或出公网）。**中心侧** 默认 **不对**「VPN 客户端 ↔ 对端内网」互访做 SNAT（与现有「VPN 子网出公网 MASQUERADE」区分）。

**与实现对齐（中心访 LAN / 出网 NAT）**：中心 **`global_subnet` 出向 NAT** 现为 **`nat` 表 `POSTROUTING`：`-s <global_subnet> -j MASQUERADE`**（`ovpn-mgmt-masq` 注释），**无** `-o`（出接口）、**无** `-d` / `! -d`；写入路径仅经 **`rebuild_iptables()` / `IptablesManager.rebuild_rules`**，详见 **`vpn-lan-firewall-devicebind-2026-04-09/design.md`** §2、§4。

| 场景 | 典型是否 SNAT | 说明 |
|------|----------------|------|
| 中心 VPN → 对端物理网 | **否** | 源保持 VPN 虚拟地址（如 10.255.x.x）。 |
| 中心 VPN → 对端第三段内网 | **否** | 同上。 |
| 对端物理网 → 中心 VPN | **看对端配置** | 若对端 **出 tun 前 MASQUERADE**：**在对端 SNAT** 成对端隧道 IP；中心见 10.255 对端地址。若不 SNAT：源为真实内网，中心需路由 + 防火墙放行 **内网源**。 |
| 对端物理网 → 中心物理网 | **对端侧同左** | 经隧道到中心再转物理网；中心侧一般 **不再 SNAT**；除非另行设计 hairpin NAT（非默认）。 |

**防火墙与 SNAT 的关系**

- **默认宽松规则**（§3）与 **按组收紧**、**对端 SNAT 开/关** 叠加时：若对端 **MASQUERADE**，中心侧看到的源可能变为 **对端隧道 IP**，默认「`-s` 对端元数据 CIDR」规则需与 **实际抓包** 对齐，或拆成「隧道 IP + 内网 ipset」两条（实现任务中验证）。  
- **VPN → 对端内网** 路径仍 **通常无中心 SNAT**；对端侧默认规则以 **VPN 全局 `-s`** 为准。

## 6. SSH：自动 vs 手动

| 模式 | SSH | 程序职责（与当前代码一致处已标注） |
|------|-----|----------|
| **半自动（已落地）** | 可选 | **安装 OpenVPN**：**build 扳手** 先 **SSH 探测**，**已满足版本则跳过后续**；不足再安装（RHEL 可源码编译）；**配置推送**（**cloud_upload**）：`.ovpn` / **systemd** / **iptables**；**部署说明**：独立页 `/peers/manual`（见 **`vpn-peer-manual-page-2026-04-28`**）；删除对端时 **尽力** 远端按 `peer=` 清理。 |
| **分段编排（产品收口）** | — | 不要求单一按钮串联：**install**（build）、**配置推送**（客户端 / systemd / iptables）、新建编辑可选 **自动部署**，已形成闭环；历史上所称「全自动一键」不作为未完能力追踪（详见 **`tasks.md`**）。 |
| **手动** | 可选 | **部署说明**（独立页 + 下载 Markdown）；示例 iptables 与程序下发 **注释格式一致**；无 SSH 时仅依赖文档 + 中心侧同步。 |

**安全**：凭据 **明文存对端 JSON**（须限制 `data` 权限）；**主机密钥** 当前 **AutoAddPolicy**（生产建议 `known_hosts`）；加密存储与审计若单独立项则 **另起变更** 描述。

## 7. 对端 CIDR 变更的同步顺序（建议）

1. 持久化新 CIDR 列表。  
2. **重生成** 该用户 **CCD**（`iroute` + 相关 `push`）。  
3. **更新** 中心侧 **ipset**（对端前缀集合）及 **引用该 set 的 `VPN_PEER` 规则**（无需改注释 peer id）。  
4. **重算** 其它用户 CCD 中 **依赖旧前缀** 的 `push`（若有）。  
5. **经 SSH 刷新对端规则**：**当前不会在保存对端元数据后无条件自动执行**；变更 `global_subnet` / LAN / SNAT 等后，由运维在对端页使用 **「配置推送」**（或等价 API）写入远端（亦可手工改远端）。对端主机侧 **未使用 ipset**，规则以 **直写 `iptables`**、`peer=` 注释 为准（与 **`tasks.md`**「产品收口」一致）。  
6. UI 提示：**对端重连** + **其它用户重连** 后路由与会话状态收敛；**不承诺** 零停机瞬时切换。

### 7.1 保存对端实例：语义跳过副作用（增补）

- **动机**：用户在编辑页点击保存但 **未改动** LAN CIDR、中心侧放行 / **mesh 可见组**等与路由、防火墙相关的字段时，**不应**触发本机 **`iptables`** 局部刷新与 **全量 mesh CCD** 重写。  
- **实现**：`PeerService.update`（`app/services/peer_instance/service.py`）在写库前对上述字段做语义快照比对；仅在 **语义有变**时执行 `refresh_vpn_forward_only`、`sync_all_mesh_push_routes_in_ccd` 等路径（与日志「已同步 / 未变化」一致）。

## 8. 启停与清理

- **停止 / 删除对端实例**：  
  - 中心：按 **`peer=<id>`** 删除 ipset 成员、链上规则、空 set 销毁策略。  
  - 对端：SSH 执行 **对称清理**（若策略要求）。  
- **禁止** 按模糊子串删全表；与现网 **`ovpn-mgmt-*`** 清理相互独立。

## 9. 设备绑定（client-connect）

- 对端仍以 **客户端证书** 接入，**`push-peer-info`** 与现网一致。  
- **已验证**：Linux 系统上的 OpenVPN **客户端** 会传递 **IV 相关字段**，**无需** 为对端站点单独放宽「无 IV」逻辑；实现阶段仍按现有 `device_bind_mode` 联调即可。

## 10. `server.conf` 修改后是否必须重启实例？

- **产品结论（运维向）**：修改 **`server.conf` 后应重启对应 `openvpn@<实例名>`**（服务管理页「重启实例」），以保证监听端口、`push`、安全选项等 **整份配置** 与进程状态一致。  
- **技术说明**：OpenVPN 虽在部分版本/场景下可对服务端发 **SIGHUP** 做 **有限重载**，但 **并非所有指令** 都保证热更新；且 **`push` 等选项对「已在连」的客户端** 通常仍要 **重连** 才拿到新路由。为避免歧义，**本系统 UI 与文档统一写「保存后需重启生效」**（与现有配置编辑器提示一致）。  
- **CCD / 按用户文件**：仅改 **CCD** 时，部分环境依赖 **客户端重连** 或 **服务端对单连接刷新** 行为，仍以 **界面提示重连** 为主；不要求「只改 CCD 必重启服务端」，但 **`iroute` 等影响服务端内核路由** 的变更，**重启服务端更稳妥**（实现阶段可按官方行为微调提示文案）。

## 11. 服务管理页：启停 / 重启确认弹窗（与本需求一并交付）

- **问题**：`confirm_dialog` 按钮行 **右对齐**、停止确认 **正文过长**，阅读与点击体验差。  
- **目标**：弹窗 **宽度适中**；主按钮区 **底部水平居中**（取消 / 确认成组居中）；**停止** 确认正文 **一两句**（细则改由 **停止成功后的 notify** 或运维文档说明，与 `_do_stop` 已有提示对齐）。  
- **实现**：调整 `app/ui/components/confirm_dialog.py`（布局 + 可选 **确认按钮颜色**：停止=negative，启动/重启=primary）；缩短 `services.py` 中 `_confirm_stop` 的 `message` 字符串。

## 12. 依赖与风险

- **iptables-nft / legacy**：首期以 **iptables-save/restore 可见规则** 为准；若环境为 nft 后端，需单测或文档说明。  
- **权限**：管理端与 SSH 用户需 **CAP_NET_ADMIN** / sudo 才能完成对端写规则。  
- **并发**：同一 peer 的 CIDR 变更与 SSH 推送需 **串行化** 或锁，避免半更新状态。

## 13. 实现对照（代码路径，便于审阅）

| 能力 | 主要模块 |
|------|----------|
| 对端模型（SSH 凭据、`masquerade_on_peer`、`auto_install_on_peer`、`ssh_openvpn_binary`、**`bound_username`** 唯一性等） | `app/models/peer_instance.py` |
| CCD 合并（`iroute` / mesh 块顺序与稳定性，见 §4.5） | `app/services/peer_instance/ccd_merge.py` |
| SSH 连接 | `app/services/peer_instance/peer_ssh_connect.py` |
| 远端 OpenVPN 探测（可选对端字段 **`ssh_openvpn_binary` 优先路径**） | `app/services/peer_instance/remote_openvpn.py` |
| 远端 iptables 下发/清理 | `app/services/peer_instance/remote_peer_iptables.py` |
| 远端上传 .ovpn | `app/services/peer_instance/remote_peer_ovpn.py` |
| 远端安装 OpenVPN（与 `installer` 同源脚本） | `app/services/peer_instance/remote_peer_install.py` |
| Peer CRUD、mesh、**配置推送** / 安装入口、**先探测再按需安装**（`ensure_openvpn_on_peer_via_ssh`）、删除时远端尽力清理、`update` **语义比对**后再刷新 VPN_FORWARD/mesh（见 §7.1） | `app/services/peer_instance/service.py` |
| 手动部署说明（结构化 + Markdown） | `app/services/peer_instance/peer_manual_md.py`，`PeerService.export_peer_manual_markdown` / `export_peer_manual_context` |
| 对端 UI | `app/ui/pages/peers.py` |
| 对端部署说明路由 `/peers/manual` | `main.py` |
| 中心 VPN_PEER / ipset | `app/services/firewall/iptables_mgr.py` |

与 **`tasks.md`** 中「代码路径对照」表一致，可择一维护；本节约在 `design.md` 内闭环。
