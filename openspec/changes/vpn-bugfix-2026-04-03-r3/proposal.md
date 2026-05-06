# vpn-bugfix-2026-04-03-r3 — 提案

## 背景

第三轮综合修复，涵盖防火墙页面渲染不一致、ipset 原子清理、SortableJS 本地化、
仪表盘假数据清理、启用/停用真正生效、用户批量导入和证书搜索。

## 问题列表

### 防火墙
1. **页面渲染不一致**：show_all 模式用 `_render_rule_card`（有编辑无拖拽），filtered 模式曾用 `DragList`（有拖拽无编辑），`firewall-rule-line`（历史 `fw-rule-line`）文本在卡片 div 外面导致多余字符串
2. **ipset 清理**：`rebuild_rules` 先 cleanup ipsets 再 iptables-restore，但旧 iptables 规则仍引用 ipset 导致 destroy 失败
3. **SortableJS 网络加载慢**：bootcdn 偶尔不稳定，需下载到本地

### 仪表盘
4. **假数据**：SSL 证书余天写死 82 Days、较昨日变动 +0、今日阻断请求假乘法、系统平均负载假公式、趋势图假数据、快捷操作占位按钮

### 用户管理
5. **启用/停用不生效**：`toggle_status` 只改 JSON 状态，不影响 CCD 文件，用户仍可连接
6. **批量导入未实现**：前端只弹 "暂未实现" 提示

### 证书管理
7. **无搜索功能**：证书多时无法快速定位

## 解决方案

| # | 问题 | 方案 |
|---|------|------|
| 1 | 页面不一致 | 移除 DragList 依赖，统一 `_render_rule_card` 渲染，filtered 模式加 sortable 容器 + 拖拽手柄 |
| 2 | ipset | 先 `iptables -F VPN_FORWARD` 解除引用，再 destroy ipset，再创建新 ipset + iptables-restore |
| 3 | SortableJS | 下载到 `app/ui/static/`，`main.py` 注册 `/static` 路由，改为本地引用 |
| 4 | 仪表盘 | 删除趋势图、快捷操作、假公式摘要行；替换为真实证书到期天数、用户数、组数 |
| 5 | 启用/停用 | CCD 文件写入/移除 `disable` 指令 + 管理接口 `kill` 踢人下线 |
| 6 | 批量导入 | 弹窗支持粘贴或上传 CSV/TXT，空格/逗号分隔，预检查所有组存在后再逐一创建 |
| 7 | 证书搜索 | 添加搜索输入框 + 按用户名过滤 |
