# 任务清单：可视化 OpenVPN 管理系统

**输入**: `specs/001-openvpn-mgmt-system/` 下的设计文档
**前置条件**: plan.md（必须）、spec.md（必须）、research.md、data-model.md、contracts/

**格式说明**:
- `[P]`：可并行执行（不同文件，无未完成依赖）
- `[USn]`：所属用户故事编号
- 每个任务包含具体文件路径

---

## Phase 1：项目初始化（Setup）

**目标**: 创建项目结构、安装依赖、配置基础工具

- [x] T001 创建完整目录结构（`app/core/`、`app/models/`、`app/services/`、`app/ui/pages/`、`app/ui/components/`、`app/api/`、`app/utils/`、**`app/scripts/`**、`data/`、**`data/ccd/`**、`backups/`、`tests/unit/`、`tests/integration/`）
- [x] T002 创建 `requirements.txt`（nicegui>=1.4.0,<2.0、fastapi>=0.110.0、uvicorn[standard]、pydantic>=2.0,<3.0、python-box>=7.0、requests>=2.31.0、schedule>=1.2.0、packaging>=23.0）
- [x] T003 [P] 创建 `app/core/constants.py`（默认路径常量：EasyRSA 标准搜索路径列表、数据目录路径、审计日志目录、备份目录、OpenVPN Management Interface 默认端口起始值 7505）
- [x] T004 [P] 创建 `.gitignore`（排除 `data/`、`backups/`、`*.ovpn`、`*.key`、`*.pem`、`.env`）
- [x] T005 [P] 创建 `deploy/systemd/openvpn-mgmt.service`（systemd 单元文件）
- [x] T006 [P] 创建 `deploy/docker/Dockerfile`（基于 Python 3.10，`--privileged` 说明注释）

**检查点**: 目录结构完整，`pip install -r requirements.txt` 无报错。

---

## Phase 2：基础设施（Foundational — 阻塞所有用户故事）

**目标**: 所有用户故事共用的数据模型、工具、日志基础设施

⚠️ **关键**: 此阶段完成前，任何用户故事实现均不可开始。

- [x] T007 [P] 实现 `app/utils/cidr.py`（`validate_cidr(s) -> bool`、`is_subnet_of(child, parent) -> bool`、`subnets_overlap(a, b) -> bool`，使用 Python `ipaddress` 标准库）
- [x] T008 [P] 实现 `app/utils/file_lock.py`（基于 `fcntl.flock` 的 JSON 文件原子写入上下文管理器，`write_json_atomic(path, data)`）
- [x] T009 [P] 实现 `app/utils/audit_log.py`（`AuditLogger` 单例，JSONL 追加写入，SHA-256 哈希链，按天分割文件到 `data/audit/`，`log(action, target_type, target_id, detail, result)`）
- [x] T010 [P] 实现 `app/models/config.py`（`SystemConfig` Pydantic 模型，字段：`initialized`、`global_subnet`、`openvpn_bin`、`easyrsa_dir`、`pki_dir`、`dingtalk_webhook`、`download_base_url`、`created_at`、`updated_at`；含 CIDR 校验器）
- [x] T011 [P] 实现 `app/models/group.py`（`Group` Pydantic 模型，字段：`id`、`name`、`subnet`、`status`、`user_count`、`firewall_rule_ids`、`created_at`、`updated_at`）
- [x] T012 [P] 实现 `app/models/user.py`（`User` Pydantic 模型，字段：`username`、`group_id`、`password_enabled`、`password_hash`、`status`、`ovpn_file_path`、`device_binding_id`、`cert_serial`、`firewall_rule_ids`、`created_at`、`updated_at`）
- [x] T013 [P] 实现 `app/models/device.py`（`DeviceBinding` Pydantic 模型，字段：`id`、`username`、`fingerprint`、`fingerprint_source`、`openvpn_unique_id`、`bound_at`、`last_seen_at`）
- [x] T014 [P] 实现 `app/models/firewall.py`（`FirewallRule` Pydantic 模型，字段：`id`、`owner_type`、`owner_id`、`instance`、`action`、`source_subnet`、`dest_ip`、`dest_port`、`protocol`、`priority`、`enabled`、`description`；含端口范围校验器）
- [x] T015 [P] 实现 `app/models/cert.py`（`Certificate` Pydantic 模型，字段：`serial`、`common_name`、`issued_at`、`expires_at`、`status`、`revoked_at`、`crl_version`）
- [x] T016 [P] 实现 `app/models/download_link.py`（`DownloadLink` Pydantic 模型，字段：`token`、`username`、`file_path`、`expires_at`、`used`、`created_at`、`used_at`）
- [x] T017 [P] 实现 `app/models/audit.py`（`AuditEntry` Pydantic 模型，字段：`id`、`timestamp`、`operator`、`action`、`target_type`、`target_id`、`detail`、`result`、`error_message`、`prev_hash`、`entry_hash`）
- [x] T018 [P] 实现 `app/core/config.py`（`load_config() -> SystemConfig`、`save_config(config: SystemConfig)`，通过 `file_lock.py` 写入 `data/config.json`；使用 python-box 包装运行时访问）
- [x] T019 [P] 实现 `app/ui/components/confirm_dialog.py`（NiceGUI 二次确认弹窗组件，`show(message, on_confirm)`，用于敏感操作）
- [x] T020 [P] 实现 `app/ui/components/alert_card.py`（NiceGUI 告警卡片组件，`show(level, title, message)`，支持 warning/error/info 级别）

