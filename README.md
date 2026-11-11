# NiceVPN

基于 **NiceGUI + FastAPI + OpenVPN Community** 的可视化 VPN 管理系统，提供用户、用户组、证书、设备绑定、对端站点、Mesh、防火墙、服务管理、CLI 与公网 API。

- **Python**：3.10+；GitHub Actions 当前以 **Python 3.14** 为测试基线。
- **管理界面**：默认 `0.0.0.0:8880`。
- **公网 API**：默认 `0.0.0.0:8881`。
- **进程模型**：一个 NiceVPN Python 进程、一个 systemd 服务、两个 HTTP 监听端口。
- **结构化存储**：SQLite，默认 `data/nicevpn.db`。


> **统一访问地址与企业认证（开发分支）**：系统设置 → 访问地址支持全局 `domains`；系统设置 → 企业认证通过“开启认证 / 所有人生效”控制范围，并在“统一认证 → 认证方式”选择钉钉或企业微信后配置相应凭证。钉钉使用一套 Client ID / Secret 和可增删的企业名单，每家 CorpId/企业名称至少填一项，两项同填以 CorpId 为准；仅填名称时须有钉钉组织基础信息读取权限，无法获取可信名称则拒绝。不读取员工个人资料。关闭“所有人生效”时仍按组/标签 SSO 策略执行。

