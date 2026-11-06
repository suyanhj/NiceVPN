# OpenVPN 可视化管理系统

基于 **NiceGUI** 与 **FastAPI** 的 OpenVPN 运维控制台：Web 管理用户、组、证书、防火墙与对端站点；并提供 **HTTP Basic** 下的 `**/api/vpn`** 创建账号、批量分发、删除与重置设备绑定等接口。

- **Python**：3.10+（GitHub Actions 当前以 Python 3.14 作为唯一测试基线）
- **默认监听**：管理界面 `0.0.0.0:8880`；独立公网 API `0.0.0.0:8881`。两者为不同进程，公网只需按网络策略开放 API 入口。

## 主要能力（摘要）

- **PKI / 隧道**：EasyRSA、tls-crypt-v2、CCD、多端 `.ovpn` 与一次性下载链。
- **内网分流**：`push_lan_routes` 推送局域网路由；与中心 NAT / FORWARD / iptables 等联动。
- **设备绑定**：`client-connect` + 客户端 **IV** 指纹；支持仅审计、弱指纹（默认）、强 MAC 三档；单账号绑定档案，可控制台或 API 按前缀重置。
- **对端与 Mesh**：站点绑定用户 + 后方 LAN CIDR，`ifconfig-push` / `**iroute`**；按用户组下发 **mesh** `push route`，多站点经中心互访内网（变更后需重连等以界面为准）。对端管理借鉴了项目：**netmaker**
- **防火墙**：iptables + ipset、SQLite 中心规则、简单导入、与对端规则协作。
- **通知**：钉钉、企业微信等插件式通道。
- **诊断与恢复**：系统一致性诊断；完整备份/恢复覆盖 SQLite、配置、PKI/CRL、CCD、OVPN、设备绑定与 OpenVPN 实例配置，恢复前自动生成回退备份。
- **CLI**：`python main.py cli` 或 `cli.py`；bash 补全见 `deploy/shell/install-ovpn-cli-symlink.sh`。

更完整的产品说明见 **[docs/overview.md](./docs/overview.md)**。

## 功能菜单


| 模块     | 说明                                                  |
| ------ | --------------------------------------------------- |
| 初始化向导  | PKI、地址池、`push_lan_routes`、默认实例                      |
| 用户 / 组 | 账号、设备绑定、批量命名、下载链                                    |
| 对端站点   | LAN、CCD、中心转发、Mesh                                   |
| 防火墙    | iptables / ipset、导入                                 |
| 服务管理   | 实例与 `systemctl`（依环境）                                |
| 系统设置   | `device_bind_mode`、`download_base_url`、API Basic、通知 |
| 公网 API | 见 [api.md](./api.md)                                |


## 快速开始

推荐使用根目录 `start.sh`。正式 Vue 运行产物由 GitHub Actions 构建并提交到 `app/ui/vue/dist/`，普通启动直接使用仓库内预编译资源，因此生产宿主机**不需要 Node/npm，也不需要 Docker**。

```bash
pip install -r requirements.txt
./start.sh
```

浏览器访问 `http://<主机>:8880/`；未完成初始化时进入 **First Run Setup**。

公网 API 与管理端**分进程、分端口**：本地直接运行时，需要另开一个终端执行 `./start.sh api`，才能在 `8881` 上提供 `/api/vpn/*` 与 `/download/{token}`；原来的 `./start.sh` 仍只启动管理界面。API 端口不会返回管理页面、登录页、NiceGUI 资源或 Swagger 文档。管理端口不再提供 `/api/vpn/*`，已有调用方需切换到新端口。

生产环境可直接使用 daemon 模式。它会直接使用仓库内预编译前端资源，再将当前代码真实目录和实际 Python 解释器路径写入 systemd unit，随后 enable + restart：

```bash
sudo -E ./start.sh daemon
```

daemon 模式是幂等的。更新代码后再次执行即可同步并重启两个独立服务：`openvpn-mgmt`（Web）与 `openvpn-mgmt-api`（API）；systemd 本身不会在服务重启时构建 Vue。首次初始化及 SQLite 数据升级由管理端完成，API 进程不重复执行初始化迁移或定时任务。

