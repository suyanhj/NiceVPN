# 设备绑定豁免

保留现有三种模式：仅记录（weak_log）、通用绑定（weak_fingerprint）、强制绑定（strict_hwaddr）。在系统设置 → 设备绑定策略中启用设备绑定豁免后，只在后两种模式生效；默认关闭。

## 授权方式

- 新建/编辑用户组时可启用豁免；组内 active 用户自动获豁免。
- 新建/编辑标签时可启用豁免；直接关联该标签的用户和所属组关联该标签的用户获豁免。
- 命中任一即可；审计来源优先级：用户直接标签 > 组标签 > 组自身属性。
- 全局关闭仅让豁免失效，不清理组和标签设置；仅记录模式保持原有逻辑。

## 运行时与安全

- SQLite（0600）是配置源；schema v5 为 tags 增加 device_binding_exempt 字段，旧记录默认关闭。groups 使用原有 JSON payload。
- 对用户、组、标签与关联表的事务提交会刷新 /etc/openvpn/mgmt/device_bind_exemptions.json。
- OpenVPN 只读取精简、原子发布的 JSON 名单，不读取 SQLite 或管理端 config.json。
- /etc/openvpn/mgmt/device_bind_mode 第一行格式不变；第二行 exemptions_enabled=0/1 是全局总闸。启用时先发布名单再开启总闸。
- 豁免成功时跳过绑定验证、新建及更新，不删除历史绑定。撤销豁免后沿用旧绑定关系；变更设备需按原设备重置流程。
- 证书有效性、用户状态、防火墙、连接审计不受影响。会话审计扩展 device_binding_action、device_binding_exempt_source 及设备 IV_* 字段。
- 缺失/损坏名单时不能额外授权：退回原有绑定判断。文件写入失败应报错，不把界面保存视为成功。

## 部署验证

更新并重启 NiceVPN 管理端以同步 OpenVPN 托管脚本及运行时策略；新增加的 device_bind_exemption.py 必须和 device-bind.sh 部署在同一 scripts 目录。
测试强绑定下授权用户缺少 IV_HWADDR 可连接，而未授权用户仍拒绝；测试仅记录模式、关闭总闸、取消标签和切换用户组后的恢复；确认连接审计日志保留设备相关字段。

注意：公网 IP 或网络切换本身不应参与稳定的设备指纹匹配。