> **开发中的功能（尚未验收）**：企业身份 SSO（钉钉/企业微信）在 `feature/enterprise-sso-webauth` 独立分支与 [Draft PR #78](https://github.com/suyanhj/NiceVPN/pull/78) 开发，默认关闭，未通过真实 OpenVPN/办公系统实机验收，**不可在生产环境直接启用**。配置、认证策略和 POC 验收参见 [企业 SSO 文档](docs/enterprise-sso.md)。

## 主要能力

- **OpenVPN / PKI**：EasyRSA、CRL、tls-crypt-v2、CCD、固定虚拟 IP、OVPN 生成。
- **用户与用户组**：生命周期管理、CIDR 规范化、批量操作、下载分发。
- **设备绑定**：`weak_log`、`weak_fingerprint`、`strict_hwaddr` 三种策略。
- **对端与 Mesh**：站点 LAN、`iroute`、按用户组下发 Mesh 路由、SSH 远端运维。
- **防火墙**：iptables + ipset，声明式 reconcile、标签规则与远端规则协作。
- **管理界面**：NiceGUI 管理壳 + AG Grid / Vue 预编译前端资源。
- **CLI**：用户、用户组、设备绑定、OVPN、防火墙等命令行操作。
- **公网 API**：HTTP Basic 下的用户创建、批量创建、切组、删除、重置设备绑定与一次性下载。
- **诊断、备份与日志**：系统诊断、备份恢复、OpenVPN/NiceVPN 日志轮转；企业 SSO 认证事件独立记录于 `data/logs/sso-auth.jsonl`，可在「服务管理 → 本机实例 → 认证日志」按需查看。

完整能力说明见 [docs/overview.md](./docs/overview.md)。

## 快速开始

推荐使用项目根目录的 `start.sh`：

```bash
pip install -r requirements.txt
./start.sh
```

浏览器访问：

```text
http://<主机>:8880/
```

未完成初始化时会进入 First Run Setup。

正式 Vue 运行产物已经提交在 `app/ui/vue/dist/`，普通生产启动**不需要 Node/npm，也不需要 Docker 来构建前端**。

### Python 解释器选择

`start.sh` 按以下优先级选择 Python：

1. `PYTHON_BIN`
2. 项目目录 `.venv/bin/python`
3. 当前 `VIRTUAL_ENV`
4. 当前 `CONDA_PREFIX`
5. PATH 中满足 Python 3.10+ 的解释器

例如：

```bash
PYTHON_BIN=/usr/local/bin/python3.14 ./start.sh
PYTHON_BIN=/opt/nicevpn-venv/bin/python sudo -E ./start.sh daemon
```

项目存在 `.venv` 时会保留 `.venv/bin/python` 路径，不解析到 uv 的基础 Python，以确保 systemd 继续使用该虚拟环境的依赖。

## 生产部署

### systemd

推荐：

```bash
sudo -E ./start.sh daemon
```

daemon 模式会：

- 使用仓库内预编译前端资源；
- 把当前项目真实目录和选定 Python 写入 `/etc/systemd/system/openvpn-mgmt.service`；
- enable + restart `openvpn-mgmt`；
- 安装 NiceVPN / OpenVPN 的 logrotate 策略；
- 只维护**一个** NiceVPN systemd 服务。

最终服务同时监听：

| 端口 | 用途 |
|---|---|
| `8880` | NiceGUI 管理界面 |
| `8881` | 独立公网 API 路由 |

监听地址可通过 `/etc/nicevpn/listen.env` 配置：

```ini
NICEVPN_UI_HOST=0.0.0.0
NICEVPN_UI_PORT=8880

NICEVPN_API_HOST=0.0.0.0
NICEVPN_API_PORT=8881
```

生产环境建议：

- 公网拒绝直接访问管理端 `8880`；
- API 使用 HTTPS 反向代理后再转发到 `8881`；
- 若由 Nginx 终结 TLS，可将 `NICEVPN_API_HOST=127.0.0.1`；
- 一次性下载只走 8881 API 端口的 `/download/{token}`，8880 管理端不再提供下载路由；管理页的「复制」「推送」共享此下载链接。
- `download_base_url` **可选**：配置后按指定的 IP/域名生成下载链接；留空时统一使用启动检测到的本机 IP + API 监听端口（默认 8881），包括复制、推送以及 API 返回的下载地址。无需域名即可部署和测试。
- 如果 API 仅绑定 `127.0.0.1`、无法自动找到可访问的监听 IP，则应填写反代可访问的下载地址；外地用户无法访问私网 IP 时可配置公网 IP 或 HTTPS 域名。

公网 API 详情见 [api.md](./api.md)。

### 日志轮转

`start.sh daemon` 会安装 `/etc/logrotate.d/nicevpn-openvpn`。

当前策略管理：

- `openvpn-device-bind.log`
- `openvpn-session-audit.jsonl`
- `openvpn-connection-audit.jsonl`
- `server.log`

历史日志轮转后**立即 gzip 压缩**；`server-status.log` 为状态文件，不参与轮转。

如果发行版的 `logrotate.service` 使用 `ProtectSystem=full/strict`，安装脚本会通过 systemd drop-in 仅允许其写入实际 OpenVPN 日志目录，不放开整个 `/etc`。

### Docker

Docker 镜像直接使用仓库中的预编译 Vue 产物，同一个容器进程同时监听 `8880` 和 `8881`。

```bash
DOCKER_BUILDKIT=1 docker build -t nicevpn:latest -f deploy/docker/Dockerfile .

docker run -d \
  --name nicevpn \
  --privileged \
  --network host \
  -v /opt/openvpn-mgmt/data:/app/data \
  -v /opt/openvpn-mgmt/backups:/app/backups \
  nicevpn:latest
```

使用 `--network host` 时无需额外 `-p`。

## CLI

CLI 不需要构建前端：

```bash
./start.sh cli --help
```

也可以使用：

```bash
python main.py cli --help
python cli.py --help
```

完整命令、`--dry-run`、JSON 输出、OVPN 导出和 bash Tab 补全见 **[cli.md](./cli.md)**。

## 数据与升级

结构化管理元数据使用：

```text
data/nicevpn.db
```

包含用户、用户组、对端、中心防火墙规则、标签等结构化数据。

旧版本升级时，启动阶段会自动将旧 JSON 数据迁入 SQLite，并把旧 JSON 归档到：

```text
backups/storage-migration-<UTC>/
```

以下仍保持文件形态：

- 系统配置；
- PKI / CRL；
- CCD；
- OVPN；
- 设备绑定；
- 远端链缓存；
- 下载令牌 / bundle；
- 审计与运行日志。

若需要降级到仍使用 JSON 的旧版本，必须先停止服务并从迁移归档恢复对应 JSON；旧版本不会读取 `nicevpn.db`。

## 文档

| 文档 | 用途 |
|---|---|
| [docs/overview.md](./docs/overview.md) | 产品能力与模块说明 |
| [cli.md](./cli.md) | CLI 使用说明与命令参考 |
| [api.md](./api.md) | 公网 VPN API |
| [CLAUDE.md](./CLAUDE.md) | AI / 开发者架构与开发约束 |
| `openspec/changes/` | 功能变更设计、规范与任务归档 |

## 开发与测试

安装依赖：

```bash
pip install -r requirements.txt
```

单元测试：

```bash
python -m pytest tests/unit -q
```

需要重新构建前端时：

```bash
./start.sh --build-ui
# 或
./build-ui.sh
```

GitHub Actions 当前覆盖：

- Python 3.14 单元测试；
- OpenSpec strict validation；
- shell / UTF-8 / whitespace 检查；
- 按改动范围执行 Docker build；
- 按改动范围执行 Chrome browser smoke。

代码注释和文档字符串使用中文，代码标识符使用英文。架构与开发边界以 [CLAUDE.md](./CLAUDE.md)、代码和当前 OpenSpec 为准。
> **SSO 升级清理**：schema v9 将 SQLite 旧认证审计导出到 `data/logs/sso-auth-legacy.jsonl` 后删除废弃表，统一钉钉配置成功保存/导入后清理旧企业凭证。建议升级前备份数据库和 `data/sso_provider_master.key`。

> **企业认证自动重连（SQLite v12）**：钉钉首次登录通过后，NiceVPN 按原证书指纹复用 SSO 会话；OpenVPN 仍自行验证证书、CRL 与 TLS。在线客户端由 Management `status 3` 按每实例随机 1–5 秒确认，SQLite 约每 55 分钟原地刷新同一条会话记录（为 1 小时超时预留 5 分钟余量）；**正常断开后保留 1 小时复用窗口，持续在线按周期续期**。支持 OpenVPN / NiceVPN 重启及 TLS REAUTH；撤销、白名单失效、缺少可信证书指纹等情况仍不能绕过认证。详见 `docs/enterprise-sso.md`。
