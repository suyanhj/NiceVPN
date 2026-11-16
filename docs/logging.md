# 日志与审计

## 日志轮转

`./start.sh daemon` 会安装系统 logrotate 策略，由系统 `logrotate.timer` 或 `cron.daily` 调度。

默认目录：`/etc/openvpn/log`。

| 文件 | 触发大小 | 方式 |
|---|---:|---|
| `openvpn-device-bind.log` | 16 MiB | rename + create |
| `openvpn-session-audit.jsonl` | 64 MiB | rename + create |
| `openvpn-connection-audit.jsonl` | 128 MiB | rename + create |
| `server.log` | 128 MiB | copytruncate |

每日检查，达到 `maxsize` 时轮转；压缩旧文件，最多保留 14 份且不超过 7 天。大小不是实时硬上限。OpenVPN 的 `server-status.log` 是当前状态文件，不轮转。

手动安装或验证：

```bash
bash deploy/shell/install-nicevpn-logrotate.sh
logrotate -d /etc/logrotate.conf
```

容器部署应在有调度器的宿主机轮转实际挂载的日志目录，不要依赖无 systemd 的容器定时器。

## 审计日志

| 文件 | 内容 |
|---|---|
| `/etc/openvpn/log/openvpn-session-audit.jsonl` | OpenVPN 连接、断开、设备绑定结果 |
| `/etc/openvpn/log/openvpn-connection-audit.jsonl` | VPN 网段发起的 conntrack NEW / DESTROY 连接事件 |
| `data/logs/sso-auth.jsonl` | 企业 SSO 认证结果 |

事件使用扁平 JSON，每行一条。公共字段包括 `schema_version`、`event_time`、`event_source`、`event_dataset`、`event_type` 和 `vpn_instance`。

会话日志还包含用户名、VPN 地址、客户端地址、传输协议、流量和连接时长；conntrack 日志包含源/目标 IP、端口、协议及 DESTROY 时的包数和字节数。用户名由 CCD 固定虚拟 IP 映射补充。

## Fluent Bit / VictoriaLogs

- 使用 JSON parser 采集活动中的两个 VPN JSONL 文件及需要的 SSO 日志。
- 为 Tail Input 配置持久化 DB 和合理的 `Rotate_Wait`。
- 不要同时采集活动文件和其 gzip 归档，避免重复入库。
- `vpn_username`、IP、端口等是高基数字段，不建议作为 VictoriaLogs stream 标签。
- conntrack 是连接级事件，不是逐包流量日志。
