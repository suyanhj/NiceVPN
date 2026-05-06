# 设计方案

## 客户端配置
- 在 `ovpn_gen.py` 的 `generate_ovpn` 函数中添加 c2.conf 中的关键参数
- 参数选择标准：安全相关 + 网络优化 + 设备绑定支持
- **不得**再写入 `auth-user-pass`：与仅证书认证的服务端配置一致，避免客户端误弹密码

## 用户侧 .ovpn 文件
- `bulk_download.py`：zip 文件名带时间戳；解析用户 JSON / 多路径查找 `.ovpn` 与生成目录一致
- `users.py`：单用户下载按钮、弹窗编辑 `.ovpn` 并 `write_text` 回盘

## 暗色主题
- 在 `theme.py` 追加 Quasar 组件暗色覆盖：
  - `.q-dialog .q-card` / `.q-menu` / `.q-select__dialog` 背景色
  - `.q-field__control` / `.q-field__native` 输入框暗色
  - `.q-item` / `.q-chip` / `.q-toggle` / `.q-btn-toggle` 暗色
- 同时补充 `mgmt-*` 通用管理页面组件 CSS（从上轮 CSS 重构中恢复）

## 防火墙 ipset 优化
- `_rule_ipset_name(rule_id, direction)` 支持 src/dst 方向
- `rebuild_rules` 为源 IP 列表和目标多 IP 分别创建 ipset
- `_expand_rule_lines` 根据目标 IP 数量决定用 ipset 还是直接 -d
- 优先级从 per-owner 改为全局唯一

## 批量操作
- 防火墙：`selected_rule_ids` set + `_batch_delete` 方法
- 证书：`selected_certs` set + `_batch_renew` / `_batch_revoke` 方法
