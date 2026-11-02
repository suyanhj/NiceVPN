# OpenVPN 可视化管理系统

基于 **NiceGUI** 与 **FastAPI** 的 OpenVPN 运维控制台：Web 管理用户、组、证书、防火墙与对端站点；并提供 **HTTP Basic** 下的 `**/api/vpn`** 创建账号、批量分发、删除与重置设备绑定等接口。

- **Python**：3.10+（GitHub Actions 当前以 Python 3.14 作为唯一测试基线）
- **默认监听**：`0.0.0.0:8880`（`app/core/constants.py`）

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

推荐使用根目录 `start.sh`。普通模式会先通过 Docker 多阶段构建导出 V2 前端静态产物，再以前台方式启动 NiceGUI；宿主机无需安装 Node/npm。

```bash
pip install -r requirements.txt
./start.sh
```

浏览器访问 `http://<主机>:8880/`；未完成初始化时进入 **First Run Setup**。

生产环境可直接使用 daemon 模式。它会先完成前端构建，再将当前代码真实目录和实际 Python 解释器路径写入 systemd unit，随后 enable + restart：

```bash
sudo -E ./start.sh daemon
```

daemon 模式是幂等的，更新代码后再次执行即可重新构建前端、同步 service 并重启服务。systemd 本身只运行 `python main.py`，不会在每次服务重启时重复构建前端。

如只想构建前端产物、不启动服务：

```bash
./build-ui.sh
```

默认输出到 `app/ui/vue/dist/`。也可以指定输出目录，方便本地调试、CI 或其他容器流程直接复用：

```bash
./build-ui.sh /tmp/nicevpn-ui-dist
```

如已存在 `app/ui/vue/dist/` 构建产物，可跳过启动前的前端构建：

```bash
NICEVPN_BUILD_UI=0 ./start.sh
./start.sh --no-build
sudo -E ./start.sh daemon --no-build
```

CLI 不需要前端构建：

```bash
./start.sh cli --help
```

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

GitHub Actions 在 `admin-ui` / `main` 的 push 与 pull request 上验证 Python 3.14，并单独通过 Docker 构建 V2 Vue 前端、运行 Chrome smoke 与 OpenSpec strict validation。

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

因此 `systemctl restart openvpn-mgmt` 不会重新构建 Vue 前端。需要发布新前端时再次执行 `./start.sh daemon`；如发布包已经包含 V2 前端产物，可使用 `./start.sh daemon --no-build`。

### Docker

生产镜像使用多阶段构建：Node 22 只存在于 `frontend-builder` 阶段，最终 runtime 为 Python 3.14，不携带 Node/npm。默认针对国内网络优化：

- Node / Python 基础镜像：华为 SWR 公共镜像路径
- Debian APT：中科大 `mirrors.ustc.edu.cn`
- npm：阿里/npmmirror `https://registry.npmmirror.com`
- apt、pip、npm 下载缓存使用 BuildKit cache mount，不写入最终 runtime 层

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

- 默认 Web UI 监听 `0.0.0.0:8880`；使用 `--network host` 时无需额外 `-p`。
- 目标环境以 **Linux + OpenVPN + EasyRSA** 与控制台能力匹配为准。
- 须配置 `download_base_url` 或反代 `Host` / `X-Forwarded-*`，否则创建用户可能无法生成可达下载链接。

构建源均可通过 build arg 覆盖，例如：

```bash
docker build \
  --build-arg NODE_IMAGE=your-registry/node:22-alpine \
  --build-arg PYTHON_IMAGE=your-registry/python:3.14-slim \
  --build-arg NPM_REGISTRY=https://registry.npmjs.org \
  --build-arg DEBIAN_MIRROR=deb.debian.org \
  -t nicevpn:latest \
  -f deploy/docker/Dockerfile \
  .
```

## 开发约定

代码注释与文档字符串 **中文**，标识符 **英文**。变更流程见仓库根 **AGENTS.md** 与 **OpenSpec**。