**检查点**: 所有模型可实例化并通过 Pydantic 校验；`audit_log.py` 可写入并生成有效哈希链。

---

## Phase 3：用户故事 1 — 系统初始化与 VPN 服务启动（P1）🎯 MVP

**目标**: 完成 OpenVPN 安装检测/自动安装、PKI 初始化、首次运行引导、服务启动

**独立验收**: 在全新 Linux 服务器上执行 `python main.py`，通过浏览器完成全流程初始化，
界面显示服务状态为"运行中"，全程无命令行操作。

### Phase 3 实现任务

- [x] T021 [P] [US1] 实现 `app/services/openvpn/detector.py`（`detect_openvpn() -> dict`：读取 `/etc/os-release`、执行 `which openvpn`、用 `packaging.version` 比较版本 ≥2.7.0；`find_easyrsa(openvpn_bin) -> str|None`：按标准路径列表搜索 `easyrsa` 脚本）
- [x] T022 [P] [US1] 实现 `app/services/openvpn/installer.py`（`install_openvpn(distro, version_id) -> bool`：按发行版执行对应包管理器命令，从官方仓库安装 OpenVPN 2.7.0+；安装过程输出实时流式展示到 UI）
- [x] T023 [US1] 实现 `app/services/openvpn/instance.py`（`generate_server_conf(instance_name, config) -> str`：生成完整 `server.conf` 配置文件内容，必须包含以下 2.7.0 标准指令：`tls-crypt-v2`、**`tls-crypt-v2-max-age 3650`**、`status <file> 30`、`status-version 2`、`connect-freq 10 60`、`crl-verify`、`client-config-dir`、`management 127.0.0.1 <port>`、`client-connect`、`topology subnet`；`start/stop/restart_instance(name)`；`get_status(name) -> dict`：读取 status 文件获取在线用户和流量）
- [x] T024 [US1] 实现 `app/services/easyrsa/wrapper.py`（`EasyRSAWrapper`，封装：`init_pki()`、`build_ca(passphrase)`、`gen_req(cn)`、`sign_req(cn)`、`gen_crl()`、`revoke(cn)`、`renew(cn)`、`gen_tls_crypt_v2_server()`、`gen_tls_crypt_v2_client(cn)`，所有调用通过 `subprocess.run` + `script-security 2`；参数通过 stdin 传递密码短语，不使用命令行明文）
- [x] T025 [US1] 实现 `app/core/init_wizard.py`（`InitWizard` 状态机：状态 `DETECT_OPENVPN → INSTALL_OPENVPN → CONFIG_PKI → CONFIG_SUBNET → CREATE_DEFAULT_GROUP → DONE`；`run_step(step, data) -> StepResult`）
- [x] T026 [US1] 实现 `app/ui/pages/init_page.py`（NiceGUI 引导页：步骤一展示 OpenVPN 检测结果及"一键安装"按钮；步骤二自定义路径设置；步骤三 CIDR 网段输入（含实时格式校验提示）；步骤四 PKI CA 初始化（含密码输入）；步骤五启动服务并跳转主页）
- [x] T027 [US1] 创建 `app/scripts/device-bind.sh`（`client-connect` 设备指纹绑定脚本，由系统在初始化时部署到服务器 `/etc/openvpn/scripts/device-bind.sh`；脚本读取 `$common_name` 和 `$password`（指纹哈希），对比 `data/device_bindings/` 中的记录，首次连接写入绑定，已绑定时比对，不匹配返回退出码 1）
- [x] T028 [US1] 实现 `main.py`（入口：从 `core/config.py` 加载配置，未初始化则展示 `init_page`，已初始化则跳转 `dashboard`；通过 `nicegui.app.include_router` 挂载 FastAPI 路由；`ui.run(host, port)`）

