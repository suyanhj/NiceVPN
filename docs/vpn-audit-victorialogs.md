# VPN 审计日志字段约定

本文档约定 VPN 会话日志、conntrack 连接日志进入 Fluent Bit / VictoriaLogs 后的统一字段。

## Stream 字段建议

VictoriaLogs stream 字段应保持低基数，建议使用：

```text
schema_version
event_source
event_dataset
event_type
vpn_instance
```

不建议把 `vpn_username`、`vpn_virtual_ip`、`dst_ip`、`dst_port` 作为 stream 字段，这些字段基数高，适合作为查询过滤字段。

## 公共字段

所有 VPN 审计事件统一保留以下字段：

```text
schema_version     固定为 vpn_audit_v1
event_time         事件时间，UTC ISO8601 毫秒格式
event_source       openvpn / conntrack
event_dataset      vpn.session / vpn.connection
event_type         vpn_session_start / vpn_session_end / vpn_connection_new / vpn_connection_end
vpn_instance       OpenVPN 实例名
vpn_username       VPN 用户名，连接日志可由 virtual_ip 关联补充
vpn_virtual_ip     VPN 虚拟 IP
```

## VPN 会话事件

由 OpenVPN `client-connect` / `client-disconnect` 钩子输出到：

```text
/etc/openvpn/log/openvpn-session-audit.jsonl
```

字段：

```text
schema_version
event_time
event_source=openvpn
event_dataset=vpn.session
event_type=vpn_session_start | vpn_session_end
vpn_instance
vpn_username
vpn_virtual_ip
client_real_ip
client_real_port
transport_proto
bytes_received
bytes_sent
duration_seconds
openvpn_time_ascii
openvpn_time_unix
openvpn_script_type
openvpn_config
openvpn_daemon_pid
```

示例：

```json
{"schema_version":"vpn_audit_v1","event_time":"2026-06-08T08:01:02.123Z","event_source":"openvpn","event_dataset":"vpn.session","event_type":"vpn_session_start","vpn_instance":"server","vpn_username":"huangjiang","vpn_virtual_ip":"10.8.0.23","client_real_ip":"1.2.3.4","client_real_port":54321,"transport_proto":"udp","bytes_received":null,"bytes_sent":null,"duration_seconds":null,"openvpn_time_ascii":"Mon Jun  8 08:01:02 2026","openvpn_time_unix":1780905662,"openvpn_script_type":"client-connect","openvpn_config":"/etc/openvpn/server/server.conf","openvpn_daemon_pid":1234}
```

## conntrack 连接事件

VPN 项目启动时会在 Linux 环境下拉起后台监听器，执行：

```bash
conntrack -E -o timestamp
```

监听器只记录原始方向源地址属于 VPN 全局网段的 `NEW` / `DESTROY` 事件，并输出到：

```text
/etc/openvpn/log/openvpn-connection-audit.jsonl
```

字段：

```text
schema_version=vpn_audit_v1
event_time
event_source=conntrack
event_dataset=vpn.connection
event_type=vpn_connection_new | vpn_connection_end
vpn_instance
vpn_username
vpn_virtual_ip
src_ip
src_port
dst_ip
dst_port
transport_proto
network_proto
packet_count
byte_count
conntrack_event
conntrack_timestamp
```

字段说明：

```text
vpn_virtual_ip  <- conntrack 原始方向 src，且必须属于 VPN 全局网段
vpn_username    <- 由 CCD 固定虚拟 IP 映射补充
src_ip          <- conntrack 原始方向 src
src_port        <- conntrack 原始方向 sport
dst_ip          <- conntrack 原始方向 dst
dst_port        <- conntrack 原始方向 dport
transport_proto <- tcp / udp / icmp
network_proto   <- 当前仅记录 ipv4
packet_count    <- DESTROY 事件中 conntrack 原始方向 packets，NEW 事件通常为空
byte_count      <- DESTROY 事件中 conntrack 原始方向 bytes，NEW 事件通常为空
```

示例：

```json
{"schema_version":"vpn_audit_v1","event_time":"2026-06-08T09:21:00.123Z","event_source":"conntrack","event_dataset":"vpn.connection","event_type":"vpn_connection_new","vpn_instance":"server","vpn_username":"huangjiang","vpn_virtual_ip":"10.8.0.23","src_ip":"10.8.0.23","src_port":53124,"dst_ip":"1.2.3.4","dst_port":443,"transport_proto":"tcp","network_proto":"ipv4","packet_count":null,"byte_count":null,"conntrack_event":"NEW","conntrack_timestamp":"2026-06-08T09:21:00.123456+0000"}
```

conntrack 方案是连接级事件，不是包级日志。Fluent Bit 只需要按 JSON 解析这两个 JSONL 文件并写入 VictoriaLogs。
