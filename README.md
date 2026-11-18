# NiceVPN

基于 NiceGUI、FastAPI 和 OpenVPN Community 的 VPN 管理系统，提供 Web 管理、CLI、公网 API、用户与证书管理、站点组网及防火墙策略。

## 架构

| 项目 | 默认值 |
|---|---|
| Python | 3.10+ |
| 管理界面 | `0.0.0.0:8880` |
| 公网 API | `0.0.0.0:8881` |
| 进程 / 服务 | 单 Python 进程、单 `openvpn-mgmt.service` |
| 数据库 | `data/nicevpn.db` |

## 功能

- OpenVPN / EasyRSA 初始化、证书和客户端配置管理
- 用户、用户组、标签、设备绑定
- 对端站点、Mesh 路由与远端防火墙
- iptables / ipset 规则管理
- 企业 SSO、审计、备份恢复与日志
- Web 管理、CLI 和公网 API

## 快速启动

需要 Python 3.10+。Linux 主机上的 OpenVPN、PKI 和防火墙依赖可通过初始化向导安装或复用。

```bash
# 使用系统中已安装的 Python 3.10+，例如 3.11
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
./start.sh
```

访问管理界面：`http://<主机>:8880`。

仓库已包含 Vue 生产构建产物，正常启动不需要 Node.js、npm 或 Docker。

## 配置文件与管理后台登录

项目根目录使用 `config.yml` 保存系统配置，`config.example.yml` 包含所有默认字段：

```bash
cp config.example.yml config.yml
chmod 600 config.yml
```

新环境可先复制模板再修改；**已有 `data/config.json` 的旧环境不要预先复制模板**，首次启动会自动迁移所有既有设置到根目录 `config.yml`，并保留旧 JSON 文件。

默认不要求管理后台登录。如需开启：

```yaml
admin_login_enabled: true
admin_username: admin
admin_password: "请设置自己的强密码"
```

首次启动时会将明文转换为 Argon2id 哈希并从 `config.yml` 清除；也可以不填 `admin_password`，程序将随机生成初始密码，仅保存在本机 `data/admin_initial_password`（权限 600），查看后立即重置。**没有仓库内置的固定弱密码。**

```bash
# 推荐：交互式重置，密码不在终端历史中出现
ovpn-cli admin password reset
# 或者还未安装 CLI 包装器
./start.sh cli admin password reset
```

密码修改后重启 NiceVPN；管理端 8880 生效，公网 VPN API 8881 独立认证。更多说明见 [docs/configuration.md](docs/configuration.md)。

## 生产部署

使用 systemd：

```bash
sudo -E ./start.sh daemon
```

只安装一个 `openvpn-mgmt.service`，同时管理两个端口：

- `8880`：管理界面，建议仅内网或 VPN 可访问。
- `8881`：公网 API、一次性下载和 SSO 回调。公网使用时建议经 HTTPS 反向代理。

监听地址可写入 `/etc/nicevpn/listen.env`：

```ini
NICEVPN_UI_HOST=0.0.0.0
NICEVPN_UI_PORT=8880
NICEVPN_API_HOST=0.0.0.0
NICEVPN_API_PORT=8881
```

Docker 构建入口：`deploy/docker/Dockerfile`。容器需按部署方式提供 OpenVPN 运行环境及持久化数据目录。

## 数据

- SQLite：用户、组、对端、标签和防火墙等结构化数据。
- 配置：项目根目录 `config.yml`；运行文件：`data/` 内的会话密钥、SQLite、审计等，以及 OpenVPN 的 PKI/CCD/OVPN。
- 备份时同时保留根目录 `config.yml`、`data/` 和 `backups/`；启用 SSO 时，数据库必须与 `data/sso_provider_master.key` 一起备份。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/overview.md](docs/overview.md) | 功能概览 |
| [docs/security.md](docs/security.md) | SSO 与设备绑定 |
| [docs/firewall-tags.md](docs/firewall-tags.md) | 标签、防火墙与 Mesh |
| [docs/storage.md](docs/storage.md) | SQLite 与迁移 |
| [docs/configuration.md](docs/configuration.md) | 根目录配置、管理员登录与旧版迁移 |
| [docs/logging.md](docs/logging.md) | 日志与审计 |
| [docs/install-layout.md](docs/install-layout.md) | OpenVPN / EasyRSA 安装目录、安装模式与版本回滚 |
| [api.md](api.md) | 公网 API |
| [cli.md](cli.md) | CLI |
| [agent.md](agent.md) | AI / 开发约束 |
| `openspec/changes/` | 变更设计与历史记录 |

## 开发

```bash
pip install -r requirements.txt
python -m pytest tests/unit -q
./start.sh --build-ui
```

`./build-ui.sh` 也可单独构建前端。测试基线以 GitHub Actions 配置为准。