**检查点**: 用户故事 1 全部 5 个验收场景可通过独立演示验证。

---

## Phase 4：用户故事 2 — 用户创建与配置文件下发（P1）

**目标**: 单用户创建流程：证书生成 → `.ovpn` 构造 → 一次性链接 → 设备绑定 → 双重拒绝验证

**独立验收**: 创建用户、生成 `.ovpn`、验证设备绑定后第二台设备连接被拒；
下载链接 1 小时后过期。

### Phase 4 实现任务

- [x] T029 [P] [US2] 实现 `app/services/user/ovpn_gen.py`（`generate_ovpn(username, group, config) -> str`：构造完整 `.ovpn` 配置内容，所有证书和 tls-crypt-v2 密钥以 `<ca>`、`<cert>`、`<key>`、`<tls-crypt-v2>` 内联块嵌入，不引用外部文件路径；文件保存到 `data/users/{username}.ovpn`）
- [x] T030 [P] [US2] 实现 `app/services/user/device_bind.py`（`DeviceBindingService`：`create_binding(username, fingerprint, source)`、`verify_binding(username, fingerprint) -> bool`、`reset_binding(username)`；JSON 持久化到 `data/device_bindings/`）
- [x] T031 [P] [US2] 实现 `app/services/download/link_mgr.py`（`create_link(username) -> str`：生成 `secrets.token_urlsafe(32)` 令牌，写入 `data/download_links/{token}.json`（含 `expires_at = now+3600`、`used=false`）；`consume_link(token) -> Path | None`：原子校验后置 `used=true` 再返回文件路径）
- [x] T032 [US2] 实现 `app/services/user/crud.py`（`UserService`：`create(username, group_id, password_enabled) -> User`：调用 EasyRSAWrapper 生成证书和 tls-crypt-v2 密钥、调用 ovpn_gen 生成配置文件、**写入 `ccd/{username}` 文件（含 `ifconfig-push <ip> <mask>` 指令）**、写入 `data/users/{username}.json`、写入审计日志；`delete(username)`：调用 EasyRSA revoke → gen_crl → 更新 CRL 文件路径 → 删除 `.ovpn` 文件 → 删除 CCD 文件 → 删除设备绑定；`get/list`）
- [x] T033 [US2] 实现 `app/api/download.py`（FastAPI 路由 `GET /download/{token}`：调用 `link_mgr.consume_link`；返回 `FileResponse` 或相应 404/410 错误；写入审计日志；文件名设为 `{username}.ovpn`）
- [x] T034 [US2] 实现 `app/services/notify/dingtalk.py`（`send_download_link(username, url) -> bool`：POST 到 `dingtalk_webhook`，超时 10 秒；任何异常均被捕获记录审计日志，不抛出；返回推送是否成功）
- [x] T035 [US2] 实现 `app/ui/pages/users.py`（NiceGUI 用户管理页：用户列表（含状态、证书状态列）；"新建用户"弹窗（用户名、组选择、密码可选）；创建成功后展示下载链接 + "钉钉推送"按钮（仅此按钮触发钉钉，推送失败时展示链接供手动复制）；多选复选框（供批量下载使用，US7）；删除用户需经 `confirm_dialog` 二次确认）

