# OpenVPN 管理系统 — 产品与能力说明

面向 **希望了解能做什么、怎么做** 的读者；实现细节以代码与 [OpenSpec 变更目录](../openspec/changes/) 为准。

公网 HTTP 接口见项目根目录 **[api.md](../api.md)**。

---

## 接入与安全

- **TLS 与隧道**：服务端配置由系统生成（含 **tls-crypt-v2**、`topology subnet`、`client-config-dir` CCD、管理端口等），与 **EasyRSA** PKI、**CRL**、多端 `.ovpn` 下发配套。
- **客户端内网分流**：按系统配置的 **`push_lan_routes`** 向客户端下发 `push route`（不写全隧道 `redirect-gateway` 时，可实现「仅所列网段经 VPN 到达中心侧局域网」）；与中心侧 **NAT / FORWARD / INPUT（tun+）** 等数据面策略联动（详见 OpenSpec `vpn-lan-firewall-devicebind` 等设计）。

## 设备绑定（一连接一档案）

- **`client-connect` 脚本**：每路连接在服务端执行设备绑定逻辑，与 OpenVPN 环境变量中的 **IV\_\*** 客户端指纹字段配合。
- **策略模式**（系统设置中可改，落盘 `/etc/openvpn/mgmt/device_bind_mode`）：
  - **仅审计**：记录连接，不据指纹拒绝。
  - **弱指纹（默认）**：在硬件 MAC（**IV_HWADDR**）可用时优先使用；否则按平台退化（如 iOS/Mac/Windows 使用 **UV_UUID**，安卓使用 **IV_PLAT / IV_PLAT_VER** 等组合），实现「尽量稳定的一机一档」而不强制所有客户端都上报 MAC。
  - **强绑定**：必须 **IV_HWADDR**，无则拒绝（部分移动端可能不兼容，需谨慎）。
- **一账号一侧写**：同一 VPN 用户名对应绑定档案；换机或换配置后可由控制台或 **`/api/vpn` 按前缀重置设备绑定**，清除后允许新设备重新登记。

## 对端站点与多端 Mesh 组网

- **对端实例**：将某一 **VPN 用户（CCD 同名）** 标识为「站点」，配置其 **后方内网 CIDR**（可多段）；系统在 **CCD** 中写入 **`ifconfig-push`（固定隧道地址）** 与 **`iroute`**，使中心将去往该前缀的流量交给该客户端会话（OpenVPN 语义）。
- **中心访问对端内网**：依赖上述 **iroute** + 中心侧 **VPN_FORWARD / iptables** 等与对端 LAN 关联的规则；可在对端维度上配置是否在中心本机合并放行。
- **Mesh 可见性**：支持按 **用户组** 配置「哪些对端的内网路由应对哪些组的用户下发」；系统在用户 CCD 中维护 **mesh 段的 `push "route …"` 块**，使 **站点之间经中心互访对端内网**（路由变更后客户端通常需 **重连** 才一致，以界面提示为准）。
- **远端运维**：对端可配置 **SSH**、可选 **远端 OpenVPN / iptables** 协作（参数与能力以「对端站点」页与实际服务代码为准）。

## 用户、组与分发

- **组**：策略域、子网划分、与防火墙规则源子网等联动。
- **用户**：单人或 **批量前缀**（`base`、`base_1`…）建号；**一次性下载链接** 分发 `.ovpn` 或 zip；支持 **批量导入**、控制台与 **CLI**（`python main.py cli` / `cli.py`）。

## 防火墙与规则

- **iptables + ipset**：规则 JSON 落库、重建链路与对端关联清理；支持 **简单规则导入**、与多 IP 场景下的 **ipset** 优化路径。

## 证书与通知

- **证书页**：与 PKI/到期策略等运维能力配合（见证书服务与定时任务）。
- **通知**：**钉钉**、**企业微信** 等通道可按配置推送（插件式注册表）。

## Web 运维与公网 API

- **服务管理**：查看实例状态、日志，与本机 **systemctl** 启停协作（Linux 部署）。
- **公网 API**：HTTP Basic 下 **创建 / 批量创建 / 按前缀删除 / 重置设备绑定**；批量创建返回与单次相同响应结构，合并 **`vpns_*.zip`**。详见 [api.md](../api.md)。

---

## 功能菜单（与 Web 侧一致）

| 模块 | 说明 |
|------|------|
| 初始化向导 | 环境检测、PKI、地址池与内网 `push_lan_routes`、默认接入实例 |
| 用户 / 组 | 账号生命周期、设备绑定状态、批量命名、下载链接 |
| 对端站点 | 绑定用户、LAN CIDR、CCD、中心转发与 Mesh 路由 |
| 防火墙 | iptables / ipset、规则与导入 |
| 服务管理 | OpenVPN 实例与本机 `systemctl`（依部署环境） |
| 系统设置 | `device_bind_mode`、`download_base_url`、API Basic、通知等 |
| 公网 API | `POST/DELETE /api/vpn/...`、见 [api.md](../api.md) |
