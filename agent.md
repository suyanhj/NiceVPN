# NiceVPN Agent Guide

> 本文件只记录必须长期遵守的项目约束；实现细节以当前代码、测试和正式文档为准。

## 1. 工作约定

- 修改前先检查相关实现、调用链、测试和文档，不凭旧对话或猜测判断现状。
- 优先小步修改，复用现有 Service、Repository、脚本和组件；避免重复实现。
- Web、CLI、API 共用业务 Service；业务校验和状态变更不得只写在 UI 回调中。
- 保持现有行为和兼容性；确需破坏性变更时，先说明影响并同步迁移文档。
- 不声称测试通过，除非实际运行并确认结果；说明未覆盖的真实环境验证。

## 2. 技术栈与入口

- Python 3.10+；依赖版本以 `requirements.txt` 为准。
- NiceGUI 提供管理界面，FastAPI 提供业务 API，OpenVPN 提供 VPN 服务。
- `main.py` 启动管理端和公网 API；`cli.py` 是 CLI 入口；`./start.sh cli` 调用 CLI。
- 生产仅使用一个 `openvpn-mgmt.service`，同时管理端口 `8880` 和 API 端口 `8881`。
- API 端口只挂载业务 API、一次性下载和 SSO OAuth 流程，不得暴露管理页面或管理路由。

## 3. 分层与数据

- 结构化业务数据存于 `data/nicevpn.db`；SQL 只放在 Repository / Storage 层。
- Schema 变更必须新增版本化 migration；不得修改已发布的历史迁移，失败必须回滚。
- 系统配置、PKI/CRL、CCD、OVPN、下载文件和运行日志继续按文件管理，不强行迁入 SQLite。
- 文件、数据库和运行状态的路径应复用现有配置与路径工具，不散落硬编码。
- 同一业务逻辑只保留一个权威实现；CLI、UI、API 不各自复制业务规则。

## 4. Linux 与 OpenVPN

- 安装、依赖、systemd、权限和文件部署优先复用 `app/scripts/`、`deploy/shell/`、`deploy/systemd/`。
- Python 负责业务编排；发行版包管理和系统操作优先交给可复用 Shell 函数，不重复造轮子。
- Debian/RHEL 通用包管理函数位于 `app/scripts/common/linux-packages.sh`。
- 优先识别并复用已安装的 OpenVPN、EasyRSA 和完整 PKI；不得覆盖已有 CA 或无必要重装 OpenVPN。
- DCO 是可选优化；环境不满足时应自动关闭，不得阻断常规安装。
- 证书、CA、CRL、TLS 和隧道协商由 OpenVPN 原生处理，NiceVPN 不重复实现。
- 独立运行的 OpenVPN hooks 使用部署时确定的 Python 绝对路径，不依赖交互式环境或默认 `python3`。

## 5. 防火墙、标签与对端

- iptables / ipset 按声明式期望状态收敛；集合更新遵循 staging、swap、失败回滚和安全 GC。
- 中心与各对端的 ipset 命名空间必须隔离；不得清理未知前缀或其他对端的集合。
- 标签成员变化通过统一 Service 流程同步被引用的中心规则、Mesh 路由和远端防火墙。
- 删除用户组或标签前检查用户、规则、路由及对端引用；Service 层必须强制执行，不能只依赖 UI 提示。
- 防火墙更新应保持幂等，避免无关规则被重建或短暂清空。

## 6. 身份与安全

- 企业 SSO 默认关闭；仅全局策略或明确启用 SSO 的组/标签触发认证。
- 企业身份必须由服务端 OAuth 响应核验；不得信任浏览器传入的企业 ID 或客户端自报平台。
- VPN 连接权限仍由证书、用户、组、标签和防火墙决定；企业账号不自动创建 VPN 用户。
- OpenVPN 原生证书校验不能被 NiceVPN 缓存替代；跨重连复用须验证可信 Management 证书指纹，不能只凭 CN。
- SSO 成功只影响该连接的设备绑定处理，不改变证书和防火墙权限。
- 密钥、Token、OAuth Code 不得写入日志；`data/sso_provider_master.key` 与 SQLite 一起备份并保持权限 `0600`。
- Management Socket 不得暴露公网。

## 7. UI、CLI 与文档

- 管理界面使用 NiceGUI；表格优先复用共享 AG Grid 组件，保持筛选、分页和操作交互一致。
- 生产前端产物位于 `app/ui/vue/dist/`；生产启动不依赖 Node.js / npm。
- CLI 按资源分层：`group`、`user`、`tag`、`firewall rule`、`admin password`；新增命令放在所属资源下。
- `README.md` 说明项目与部署；`api.md`、`cli.md` 说明稳定接口；`docs/` 保存长期有效的功能和运维说明。
- 新功能按需维护 OpenSpec 变更；历史任务不复制到 README 或本文件。
- 同一事实只维护一处权威说明，其他文档通过链接引用，避免重复和过期。

## 8. 验证

```bash
./start.sh
./start.sh cli --help
python -m pytest tests/unit -q
./start.sh --build-ui
```

- Shell 修改执行对应脚本测试；UI 修改执行前端构建及相关浏览器测试。
- 涉及 OpenVPN、iptables、ipset、systemd 或权限的改动，除单测外说明真实 Linux 验证范围。
- 提交前检查 diff、格式、测试结果和文档引用；不要顺手改动无关功能。
