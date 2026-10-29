# Spec Delta

## Purpose

定义 NiceVPN 结构化管理元数据迁移到 SQLite 后的单一事实源、自动升级、回滚与文件边界。

## ADDED Requirements

### Requirement: 结构化管理元数据使用 SQLite 单一事实源
系统 SHALL 使用 `data/nicevpn.db` 持久化 Users、Groups、Peers 与中心 Firewall Rules，迁移完成后 SHALL NOT 在正常运行路径读取或写入这些对象的旧 JSON。

#### Scenario: 升级后读取用户
- **WHEN** 数据库已经完成迁移
- **THEN** UserService 从 SQLite 返回用户数据，且不扫描 `data/users/*.json`

#### Scenario: 创建中心规则
- **WHEN** 管理员创建中心 Firewall Rule
- **THEN** 规则写入 SQLite，并继续按原语义刷新 VPN_FORWARD

### Requirement: 旧 JSON 在首次升级启动时自动迁移
系统 SHALL 在正式数据库不存在时检测旧 JSON，严格校验并一次性导入 SQLite。

#### Scenario: 旧实例首次启动新版本
- **WHEN** `nicevpn.db` 不存在且存在 legacy JSON
- **THEN** 系统建立临时数据库、导入并校验全部数据、原子启用 SQLite，然后归档旧 JSON

#### Scenario: 迁移数据损坏
- **WHEN** 任一 legacy JSON 无法解析或不满足对应模型
- **THEN** 系统中止迁移并拒绝继续启动，正式数据库不生效，原 JSON 保持可恢复状态

### Requirement: 不产生长期双写
系统 SHALL NOT 提供 JSON/SQLite 双写或可切换持久化模式。

#### Scenario: SQLite 已存在
- **WHEN** 后续再次启动
- **THEN** 系统直接打开并升级 SQLite schema，不重新导入 legacy JSON

### Requirement: OpenVPN 原生运行时文件保持文件语义
系统 SHALL 保持 CCD、Device Bindings、PKI/CRL、OVPN、远端 chain cache 与日志的原有文件协议。

#### Scenario: Device Binding 更新
- **WHEN** OpenVPN client-connect 脚本写入设备绑定
- **THEN** 仍写入 `/etc/openvpn/mgmt/device_bindings/*.json`，不要求访问 SQLite

### Requirement: Firewall 写入失败可回滚
系统 SHALL 在中心规则写入后若 iptables/VPN_FORWARD 重建失败，将 SQLite 中规则恢复为本次操作前快照。

#### Scenario: 单 owner 修改后重建失败
- **WHEN** 规则修改已写入 SQLite 但 VPN_FORWARD 刷新抛出异常
- **THEN** 该 owner 的 SQLite 规则恢复到修改前内容，并把异常继续返回调用方
