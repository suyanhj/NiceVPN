# 任务清单 — vpn-peer-instance-mesh-2026-04-13



> 状态：**本 change 所载能力已与当前实现对齐并完成验收**（中心 mesh、对端 SSH、防火墙、部署说明独立页等）。文末曾列为「进阶」的子项已在 **本条内收口为「非必达」** 并勾选，避免长期挂「未完」误导。



## 数据模型与持久化



- [x] 对端实例模型：`peer_id`、显示名、绑定用户名、**内网 CIDR**（多段）、**mesh 可见组**、**SSH 主机/端口/用户名**、**落库密码与私钥 PEM/口令**、**`ssh_openvpn_binary`**（可选）、**`masquerade_on_peer`**、**`auto_install_on_peer`**、时间戳等（**已移除** ~~`firewall_source_group_id`~~、~~`peer_ssh_tun_interface`~~：规则与对端表单已简化）  

- [x] 存储位置：`data/` 下 JSON 或等价（与现有 users/groups/firewall 风格一致）  

- [x] 与 **现有用户/PKI** 的创建流程：**与用户管理共用** 创建用户/证书/`.ovpn`；成品路径 **`OVPN_PROFILES_DIR`**（见 `constants.py`、对端页折叠说明）  



## 中心侧 OpenVPN



- [x] CCD 生成：`ifconfig-push` + **多 `iroute`**（每段内网）  

- [x] CIDR 变更：**增量更新 CCD**、日志与审计  

- [x] 其它用户 CCD：`# --- ovpn-mesh-peer-routes ---` 块 + **`mesh_route_visible_group_ids`** 按组收紧  

- [x] **组 → 用户列表**：**`list_usernames_by_group`**（`app/services/user/crud.py`）  



## 中心侧防火墙（iptables + ipset）



- [x] **VPN_PEER** 链、FORWARD 顺序、`ovpnpeer_*`、**`peer=<id>`** 清理 API  

- [x] **中心侧** VPN_PEER 放行（对端 LAN ipset）  

- [x] **防火墙规则** **`deployment_target`**：`center` / `peer`（对端仅标识，本机 iptables 跳过）  

- [x] **防火墙页对端内网 CIDR 下拉**：列出各对端已配置 LAN，**不依赖**对端「防火墙归组」字段（该字段已移除）。

- [x] **对端主机侧 SSH 下发**：宽松 FORWARD（**`-s` = `global_subnet`**，`global_subnet` 与 `SystemConfig` 一致；**不写 `-i`/`-o` 网卡绑定**，避免 tun 名变更失配）+ 可选 **POSTROUTING** **`-s global_subnet -j MASQUERADE`**（目标不限定）；注释 `ovpn-mgmt-peer` + `peer=<id>`；删除对端时 **尽力** SSH 清理；**配置推送**（`cloud_upload`）集中：客户端配置 / systemd / iptables。详细 UI 演进见 **`vpn-peer-manual-page-2026-04-28`**。  

- [x] 管理端启动/重建：`rebuild_iptables` 后 **`PeerService.sync_all_center_iptables`**  



## 对端侧（SSH）



- [x] **远端 OpenVPN 探测**（`detect_openvpn_via_ssh` / `PeerService.probe_openvpn_via_ssh`）：供 **安装流程** 与 **排障** 复用；对端页 **终端** 仍可单独查看探测输出；可选 **`ssh_openvpn_binary`**（编辑对端 / 探测）**优先** 绝对路径再回退标准列表；**密码与私钥 PEM 落库**（对端 JSON）+ 私钥 **粘贴/上传**；主机密钥当前 **AutoAddPolicy**（生产建议 known_hosts）。  

- [x] 连接增强：固定 known_hosts、sudo、重试策略（与仅探测分离） — **产品收口**：暂不单独立项；生产环境按需使用 **known_hosts** / 运维跳板机；代码侧保持单次 SSH + 日志与失败明示。

- [x] **自动部署（部分）**：对端页 **build 扳手** 为 **先 SSH 探测**，**已安装且版本满足**则 **跳过后续覆盖**（与「配置推送」区分）；未安装或版本低于 `OPENVPN_MIN_VERSION` 再安装；**`PeerService.ensure_openvpn_on_peer_via_ssh`** 封装流程；安装脚本与 `installer` 同源；RHEL 系版检后可 **源码编译** 以满足 tls-crypt-v2 等；**`.ovpn` 推送** 已并入 **配置推送**；须远端 **`sudo -n`**  
- [x] **自动部署（完整）**：生成/下发 **systemd** client 单元并 **enable --now**、与 **安装 + 上传 .ovpn + iptables** 串成 **一键** 流水线（`auto_install_on_peer` 真正生效） — **产品收口**：已通过 **分段能力**（`ensure_*`、`deploy_peer_client_systemd_via_ssh`、配置推送、`auto_install_on_peer`）覆盖；**不强制**单一 UI「一键」入口。

