# OpenVPN 可视化管理系统

基于 **NiceGUI** 与 **FastAPI** 的 OpenVPN 运维控制台：Web 管理用户、组、证书、防火墙与对端站点；并提供 **HTTP Basic** 下的 `**/api/vpn`** 创建账号、批量分发、删除与重置设备绑定等接口。

- **Python**：3.10+（GitHub Actions 当前以 Python 3.14 作为唯一测试基线）
- **默认监听**：`0.0.0.0:8880`（`app/core/constants.py`）

## 主要能力（摘要）

- **PKI / 隧道**：EasyRSA、tls-crypt-v2、CCD、多端 `.ovpn` 与一次性下载链。
- **内网分流**：`push_lan_routes` 推送局域网路由；与中心 NAT / FORWARD / iptables 等联动。
- **设备绑定**：`client-connect` + 客户端 **IV** 指纹；支持仅审计、弱指纹（默认）、强 MAC 三档；单账号绑定档案，可控制台或 API 按前缀重置。
- **对端与 Mesh**：站点绑定用户 + 后方 LAN CIDR，`ifconfig-push` / `**iroute`**；按用户组下发 **mesh** `push route`，多站点经中心互访内网（变更后需重连等以界面为准）。对端管理借鉴了项目：**netmaker**
- **防火墙**：iptables + ipset、规则 JSON、简单导入、与对端规则协作。
- **通知**：钉钉、企业微信等插件式通道。
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

推荐使用根目录 `start.sh`。它会先通过 Docker 多阶段构建导出 V2 前端静态产物，再启动 NiceGUI；宿主机无需安装 Node/npm。

```bash
pip install -r requirements.txt
./start.sh
```

浏览器访问 `http://<主机>:8880/`；未完成初始化时进入 **First Run Setup**。

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
```

CLI 不需要前端构建：

```bash
./start.sh cli --help
```

**命令行（可选）**：`python main.py cli --help`（逻辑见 `app/cli/entry.py`）

## 文档


| 文档                                     | 用途                        |
| -------------------------------------- | ------------------------- |
| [docs/overview.md](./docs/overview.md) | 产品能力说明（设备绑定、Mesh、各模块展开）   |
| [api.md](./api.md)                     | 公网 VPN API（Basic、批量、下载令牌） |
| [CLAUDE.md](./CLAUDE.md)               | 技术栈与目录速查（开发者）             |
| `openspec/changes/`                    | 功能变更设计与任务归档               |


初始化后 `**data/api_basic_credentials.json`** 等为运行时数据，勿提交版本库。

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
data/                   # 运行时 JSON
docs/overview.md        # 产品说明
api.md                  # 对外 API
```

## 生产部署

### systemd

`deploy/systemd/openvpn-mgmt.service` 默认调用根目录 `start.sh`。首次启动或前端源码变化时，由 Docker 构建 `frontend-assets` 并写入 `app/ui/vue/dist/`，随后启动 Python 服务。

```bash
cp deploy/systemd/openvpn-mgmt.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now openvpn-mgmt
```

如果发布包已经预置 V2 前端产物，可通过 systemd override 设置 `NICEVPN_BUILD_UI=0`，启动时不再调用 Docker。

### Docker

生产镜像使用多阶段构建：Node 22 只存在于 `frontend-builder` 阶段，最终 runtime 为 Python 3.14，不携带 Node/npm。

```bash
docker build -t nicevpn:latest -f deploy/docker/Dockerfile .

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

## 开发约定

代码注释与文档字符串 **中文**，标识符 **英文**。变更流程见仓库根 **AGENTS.md** 与 **OpenSpec**。