可在 `/etc/nicevpn/listen.env`（root 可读）中配置独立监听地址，随后重启对应服务：

```ini
# 管理端可绑定内网 IP，仅允许可信网段访问 8880
NICEVPN_UI_HOST=0.0.0.0
NICEVPN_UI_PORT=8880
# 公网 API 对应的内部监听地址/端口
NICEVPN_API_HOST=0.0.0.0
NICEVPN_API_PORT=8881
```

推荐防火墙**拒绝公网访问管理端 `8880`**；API 基于 HTTP Basic，必须通过 HTTPS 入口或支持 TLS 的反向代理传输凭据，不能直接把明文 HTTP `8881` 暴露给公网。若使用 Nginx 终结 TLS，可把 `NICEVPN_API_HOST` 设为 `127.0.0.1`，让 Nginx 的公开端口仅代理 `/api/vpn/` 和 `/download/`。注意系统设置中的 `download_base_url` 应使用 API 的公网 HTTPS 地址（或留空让其根据反代请求推断），避免一次性下载链接指向内网 `8880`。

正常生产启动不需要构建前端。开发或需要强制重建时可使用：

```bash
./start.sh --build-ui
sudo -E ./start.sh daemon --build-ui
```

`build-ui.sh` 会优先使用本机 npm；没有 npm 时回退到 Docker：

```bash
./build-ui.sh
./build-ui.sh /tmp/nicevpn-ui-dist
```

`--no-build` 与 `NICEVPN_BUILD_UI=0` 继续保留兼容，但现在已经是默认行为。

CLI 不需要前端构建：

```bash
./start.sh cli --help
```

推荐使用分层命令：

```bash
# 用户组
./start.sh cli group list
./start.sh cli group show --group-name 开发组
./start.sh cli group add --name 开发组 --subnet 10.224.1.0/24
./start.sh cli group update --group-name 开发组 --name 研发组
./start.sh cli group disable --group-name 开发组
./start.sh cli group enable --group-name 开发组
./start.sh cli group delete --group-name 开发组

# 用户
./start.sh cli user list
./start.sh cli user show alice
./start.sh cli user add --username alice --group-name 开发组
./start.sh cli user move alice --group-name 运维组
./start.sh cli user disable alice
./start.sh cli user enable alice
./start.sh cli user kick alice
./start.sh cli user regenerate-ovpn alice

# 设备绑定
./start.sh cli user device show alice
./start.sh cli user device reset alice

# OVPN 文件
./start.sh cli user ovpn show alice
./start.sh cli user ovpn export alice --output /root/alice.ovpn
./start.sh cli user ovpn regenerate alice

./start.sh cli user delete alice
```

修改类命令均支持 `--dry-run`；列表命令支持 `--json` 便于脚本消费。旧的 `add-group` / `add-user` 命令继续兼容。

**命令行（可选）**：`python main.py cli --help`（逻辑见 `app/cli/entry.py`）。

### Python 解释器选择

NiceVPN 要求 **Python 3.10+**。启动脚本会校验真实解释器版本，并按以下优先级选择：

1. 显式环境变量 `PYTHON_BIN`
2. 当前激活的 `VIRTUAL_ENV`
3. 当前激活的 `CONDA_PREFIX`
4. 项目目录下的 `.venv/bin/python`
5. 当前 PATH 中满足版本要求的 Python

例如：

```bash
PYTHON_BIN=/usr/local/bin/python3.14 ./start.sh
PYTHON_BIN=/opt/nicevpn-venv/bin/python sudo -E ./start.sh daemon
```

daemon 安装时会把解释器的**绝对真实路径**固化到 systemd unit 中，因此 systemd 不依赖登录 shell 的 PATH 或虚拟环境激活状态。

## 文档


| 文档                                     | 用途                        |
| -------------------------------------- | ------------------------- |
| [docs/overview.md](./docs/overview.md) | 产品能力说明（设备绑定、Mesh、各模块展开）   |
| [api.md](./api.md)                     | 公网 VPN API（Basic、批量、下载令牌） |
| [CLAUDE.md](./CLAUDE.md)               | 技术栈与目录速查（开发者）             |
| `openspec/changes/`                    | 功能变更设计与任务归档               |