**检查点**: 用户故事 2 全部 4 个验收场景可通过独立演示验证。

---

## Phase 5：用户故事 3 — 用户组管理（P2）

**目标**: 组 CRUD、子网冲突检测、活跃用户保护

**独立验收**: 创建两个不同网段的组并验证冲突检测；验证含活跃用户的组无法修改网段或删除。

### Phase 5 实现任务

- [x] T036 [P] [US3] 实现 `app/services/group/subnet.py`（`check_subnet_conflict(new_subnet, global_subnet, existing_groups) -> list[str]`：返回所有冲突组名列表，空列表表示无冲突；使用 `cidr.py` 中的 `subnets_overlap`）
- [x] T037 [US3] 实现 `app/services/group/crud.py`（`GroupService`：`create(name, subnet) -> Group`：调用 subnet 冲突检测，通过后写入 `data/groups/{id}.json`；`bulk_create(groups: list[dict]) -> list[Group]`：逐一检测冲突后批量创建；`update_subnet(id, new_subnet)`：检查 `user_count == 0`；`delete(id)`：检查 `user_count == 0`；`bulk_delete/bulk_enable/bulk_disable(ids: list[str])`；`list_all()`；所有变更写审计日志）
- [x] T038 [US3] 实现 `app/ui/pages/groups.py`（NiceGUI 组管理页：组列表（名称、子网、状态、用户数）；"新建组"弹窗（名称、子网输入，含实时冲突预检提示）；编辑弹窗（有活跃用户时网段字段置灰并提示原因）；删除需二次确认；批量启用/禁用）

**检查点**: 用户故事 3 全部 4 个验收场景可通过独立演示验证。

---

## Phase 6：用户故事 4 — 防火墙规则可视化管理（P2）

**目标**: 防火墙规则 CRUD、拖拽排序、原子重建、JSON 备份恢复

**独立验收**: 创建两条规则、拖拽调换顺序、5 秒内验证实际 iptables 规则已按新顺序生效；
导出 JSON 后还原验证规则集完整。

### Phase 6 实现任务

- [x] T039 [P] [US4] 实现 `app/services/firewall/ipset_mgr.py`（`IpsetManager`：`create_set(name, subnet)`、`update_set(name, subnet)`、`delete_set(name)`；通过 subprocess 调用 `ipset` 命令）
- [x] T040 [P] [US4] 实现 `app/services/firewall/iptables_mgr.py`（`IptablesManager`：`rebuild_rules(rules: list[FirewallRule])`：按 priority 排序后生成完整 iptables-save 格式文本，写入临时文件，执行 `iptables-restore`，删除临时文件；`export_rules() -> str`；`import_rules(rules_json: str)`）
- [x] T041 [US4] 实现 `app/services/firewall/rule_service.py`（`FirewallRuleService`：`create(rule_data)`：三重校验（端口/CIDR/优先级唯一性）；`reorder(owner_id, new_id_order: list[str])`：重新计算 priority 步长后持久化并调用 `IptablesManager.rebuild_rules`；`backup() -> str`；`restore(json_str)`：校验格式后完整替换；所有变更写审计日志）
- [x] T042 [US4] 实现 `app/ui/components/drag_list.py`（NiceGUI 可拖拽规则列表组件，基于 `ui.sortable`，拖拽结束后回调 `on_reorder(new_order: list[str])`）
- [x] T043 [US4] 实现 `app/ui/pages/firewall.py`（NiceGUI 防火墙规则页：使用 `drag_list` 展示规则列表；新建/编辑规则弹窗（含保存前实时格式校验）；导出/导入 JSON 按钮；删除需二次确认）

