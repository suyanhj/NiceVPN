# NiceVPN 审计日志轮转

本配置只管理 NiceVPN 写入的三个文件，不管理 OpenVPN 自己的 server.log / server-status.log，也不修改现有 JSONL 内容。

| 文件 | maxsize | 写入入口 |
| --- | --- | --- |
| openvpn-device-bind.log | 16MiB | device-bind.sh |
| openvpn-session-audit.jsonl | 64MiB | vpn_session_audit.py |
| openvpn-connection-audit.jsonl | 128MiB | conntrack 审计线程 |

## 轮转行为

- systemd timer 每 10 分钟检查；daily + maxsize，文件非空时至少按日检查并轮转，达到容量阈值时可提前轮转。
- 最多 14 份归档，最多 7 天，压缩且延迟一轮压缩，给 Fluent Bit 留读取机会。
- 使用 rename + create 而非 copytruncate；三个写入器每个事件重新打开文件并追加，不要求重启 OpenVPN 或 NiceVPN。
- 新文件创建为 0644 nobody:主属组；不同发行版可能是 nobody 或 nogroup，安装器动态识别。
- 阈值是每次检查时判断，**不是严格文件大小硬上限**。
- 不采用 /etc/logrotate.d 以免和系统默认轮转进程双重执行；独立配置及独立 state 文件。

## 安装

在有 systemd 的宿主机使用 root 执行：

    # Debian/Ubuntu
    apt-get install -y logrotate

    # Rocky/RHEL
    dnf install -y logrotate

    bash deploy/shell/install-nicevpn-audit-logrotate.sh
    systemctl list-timers nicevpn-audit-logrotate.timer

重新执行 ./start.sh daemon 时会自动尝试安装/更新这个定时器。没有 logrotate 时只提示警告，不阻断主服务。

Docker 部署应在**宿主机**启用定时器，而不是在无 systemd 的容器内：

    NICEVPN_AUDIT_LOG_DIR=/path/on/host/openvpn/log \
      bash deploy/shell/install-nicevpn-audit-logrotate.sh

确保宿主机日志目录的实际所有者/权限可供 nobody 用户重命名文件（尤其容器 UID 映射）。

## 验证

    # 仅模拟，不修改生产文件
    logrotate -d -s /var/lib/logrotate/nicevpn-audit.status \
      /etc/nicevpn/logrotate-audit.conf

    # 只检查是否达到阈值，不强制轮转
    systemctl start nicevpn-audit-logrotate.service

    systemctl status nicevpn-audit-logrotate.timer --no-pager
    journalctl -u nicevpn-audit-logrotate.service -n 50 --no-pager
    ls -lh /etc/openvpn/log/openvpn-*.log* /etc/openvpn/log/openvpn-*.jsonl*

不建议用 logrotate -f 直接强制处理生产审计文件，避免在 Fluent Bit 已积压时提前移动日志。

## Fluent Bit / VictoriaLogs 兼容性

- 对各个 Tail 输入配置独立稳定的 DB 来保存 inode/offset，并检查 Rotate_Wait（例如 30 秒）。
- Tail Path 应仅包含当前活动文件，不要匹配归档，防止重复消费。
- 高流量或采集积压场景应评估归档保留时间、数量和压缩时机；启用轮转前确认现有积压已追平。
- rename + create 可以避免 copytruncate 的复制/截断窗口，但不能保证审计事件零丢失：竞态、采集积压和磁盘/进程故障仍有风险。严格无损需要独立的可靠采集及持久化确认。
