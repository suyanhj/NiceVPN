# Tasks: vpn-bugfix-2026-04

- [x] OpenSpec 变更目录与 proposal/design/tasks 落盘
- [x] 初始化：PKI 阶段生成服务端证书 (server.crt/key)、默认防火墙规则、`instances` 写入配置、大/小前缀子网划分兜底
- [x] 组：子网校验短路（不在全局范围则直接返回）、禁止删除默认组、组 ID 完整展示 + 复制按钮
- [x] 证书：`CertService._get_wrapper` 使用 `easyrsa_dir`（修复 Box TypeError）、续签后同步 `cert_serial` 到用户 JSON
- [x] 防火墙：全 owner 合并重建 iptables、ipset 源匹配、多端口（逗号分隔 → multiport）、空 owner 列出全部规则、侧栏 Drawer 表单
- [x] 防火墙：归属类型切换（组/用户）、源 CIDR 自动获取开关、优先级插入/追加模式
- [x] 用户：批量创建（数量 + 后缀命名）、模糊搜索、单个/批量设备绑定重置、单行信息布局 + 简易 SVG 流量指示
- [x] 服务页：扫描 `/etc/openvpn/*.conf` 发现未注册实例，即使未启动也可管理