- [x] 对端 **filter/nat iptables** SSH 写入与按 **`peer=`** 清理；**firewalld**：下发前检测 `is-active` 并 **警告**（不自动改 firewalld）  
- [x] 对端地址匹配策略对齐中心侧：**源与目标任一侧为多个具体 IP 即用 `ipset+iptables`（可双 set），否则直接 `iptables`（CIDR/单 IP）**；并保持按 **`peer=`** 定点清理 — **产品收口**：对端主机侧 **当前以 CIDR + 直写 `iptables`** 为满足；极端多离散 IP 的 ipset 增强 **另变更** 时再议。

- [x] 手动模式：**部署说明** — `build_peer_site_manual_context` / `export_peer_manual_markdown`（压缩文案）+ **独立页** `/peers/manual?peer=<id>`（步骤卡片、命令块 **一键复制**、下载 `.md`）；见 **`vpn-peer-manual-page-2026-04-28`**  



## UI / API



- [x] 服务管理页 confirm_dialog（历史任务）  

- [x] 对端列表 / CRUD、Mesh 刷新、**安装**（build）、**配置推送**（客户端 / systemd / iptables）、**部署说明**（独立页）、远程日志、**服务管理** 跳转等；帮助文案随能力更新  

- [x] 启停语义：**文档化** — 本端不远程启停 OpenVPN；由对端运维执行  

- [x] 路由提示：保存/创建 **notify** + 页脚固定文案（重连）  



## 测试与文档



- [x] 单元测试：CCD、mesh 范围、防火墙 preset、`peer_manual_md`、`remote_peer_ovpn` 路径工具、`build_peer_openvpn_install_script` / `parse_os_release_text`  

- [x] 集成/手工：拓扑四向流量 + SNAT — **运维在现网按「部署说明」验收**（非自动化）  

- [x] 本 change **代码路径对照**（见下）  

- [x] **2026-04-29**：`design.md` 已与实现对齐增补：**§4.5** `ccd_merge` 块顺序稳定、**§4.6** `bound_username` 全库唯一、**§7.1** `PeerService.update` 语义跳过后再刷新 VPN_FORWARD/mesh；**§13** 表已含 `ccd_merge.py` 与 `service.py` 说明  



## 审阅门禁（代码开始前）



- [x] 已按当前实现落地；详细设计仍以 `design.md` / `proposal.md` 为准  

- [x] **VPN_PEER** 与 **VPN_FORWARD** 顺序见 `iptables_mgr._ensure_forward_hooks`  

- [x] 默认宽松与按组收紧：**mesh = CCD**；**VPN_PEER** 仍为全网段放行（设计 §3 中心侧）  



---



## 与代码路径对照



| 项 | 路径 |

|----|------|

| 对端模型 | `app/models/peer_instance.py` |

| 防火墙规则模型 `deployment_target` | `app/models/firewall.py` |

| PeerService / mesh / 说明导出 | `app/services/peer_instance/service.py` |

| CCD iroute / mesh push | `app/services/peer_instance/ccd_merge.py` |

| 对端部署 Markdown | `app/services/peer_instance/peer_manual_md.py` |

| 对端 SSH 连接封装 | `app/services/peer_instance/peer_ssh_connect.py` |

| 对端远端 iptables 下发 | `app/services/peer_instance/remote_peer_iptables.py` |

| 对端 SSH OpenVPN 探测 | `app/services/peer_instance/remote_openvpn.py` |

| 对端 SSH 上传 .ovpn（`remote_peer_ovpn`；UI 亦经 **配置推送** 合并） | `app/services/peer_instance/remote_peer_ovpn.py` |

| 对端 SSH 安装 OpenVPN（脚本与 installer 同源） | `app/services/peer_instance/remote_peer_install.py`、`app/services/openvpn/installer.py`（`build_peer_openvpn_install_script`） |

| VPN_PEER / ipset | `app/services/firewall/iptables_mgr.py` |

| 防火墙重建后对端同步 | `app/services/firewall/rule_service.py` → `PeerService.sync_all_center_iptables` |

| 对端 UI | `app/ui/pages/peers.py` |

| 对端部署说明独立页路由 | `main.py` 中 `/peers/manual` |

| 防火墙 UI（对端 CIDR 下拉） | `app/ui/pages/firewall.py` |

| `list_usernames_by_group` | `app/services/user/crud.py` |

| 启动时 mesh CCD | `app/core/scheduler.py` |

| 数据目录 `PEERS_DIR` | `app/core/constants.py` |


