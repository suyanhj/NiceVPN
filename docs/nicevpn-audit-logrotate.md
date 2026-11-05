# NiceVPN / OpenVPN 日志轮转

## 目的与范围

使用系统标准的 `/etc/logrotate.d/nicevpn-openvpn`，由发行版的
`logrotate.timer` 或 `cron.daily` 调度；不再安装额外的 NiceVPN 轮转定时器。
系统的 `/etc/logrotate.conf` 需包含 `include /etc/logrotate.d`。

| 文件（默认目录 /etc/openvpn/log） | 最大轮转触发值 | 写入机制 | 轮转方式 |
| --- | ---: | --- | --- |
| openvpn-device-bind.log | 16MiB | NiceVPN Shell 连接钩子 | rename + create |
| openvpn-session-audit.jsonl | 64MiB | NiceVPN Python 连接/断开钩子 | rename + create |
| openvpn-connection-audit.jsonl | 128MiB | NiceVPN conntrack 审计线程 | rename + create |
| server.log | 128MiB | OpenVPN log-append 持续写入 | copytruncate |

**server-status.log 不轮转**：OpenVPN 每 30 秒重写一次的当前状态文件，不是历史日志。

- `daily` + `maxsize`，由系统调度周期触发检查；
  `maxsize` 不是严格硬上限。发行版通常每日检查一次，高流量环境在检查间隔内可能长到几百 MB。
- `compress` 默认通过 gzip 压缩成 `.gz`，`delaycompress` 延迟一轮，留出 Fluent Bit 消费时间。
- `rotate 14`、`maxage 7` 控制保留上限；无新轮转时，历史文件的到期清理可能要等下一次轮转。
- OpenVPN server.log 持有长期打开的文件描述符，使用 `copytruncate` 避免重启 VPN，
  但复制和截断之间存在极小的丢日志窗口。NiceVPN 三个按事件重新打开的文件无需 `copytruncate`。
- 新建的 NiceVPN 三个日志使用 `nobody` 用户及系统实际的主属组；旧的应用定时清理不会主动删除这四个活动文件。

## 安装

OpenVPN **服务端**安装器（Debian/Ubuntu、RHEL/Rocky）会一并安装系统 logrotate 包，
本地安装结束后尝试部署配置；执行 `./start.sh daemon` 也会更新系统轮转配置。
如果尚未安装包或目录权限不满足，会输出警告，不影响 OpenVPN 服务运行。

对于现有安装，可手动执行：

```bash
# Debian / Ubuntu
apt-get install -y logrotate
# RHEL / Rocky / AlmaLinux
dnf install -y logrotate

bash deploy/shell/install-nicevpn-logrotate.sh
```

systemd 主机可检查：

```bash
systemctl status logrotate.timer --no-pager
systemctl list-timers logrotate.timer
grep -E '^[[:space:]]*include[[:space:]]+/etc/logrotate.d' /etc/logrotate.conf
```

在容器运行的 NiceVPN 不应在无 systemd 的容器里装定时器；需在有调度器的宿主机
安装 `logrotate` 并操作宿主机实际挂载目录，例如：

```bash
NICEVPN_OPENVPN_LOG_DIR=/path/to/host/openvpn/log bash deploy/shell/install-nicevpn-logrotate.sh
```

注意容器内的 nobody UID/GID 与宿主机可能不同，应验证实际目录权限。
如果服务器已有其他 logrotate 配置管理同一路径，必须先排除重复配置。

## 验证

```bash
# 模拟校验所有系统策略，不做实际轮转
logrotate -d /etc/logrotate.conf

# 仅实际检查标准调度该次是否达到阈值，不强制轮转
logrotate /etc/logrotate.conf

ls -lh /etc/openvpn/log/server.log /etc/openvpn/log/openvpn-*
```

不要在有 Fluent Bit 未消费积压时运行 `logrotate -f` 强制轮转。
`logrotate` 不会备份数据库，也不会更改 VPN status 文件。

## Fluent Bit / VictoriaLogs

- Tail Input 各自使用稳定 `DB` 路径持久化 inode/offset，配置适当的 `Rotate_Wait`。
- 采集 Path 精确指向活动 JSONL 文件；不要同时采集压缩归档，防止重复消费。
- 首次启用时已有 700MB 级别的日志，请先确认日志采集没有积压，合理配置归档数量、压缩延迟。
- 单纯基于文件轮转并不构成端到端零丢失审计保证；对审计完整性有严格要求时需要额外持久化与可靠采集设计。
