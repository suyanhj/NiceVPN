# 修复规范

## 涉及文件

| 文件 | 变更类型 |
|------|----------|
| `app/services/user/ovpn_gen.py` | 客户端配置参数增强 |
| `app/ui/theme.py` | 暗色主题修复 + mgmt-* CSS 恢复 |
| `app/ui/pages/users.py` | 离线状态标签 + 踢下线按钮 |
| `app/services/user/crud.py` | 日志文案修正 |
| `app/ui/pages/firewall.py` | 多源IP/批量删除/全局优先级 |
| `app/services/firewall/iptables_mgr.py` | ipset src/dst 双向支持 |
| `app/services/firewall/rule_service.py` | 全局优先级校验 |
| `app/ui/pages/certs.py` | 批量续签/吊销按钮 |