**检查点**: 用户故事 4 全部 3 个验收场景可通过独立演示验证。

---

## Phase 7：用户故事 7 — 用户批量导入与批量下载（P2）

**目标**: CSV/TXT 全量预检式批量导入、多用户 ZIP 批量下载

**独立验收**: 上传含 5 用户的 CSV 全部导入成功；包含不存在组的 CSV 整批拒绝；
勾选 5 个用户下载 ZIP 包含 5 个 `.ovpn` 文件。

### Phase 7 实现任务

- [x] T044 [P] [US7] 实现 `app/services/user/bulk_import.py`（`BulkImportService`：`parse_file(file_bytes, filename) -> list[dict]`：支持 CSV（`,` 分隔，表头 `username,group`）和 TXT（空格分隔），强制 UTF-8 编码，返回行列表或抛出格式错误；`validate_all(rows, existing_groups, existing_users) -> ValidationResult`：全量检查用户名唯一性 + 组存在性，返回含所有失败原因的结果对象（全通过或全拒绝，无中间态）；`import_batch(rows) -> list[ImportResult]`：仅在 validate_all 通过后调用，逐一调用 `UserService.create`，收集每行结果）
- [x] T045 [P] [US7] 实现 `app/services/user/bulk_download.py`（`BulkDownloadService`：`create_zip(usernames: list[str]) -> bytes`：使用 `zipfile.ZipFile` 打包所选用户的 `.ovpn` 文件，文件名为 `{username}.ovpn`；跳过文件缺失或损坏的用户并记录警告列表；返回 ZIP 字节流和警告列表）
- [x] T046 [US7] 在 `app/ui/pages/users.py` 中集成批量功能（在用户列表页新增：文件上传控件（接受 `.csv`/`.txt`）+ "开始导入"按钮；导入预检结果弹窗（全量失败原因展示）；导入成功后展示每用户创建结果明细表格；在多选状态下展示"批量下载 VPN 文件"按钮，点击后调用 `bulk_download.create_zip` 并通过 `ui.download` 触发浏览器下载，完成后展示跳过文件警告）

**检查点**: 用户故事 7 全部 5 个验收场景可通过独立演示验证。

---

## Phase 8：用户故事 5 — 证书生命周期管理（P3）

**目标**: 证书状态查看、到期告警、吊销 CRL 自动更新、CA 密码保护

**独立验收**: 手动触发证书吊销，60 秒内该用户无法建立新连接；
首页显示 7 天内到期证书告警。

### Phase 8 实现任务

- [x] T047 [P] [US5] 实现 `app/services/cert/cert_service.py`（`CertService`：`list_all() -> list[Certificate]`：从 EasyRSA PKI 目录解析 `index.txt` 获取所有证书状态；`get_expiring(days=7) -> list[Certificate]`：过滤 `expires_at - now() ≤ days`；`revoke(username)`：调用 `EasyRSAWrapper.revoke → gen_crl`，更新 server.conf 中 `crl-verify` 指向的文件，通知 OpenVPN 实例重新加载 CRL（`SIGHUP` 或 Management Interface `signal SIGHUP`）；`renew(username)`：调用 `EasyRSAWrapper.renew` 并重新生成 `.ovpn` 文件）
- [x] T048 [P] [US5] 实现证书到期定时检查任务（在 `main.py` 中通过 `schedule` 每小时执行 `CertService.get_expiring(7)`，将结果写入 `data/alerts.json`，供仪表盘读取告警卡片）
- [x] T049 [US5] 实现 `app/ui/pages/certs.py`（NiceGUI 证书管理页：证书列表（CN、签发时间、到期时间、状态）；状态颜色标记（有效/即将到期/已吊销）；续签和吊销按钮（吊销需二次确认）；CA 操作区域：密码验证弹窗 + "此操作将使所有客户端失效"强制确认弹窗，取消则不执行）

