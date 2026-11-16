# 测试覆盖率审计

> 基线：2026-11-16，Python 3.14；统计范围为 `tests/unit` 对 `app` 的行与分支覆盖。

## 结果

- **1083 passed、1 skipped**，58.72 秒。
- 行覆盖率 **58.3%**（14,581 / 23,723）。
- 分支覆盖率 **47.7%**（3,402 / 7,130）。
- Browser smoke、OpenSpec 校验通过；本次未改 Docker 相关文件，因此 Docker 构建按范围跳过。
- 覆盖率报告作为 CI artifact 保存 14 天；目前不设全局覆盖率门槛。

## 模块覆盖

| 模块 | 行覆盖 | 分支覆盖 |
|---|---:|---:|
| SQLite / Storage | 86.3% | 76.0% |
| SSO / Auth | 78.6% | 63.4% |
| API | 69.7% | 62.5% |
| Peer | 62.8% | 52.7% |
| OpenVPN | 53.8% | 44.4% |
| User | 54.0% | 43.0% |
| Firewall | 47.2% | 39.5% |
| UI Pages | 49.5% | 36.5% |
| CLI | 29.9% | 16.9% |

覆盖率是定位工具，不等于正确率；UI 页面构建、系统环境和真实内核路径不应单纯追求行覆盖率。

## 优先补测

### P0：核心业务与网络安全

- `firewall/rule_service.py` **24.7%**：`create`、`create_many`、`delete`、`backup`、`restore` 等路径缺少直接验证；现有测试已覆盖启停失败回滚和 owner 排序。
- `firewall/iptables_mgr.py` **33.8%**：重点补 FORWARD / INPUT / NAT 钩子、原子重建失败回滚、导入导出；用有状态 fake kernel 验证命令顺序与最终规则，不在 CI 操作宿主机防火墙。
- `openvpn/upgrade.py` **31.4%**：现有测试覆盖配置改写、preflight 和 preflight 失败；缺候选安装成功、服务重启、升级后检查及切换失败恢复。
- `cert/cert_service.py` **34.0%**：续签已有测试；证书列表解析、到期过滤、吊销 / CRL 失败、告警原子写入仍需补测。

### P1：运行稳定性与用户操作

- `peer_instance/remote_peer_install.py` **12.1%**、`peer_ssh_connect.py` **33.3%**：补远端发行版识别、安装脚本执行、流式日志、超时 / 退出 / SSH 失败和连接关闭。
- `user/bulk_download.py` **0%**：补 ZIP 内容、旧路径兼容、缺失 / 空配置文件和部分成功告警。
- `utils/posix_data_perms.py` **10.9%**：补 nobody UID/GID 回退、目录归属、外置 PKI、日志文件创建及 chown 失败；全部 mock 系统调用。
- `cli/entry.py` **26.1%**：现有 `test_cli_management.py` 主要验证 parser；增加命令 handler 的成功、无效参数、权限拒绝和 `--dry-run` 不落库测试。
- `user/crud.py` **45.9%**、`group/crud.py` **46.6%**：补用户删除 / 停用 / CCD / 踢线，以及组网段变更、批量创建和状态变更；组删除关联保护已有真实 SQLite 测试。

### P2：系统生命周期与 UI

- `core/scheduler.py` **9.5%**、`utils/logging_setup.py` **10.2%**、`utils/listen_lan.py` **11.4%**：用 fake scheduler、网络接口和日志 handler 验证启动、停止、异常及回退。
- UI Pages 行覆盖 **49.5%**：页面 build 已有测试，但不少事件回调未执行。优先补用户 / 组 / 标签 / 防火墙的关键操作结果和失败提示，不为静态布局追覆盖率。
- `models/audit.py` 0% 属于低逻辑数据模型，不作为优先补测项。

## 测试质量判断

**有效的行为测试，应保留：**

- SSO 集成使用真实临时 SQLite 与 FastAPI TestClient，只 mock 外部 OAuth 和 OpenVPN Management 边界。
- ipset reconcile 使用有状态 fake kernel，验证 swap、GC、失败回滚和集合隔离。
- 对端防火墙使用有状态 fake SSH 命令执行器，验证规则顺序、ipset 清理和失败回滚。
- 组删除保护直接经过 SQLite Repository，验证关联存在时不能删除。
- SSO callback 并发审批、一次性下载链接并发消费已有 ThreadPoolExecutor 测试。

**仍需改善：**

- CLI parser 测试不代表命令 handler 已验证。
- OpenVPN 升级的静态源码约束不能替代成功切换和失败恢复测试。
- UI 静态源码断言只能保护少量结构约束，关键交互应验证回调结果或使用浏览器测试。
- ipset 并发 reconcile、SQLite 多进程写入 / 迁移、用户与标签并发变更尚缺专门竞争测试。

## 建议顺序

1. FirewallRuleService + IptablesManager。
2. 证书生命周期 + OpenVPN 升级回滚。
3. 对端 SSH 安装 + 文件权限 + 批量下载。
4. CLI handler + 用户 / 组服务。
5. UI 关键操作与系统后台任务。

先补高风险路径，再根据新基线设置分模块门槛；不建议直接给全项目设 80% 统一门槛。
