# 设计方案

## 服务端连接配置
- `SystemConfig` 已有 `server_ip`/`port`/`proto` 字段
- settings.py 新增 `_render_server_connection_panel` 面板
- init_page.py 的 `_render_subnet_form` 扩展为同时收集服务端连接信息
- init_wizard.py 的 `_config_subnet` 方法保存这些字段

## 用户卡片排版
- 从 `user-row-line` 单行 inline 改为 `mgmt-record-main > mgmt-record-copy` 结构化布局
- 第一行：用户名 + 在线/离线标签 + 停用标签
- 第二行：创建时间、证书SN、组ID、流量、连接信息、虚拟IP

## 防火墙规则卡片
- 流向文本（source → dest:port）从独立行移入 `mgmt-record-meta`
- 使用 `mgmt-meta-flow` 等宽字体样式（历史 `fw-flow-text`）
- 编辑/删除按钮改为图标按钮风格与用户页一致

## 按钮白色闪烁修复
- `.q-focus-helper` / `::before` / `.q-ripple` 全局隐藏