**检查点**: 用户故事 5 全部 3 个验收场景可通过独立演示验证。

---

## Phase 9：用户故事 6 — 服务监控与运维（P3）

**目标**: 实时仪表盘、服务异常自动重启告警、在线配置编辑

**独立验收**: 停止 VPN 服务进程，60 秒内仪表盘显示告警；在线编辑配置文件，
验证编辑前自动备份已创建。

### Phase 9 实现任务

- [x] T050 [P] [US6] 实现 `app/services/monitor/service_monitor.py`（`ServiceMonitor`：`check_all_instances() -> list[InstanceStatus]`：读取 status 文件（`status-version 2` 格式）获取在线用户数和流量；`systemctl is-active` 检测服务运行状态；Management Interface 仅用于即时操作如踢出用户；`auto_restart_if_down(instance_name)`：检测到异常时执行重启并写入审计日志；通过 `schedule` 每 30 秒执行一次）
- [x] T051 [P] [US6] 实现 `app/services/config_editor/config_backup.py`（`backup_before_edit(conf_path) -> str`：将原文件复制到 `backups/{timestamp}_{filename}`，返回备份路径；`save_with_backup(conf_path, new_content)`：先备份再覆写，写入审计日志）
- [x] T052 [US6] 实现 `app/ui/pages/dashboard.py`（NiceGUI 仪表盘：实时指标卡片（在线设备数、上下行流量、服务状态指示灯、已生效规则数）；告警卡片列表（来自 `data/alerts.json`）；页面每 30 秒自动刷新（通过 `ui.timer`））
- [x] T053 [US6] 实现 `app/ui/pages/services.py`（NiceGUI 服务管理页：实例列表（名称、状态、在线用户数）；启停/重启按钮（需二次确认）；在线配置编辑按钮（打开编辑弹窗，自动备份后允许编辑，保存后提示需重启生效））

**检查点**: 用户故事 6 全部 3 个验收场景可通过独立演示验证。

---

## Phase 10：打磨与横切关注点

**目标**: 覆盖所有用户故事的横切功能、系统设置、部署验证

- [x] T054 [P] 实现 `app/ui/pages/settings.py`（NiceGUI 系统设置页：OpenVPN 自定义路径设置（含验证按钮）、钉钉 Webhook 配置（保存后推送测试消息验证）、下载链接基础 URL 配置）
- [x] T055 [P] 更新 `main.py` 完善导航结构（左侧导航栏：仪表盘、用户管理、组管理、防火墙规则、证书管理、服务管理、系统设置；初始化状态检测逻辑）
- [x] T056 [P] 更新 `plan.md` 源代码目录结构，在 `app/services/` 下补充：`cert/cert_service.py`、`monitor/service_monitor.py`、`config_editor/config_backup.py`；在项目根目录补充 `app/scripts/device-bind.sh`（由初始化流程部署到服务器 `/etc/openvpn/scripts/`）
- [x] T057 编写 `tests/unit/test_cidr.py`（`cidr.py` 的单元测试：合法/非法 CIDR、子网重叠、子网包含关系的边界用例）
- [x] T058 [P] 编写 `tests/unit/test_firewall_rules.py`（规则排序、优先级重算、端口范围校验的单元测试）
- [x] T059 [P] 编写 `tests/unit/test_bulk_import.py`（CSV/TXT 解析、预检逻辑、全量拒绝的单元测试：合法文件、含不存在组、含重复用户名、格式错误）
- [x] T060 [P] 编写 `tests/unit/test_download_link.py`（链接过期检测、一次性消费原子性的单元测试）
- [x] T061 在 CentOS/Ubuntu/Debian 至少一个发行版上运行 `quickstart.md` 验证端到端部署流程
- [x] T062 [P] 安全加固检查（确认：`data/` 目录权限 700、密钥文件权限 600、审计日志权限 644；`.ovpn` 文件生成后权限设为 600；确认所有 subprocess 调用无 shell=True 以防命令注入）
- [x] T063 [P] 为所有复杂逻辑补充中文注释（`device-bind.sh`、`iptables_mgr.py`、`easyrsa/wrapper.py`、`bulk_import.py` 中的全量预检逻辑）

