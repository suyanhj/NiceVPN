# 修复规范

## 涉及文件

| 文件 | 变更类型 |
|------|----------|
| `app/ui/pages/settings.py` | 新增服务端连接配置面板 |
| `app/ui/pages/init_page.py` | 子网配置步骤扩展服务端连接字段 |
| `app/core/init_wizard.py` | 保存服务端 IP/端口/协议到配置 |
| `app/ui/pages/users.py` | 用户卡片排版重构 |
| `app/ui/pages/firewall.py` | 规则卡片排版 + 批量启用/停用 |
| `app/services/firewall/rule_service.py` | 新增 set_enabled 方法 |
| `app/ui/theme.py` | fw-flow-text 样式 + 按钮白色闪烁修复 |
