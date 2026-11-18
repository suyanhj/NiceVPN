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
| `data/logs/sso-auth.jsonl` | 企业 SSO 认证结果；可在「服务管理 → 本机实例 → 认证日志」查看 |

事件使用扁平 JSON，每行一条。公共字段包括 `schema_version`、`event_time`、`event_source`、`event_dataset`、`event_type` 和 `vpn_instance`。

会话日志还包含用户名、VPN 地址、客户端地址、传输协议、流量和连接时长；conntrack 日志包含源/目标 IP、端口、协议及 DESTROY 时的包数和字节数。用户名由 CCD 固定虚拟 IP 映射补充。

## OpenVPN 连接/断开钩子容错与开销

- `client-connect` 执行 `device-bind.sh`：只有实例配置实际包含 `management-client-auth` 才尝试调用 `sso_grant.py`；否则不启动 SSO 检查进程。
- 部署时会固定 NiceVPN Python 解释器，连接/断开时仅检查解释器存在，不再每次额外启动 `python -c` 探测版本。
- `vpn_session_audit.py` 的开始/结束事件为 **best effort**：Python 缺失、审计脚本缺失、日志不可写时记录警告，但不影响合法 VPN 连接和断开清理。失败可能导致该条会话审计缺失，应监控 `openvpn-device-bind.log` 与 OpenVPN 断开钩子的 stderr。
- **安全策略不放宽**：证书和 SSO 继续由 OpenVPN/NiceVPN 原有路径验证；设备绑定所需的 Python/JSON 写入失败、指纹不匹配或强制模式缺少指纹，仍拒绝连接。
- 正常 `weak_log` 连接且未启用 SSO 时，只需启动一次 Python 写入会话审计；`strict_hwaddr`/通用绑定有有效指纹时，额外启动一次绑定记录 Python。

## Fluent Bit / VictoriaLogs

- 使用 JSON parser 采集活动中的两个 VPN JSONL 文件及需要的 SSO 日志。
- 为 Tail Input 配置持久化 DB 和合理的 `Rotate_Wait`。
- 不要同时采集活动文件和其 gzip 归档，避免重复入库。
- `vpn_username`、IP、端口等是高基数字段，不建议作为 VictoriaLogs stream 标签。
- conntrack 是连接级事件，不是逐包流量日志。
