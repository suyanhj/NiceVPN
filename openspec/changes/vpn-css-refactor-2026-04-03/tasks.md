# 任务清单

- [x] 分析 theme.py 全部 CSS，识别可抽取的通用模式
- [x] theme.py 添加 mgmt-* 通用组件 CSS 段（~280 行）
- [x] 更新 groups.py 类名（21 项替换）
- [x] 更新 users.py 类名（20 项替换）
- [x] 更新 certs.py 类名（25 项替换）
- [x] 更新 services.py 类名（19 项替换）
- [x] 更新 settings.py 类名（6 项替换）
- [x] 更新 firewall.py 类名（5 项替换，补充 page-shell）
- [x] 清理 theme.py 冗余 CSS（删除已被 mgmt-* 替代的旧定义）
- [x] 简化合并选择器（移除 group-toolbar-btn / user-toolbar-btn / service-toolbar-btn 等旧引用）
- [x] 删除无引用的 drag_list.py 组件
- [x] lint 检查通过，无新增错误
