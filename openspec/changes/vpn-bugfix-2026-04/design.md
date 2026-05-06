# Design: vpn-bugfix-2026-04

## 初始化

- PKI 阶段在 `build_ca`、`gen_dh` 之后增加 `gen-req server nopass` + `sign-req server server`，与 `instance.py` 中 `issued/server.crt` 一致。
- 全局 CIDR：前缀长度 **必须 ≥ /16**（拒绝 /8、/12 等过大网段，避免路由和地址分配异常）；默认组子网取全局网段内第一个 `/24`，/24 及更小网段直接使用。
- 校验链路：`_config_subnet` 后端拒绝 → `SystemConfig.validate_global_subnet` Pydantic 兜底 → `_quick_cidr_check` 前端实时反馈。
- 启动成功后：写入 `config.instances[instance_name]`（端口、协议、子网）；若默认组尚无放行规则则创建一条全放行占位规则。

## 子网冲突

- `check_subnet_conflict`：若新子网不在全局范围内，立即返回，不再与组列表做重叠判断，避免提示歧义。

## 防火墙

- **合并与优先级**：`data/firewall/*.json` 全部启用规则按 `priority` 排序后合并渲染到 **`VPN_FORWARD`**；多 owner 合并避免后者覆盖前者。  
- **规则页内核范围**：`/firewall` 上 CRUD、排序、启停、备份恢复等仅 **`refresh_vpn_forward_only()`**，**只**改写 **`VPN_FORWARD`**（及规则引用 ipset），**不**触达 `FORWARD`/`INPUT`/`nat`。  
- **项目级内核范围**：`rebuild_iptables()` / `IptablesManager.rebuild_rules` 才写入 **FORWARD 钩子、INPUT tun+、POSTROUTING MASQUERADE** 等（见 **`vpn-lan-firewall-devicebind-2026-04-09/design.md`**）。  
- **ipset**：每条含 `source_subnet` 的规则使用独立 `hash:net` 集合（名称取规则 ID 的短哈希，满足长度限制），iptables 使用 `-m set --match-set <name> src`。
- **端口**：`dest_port` 支持逗号分隔多端口，iptables 使用 `multiport` 模块（按协议拆分 tcp/udp）。

## 证书

- `CertService` 使用 `easyrsa_dir` 作为 EasyRSA 可执行脚本路径（与 `UserService` 一致），避免 Box 字段错误导致 subprocess 参数异常。
- 续签成功后从 `index.txt` 解析的最新序列号写回用户 JSON 的 `cert_serial`。

## UI

- 防火墙：Owner 为空时展示全部规则（带 owner 列）；新建/编辑使用右侧 `ui.drawer`。
- 用户：工具栏增加搜索框；创建支持数量与命名后缀；批量/单个重置设备绑定。
