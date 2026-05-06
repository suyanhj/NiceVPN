# 任务列表

- [x] 系统设置：新增「服务端连接配置」面板（server_ip/port/proto），保存后同步到 .ovpn 生成
- [x] 初始化向导：在「配置网段」步骤同时收集服务端 IP/端口/协议
- [x] 初始化向导：init_wizard._config_subnet 保存 server_ip/port/proto 到系统配置
- [x] 用户管理：卡片排版从单行混排改为结构化两行布局（用户名+标签 / 元数据）
- [x] 用户管理：连接信息仅在线时显示
- [x] 防火墙：流向文本（source → dest:port）移入元数据行，等宽字体
- [x] 防火墙：编辑/删除按钮改为图标风格
- [x] 防火墙：新增 rule_service.set_enabled() 方法
- [x] 防火墙：工具栏新增「批量启用」「批量停用」按钮
- [x] CSS：新增 `mgmt-meta-flow` 等宽字体样式（现名；历史为 `fw-flow-text`）
- [x] CSS：全局修复 flat/round 按钮的 q-focus-helper / ripple 白色闪烁
