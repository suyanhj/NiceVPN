# OpenVPN 可视化管理系统

基于 **NiceGUI** 与 **FastAPI** 的 OpenVPN 运维控制台：Web 管理用户/组/证书/防火墙/对端站点，并提供带 HTTP Basic 的 **`/api/vpn`** 公网创建与运维接口。

- **Python**：3.10+
- **默认监听**：`0.0.0.0:8880`（见 `app/core/constants.py`）

## 功能概览

| 模块 | 说明 |
|------|------|
| 初始化向导 | 环境检测、PKI、地址池与 `push_lan_routes`、默认接入实例 |
| 用户 / 组 | VPN 账号、批量命名、一次性下载链接 |
| 对端站点 | 组网、CCD、`iroute` / mesh 路由联动 |
| 防火墙 | iptables / ipset 规则与导入 |
| 服务管理 | OpenVPN 实例与本机 `systemctl` 协作（依部署环境） |
| 系统设置 | `download_base_url`、内网 push 路由、API Basic 凭据等 |
| 公网 API | `POST/DELETE /api/vpn/users…`、批量合并 zip、详见下文 |

## 快速开始

### 1. 安装依赖

```bash
cd script/py/vpn   # 或在仓库中进入本目录
pip install -r requirements.txt
```

### 2. 启动 Web

```bash
python main.py
```

浏览器访问：`http://<主机>:8880/`。首次未完成初始化时会进入 **First Run Setup** 向导。

### 3. 命令行（可选）

```bash
python main.py cli --help
```

具体子命令以 `app/cli/entry.py` 为准。

## 公网 HTTP API 与下载

- **说明文档**：[api.md](./api.md)（Basic 认证、`/api/vpn/users`、批量 `/api/vpn/users/batch`、`DELETE` 与重置设备绑定、`GET /download/{token}` 等）。
- API 挂载前缀：`/api`；下载路由与 UI 同源（反代时建议统一转发到同一进程）。

系统初始化后若启用相关逻辑，会在 `data/api_basic_credentials.json` 等处写入 VPN API 用凭据（勿提交版本库）。

## 测试

```bash
pytest tests/unit -q
```

## 目录结构（摘）

```text
main.py                 # Web 入口；可选 python main.py cli …
app/
  api/                  # FastAPI：download、vpn_ops
  core/                 # 配置、初始化、调度
  models/               # Pydantic 与配置模型
  services/             # OpenVPN、EasyRSA、用户、防火墙、对端、下载…
  ui/                   # NiceGUI 页面与主题
deploy/                 # systemd、sysctl 等示例
data/                   # 运行时 JSON（部署时自行备份）
tests/unit/             # 单元测试
api.md                  # 对外 API 说明
```

## 生产部署提示

- **目标环境**：Linux + OpenVPN + EasyRSA 等与本项目控制面匹配；Windows 多用于开发或仅跑 UI 逻辑演示。
- 可参考 `deploy/systemd/openvpn-mgmt.service` 等文件，按现网路径与用户调整。
- 须在系统设置或反代中正确配置 **`download_base_url`** / `Host`，否则创建用户时可能无法生成可达的一次性下载链接。

## 开发约定

- 代码注释与文档字符串：**中文**；标识符：**英文**。
- 功能迭代若已有 OpenSpec 变更，优先遵循仓库内 `openspec/changes/` 的流程（见项目根 `AGENTS.md`）。

## 相关文档

- [api.md](./api.md) — VPN 公网 API
- [CLAUDE.md](./CLAUDE.md) — 技术栈与结构速查（面向开发者）