---

## 依赖与执行顺序

### 阶段依赖

- **Phase 1（初始化）**: 无依赖，立即开始
- **Phase 2（基础设施）**: 依赖 Phase 1 完成 — **阻塞所有用户故事**
- **Phase 3（US1）**: 依赖 Phase 2 完成（P1，最先实现）
- **Phase 4（US2）**: 依赖 Phase 2 + Phase 3 完成（需要 UserService 依赖 EasyRSAWrapper 和 OvpnGen）
- **Phase 5（US3）**: 依赖 Phase 2 完成（独立于 US1/US2）
- **Phase 6（US4）**: 依赖 Phase 2 完成（独立于 US1/US2）
- **Phase 7（US7）**: 依赖 Phase 4（US2）完成（批量导入复用 `UserService.create`）
- **Phase 8（US5）**: 依赖 Phase 4（US2）完成（证书管理依赖 EasyRSAWrapper）
- **Phase 9（US6）**: 依赖 Phase 3（US1）完成（服务监控依赖实例管理）
- **Phase 10（打磨）**: 依赖所有用户故事完成

### 用户故事间依赖

- **US1（P1）**: Phase 2 完成后立即开始，无故事依赖
- **US2（P1）**: US1 完成后开始（服务必须可用才能验证设备绑定）
- **US3（P2）**: Phase 2 完成后可并行开始（不依赖 US1/US2）
- **US4（P2）**: Phase 2 完成后可并行开始（不依赖 US1/US2）
- **US7（P2）**: US2 完成后开始（复用单用户创建逻辑）
- **US5（P3）**: US2 完成后开始（共用 EasyRSA 封装）
- **US6（P3）**: US1 完成后开始（共用实例管理服务）

### 故事内并行���会

```bash
# Phase 2 全部 [P] 任务可同时启动
T007 T008 T009 T010 T011 T012 T013 T014 T015 T016 T017 T018 T019 T020

# Phase 3 中可并行
T021 T022（OpenVPN 检测与安装，各自独立）

# Phase 4 中可并行
T029 T030 T031（ovpn_gen、device_bind、link_mgr 各自独立）

# Phase 7 中可并行
T044 T045（bulk_import 和 bulk_download 各自独立）
```

---

## 实现策略

### MVP 优先（仅用户故事 1）

1. 完成 Phase 1：初始化
2. 完成 Phase 2：基础设施（**关键，阻塞一切**）
3. 完成 Phase 3：US1 系统初始化
4. **停止并验证**：全新服务器上完整走通初始化流程
5. 演示/部署

### 增量交付

1. Phase 1+2 → 基础设施就绪
2. Phase 3（US1）→ 系统可启动，独立验收 ✅
3. Phase 4（US2）→ 用户可创建并连接，独立验收 ✅
4. Phase 5+6（US3+US4）→ 组管理和防火墙可用，独立验收 ✅
5. Phase 7（US7）→ 批量导入/下载可用，独立验收 ✅
6. Phase 8+9（US5+US6）→ 证书和监控完整，独立验收 ✅
7. Phase 10 → 打磨、测试、部署验证

### 并行团队策略

Phase 2 完成后：
- 开发者 A：US1 → US6（纵向服务初始化和监控线）
- 开发者 B：US2 → US7（横向用户管理线）
- 开发者 C：US3 + US4（组管理和防火墙并行）

---

## 备注

- `[P]` 标记的任务操作不同文件，无未完成依赖，可并行执行
- `[USn]` 标签用于将任务与具体用户故事绑定，便于追踪独立交付进度
- 每个用户故事应独立完成并可单独演示，然后再进入下一个
- 每完成一个任务后提交（或逻辑组提交一次）
- 在每个阶段检查点处验证故事独立可用性后再继续
- 避免：模糊任务描述、同文件冲突任务、破坏故事独立性的跨故事依赖