初始化后 `**data/api_basic_credentials.json`** 等为运行时数据，勿提交版本库。

结构化管理元数据（用户、用户组、对端、中心防火墙规则）使用 `data/nicevpn.db`。旧版本升级时会在启动阶段自动把对应 JSON 迁入 SQLite，并将旧 JSON 归档到 `backups/storage-migration-<UTC>/`；CCD、设备绑定、PKI/CRL、OVPN、远端链缓存与日志继续保持文件形态。
若需要降级到仍使用 JSON 的旧版本，必须先停止服务并从上述迁移归档恢复对应 JSON；旧版本不会读取 `nicevpn.db`。

## 测试

本地与 GitHub Actions 使用同一条单元测试命令：

```bash
python -m pytest tests/unit -q
```

GitHub Actions 在 `admin-ui` / `main` 的 push 与 pull request 上验证 Python 3.14，并运行 Vue 构建、Chrome smoke、Docker 构建与 OpenSpec strict validation。另有 `frontend-dist.yml`：当 `app/ui/vue/frontend/**` 变化时使用 Node 22 构建正式资源，写入 `frontend-build.json`，并把 `app/ui/vue/dist/` 自动提交回 `main`。

## 目录结构（摘）

```text
main.py / cli.py        # Web 与 CLI 入口
app/                    # api、core、models、services、ui
deploy/                 # systemd、shell 等示例
data/                   # 运行时数据（结构化元数据使用 nicevpn.db）
docs/overview.md        # 产品说明
api.md                  # 对外 API
```

## 生产部署

### systemd

推荐直接使用：

```bash
sudo -E ./start.sh daemon
```

`deploy/systemd/openvpn-mgmt.service` 现在是安装模板。daemon 模式会把模板中的代码目录与 Python 路径替换为当前机器的真实值，再安装到 `/etc/systemd/system/openvpn-mgmt.service`。

最终 systemd 只负责：

```text
<选定的 Python 绝对路径> <NiceVPN 真实目录>/main.py
```

因此 `systemctl restart openvpn-mgmt` 不会重新构建 Vue 前端。正式发布提交已经包含 CI 生成的 V2 前端产物，纯系统安装环境只需 Python 依赖即可运行；只有开发调试需要本机重新构建时才使用 `--build-ui`。

### Docker

生产镜像直接使用仓库中由 GitHub Actions 预编译并提交的 `app/ui/vue/dist/`，Docker 构建过程不再安装 Node/npm，也不再重复编译 Vue。最终 runtime 为 Python 3.14。默认针对国内网络优化：

- Python 基础镜像：华为 SWR 公共镜像路径
- Debian APT：中科大 `mirrors.ustc.edu.cn`
- apt、pip 下载缓存使用 BuildKit cache mount，不写入最终 runtime 层

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

- 默认 Web UI 监听 `0.0.0.0:8880`；容器 CMD 只启动管理端，若要 API 服务需另起 `python -m app.api.server` 进程或容器，并共享 `data/`、OpenVPN 配置/证书与系统网络权限。使用 `--network host` 时无需额外 `-p`。
- 目标环境以 **Linux + OpenVPN + EasyRSA** 与控制台能力匹配为准。
- 须配置 `download_base_url` 或反代 `Host` / `X-Forwarded-*`，否则创建用户可能无法生成可达下载链接。

构建源均可通过 build arg 覆盖，例如：

```bash
docker build \
  --build-arg PYTHON_IMAGE=your-registry/python:3.14-slim \
  --build-arg DEBIAN_MIRROR=deb.debian.org \
  -t nicevpn:latest \
  -f deploy/docker/Dockerfile \
  .
```

Docker 构建会校验 `app/ui/vue/dist/VpnSpotlightSurface.js`、`VpnInteractiveGrid.js` 与 `frontend-build.json`。缺少预编译运行产物时直接失败，避免制作出回退到开发 SFC 的生产镜像。

## 开发约定

代码注释与文档字符串 **中文**，标识符 **英文**。变更流程见仓库根 **AGENTS.md** 与 **OpenSpec**。
