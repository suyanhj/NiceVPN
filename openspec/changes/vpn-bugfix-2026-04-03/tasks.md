# Tasks: vpn-bugfix-2026-04-03

## 证书/配置

- [x] 修复 `CertService._get_index_path` 中 `Path(Box)` 类型错误：`pki_dir` 未配置时 `default_box=True` 返回 `Box({})` 而非字符串，改为先 `str(...or "").strip()` 转换

## 用户管理

- [x] 用户卡片单行排版：用户名/时间/SN/状态/GID/流量/连接等水平排列，新增 `user-row-line` CSS 类
- [x] 搜索修复：初始渲染包裹在 `list_container` 内，搜索仅过滤原列表不单独显示；搜索按钮高度对齐
- [x] 创建用户时虚拟 IP 分配改为扫描已有 CCD 文件取下一个可用地址，确保在组 CIDR 范围内

## 组管理

- [x] 删除硬编码"默认用户组"保护，改为"根组"逻辑：按创建时间第一个组为根组
- [x] 后续所有组的子网必须是根组子网的子网（`is_subnet_of` 校验）
- [x] 根组在有其他组存在时不允许删除（需先删除所有子组）
- [x] 新建组弹窗添加根组/子组提示信息

## 防火墙

- [x] 新建规则大改：移除 OpenVPN 实例字段（自动检测），单机只有一个实例
- [x] 归属类型切换：组类型 → 只用源 CIDR；用户类型 → 下拉多选用户或填写源 CIDR
- [x] 目标 IP 支持逗号分隔多个，置空=允许所有
- [x] 端口置空=允许所有
- [x] 搜索改为轻量行内过滤（`firewall-search-row` + `firewall-inline-search` 等，历史曾用名 `fw-filter-bar`），不再是独立大面板，筛选结果直接替换列表
- [x] 拖拽排序加载 SortableJS CDN，单一归属下规则卡片即为拖拽列表（不重复渲染）
- [x] iptables 管理器支持 source_ips 列表（多条 ipset 条目）和多目标 IP（逗号 → 多行）
- [x] FirewallRule 模型：instance 改为 optional（默认 server），新增 source_ips 字段

## 服务管理

- [x] 日志查看：从实例配置文件解析 `log-append` 字段获取日志路径，展示最新 200 行日志
- [x] 支持刷新日志内容
