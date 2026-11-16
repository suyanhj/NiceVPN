# 功能概览

NiceVPN 通过 OpenVPN 提供远程接入，并集中管理账号、证书、网络策略和对端站点。

## 核心功能

- **接入与证书**：OpenVPN、EasyRSA、CA/CRL、客户端 OVPN、固定虚拟 IP。
- **用户与组**：用户生命周期、组网段、批量创建、设备绑定。
- **标签**：用户和组可关联标签，标签可联动防火墙与 Mesh。
- **对端站点**：通过 VPN 用户接入站点 LAN，配置 CCD/iroute、路由可见范围和远端防火墙。
- **防火墙**：中心与对端 iptables / ipset 规则。
- **身份认证**：可选企业 SSO；VPN 网络权限仍由证书、组、标签和防火墙决定。
- **运维**：服务状态、日志、审计、备份恢复。
- **接口**：Web 管理、CLI 和公网 API。

## 运行结构

一个 NiceVPN Python 进程提供两个 HTTP 入口：

| 端口 | 功能 |
|---|---|
| 8880 | NiceGUI 管理界面 |
| 8881 | FastAPI 公网 API、一次性下载和 SSO 回调 |

OpenVPN 服务由系统服务管理。NiceVPN 不替代 OpenVPN 的 TLS、证书和隧道处理。

## 深入说明

- [安全与身份认证](security.md)
- [标签与防火墙](firewall-tags.md)
- [数据与迁移](storage.md)
- [日志与审计](logging.md)
- [公网 API](../api.md)
- [CLI](../cli.md)
