# OpenVPN 管理系统开发指南

面向 AI / 开发者的结构与约定速查。**最后更新：2026-05-06**

> **语言规范**：所有回复和文档使用中文（代码标识符使用英文）。

## 活跃技术栈

- **语言**: Python 3.10+
- **Web UI**: NiceGUI 3.x（同进程内嵌 ASGI；含拖拽等交互）
- **后端接口**: FastAPI（挂载于 NiceGUI 的 `app` 上；版本以 `requirements.txt` 为准，当前约 0.135）
- **运行时**: uvicorn[standard]
- **数据校验**: pydantic v2
- **配置读取**: python-box（default_box=True）
- **HTTP 客户端**: requests；通知侧另有钉钉 / 企业微信 Webhook 库（见 `notify`）
- **定时任务**: schedule（证书到期检查等）
- **测试**: pytest + pytest-asyncio（主路径：`tests/unit/`）
- **防火墙**: iptables + ipset（subprocess）
- **证书管理**: EasyRSA（subprocess 封装）
- **对端组网**: paramiko（SSH）、netaddr 等（见 `peer_instance`）
- **服务管理**: systemd / subprocess（依部署环境）

## 项目结构

```text
app/
├── api/               # FastAPI：download.py（GET /download/{token}）、vpn_ops.py（/api/vpn/*，HTTP Basic）
├── core/              # 配置、初始化向导、调度
├── models/            # Pydantic 模型与配置形状
├── services/
│   ├── openvpn/       # 安装检测、实例与 server.conf 生成
│   ├── easyrsa/       # EasyRSA 封装
│   ├── cert/          # 证书生命周期（与 UI 证书页配合）
│   ├── firewall/      # iptables + ipset、规则服务、导入
│   ├── user/          # 用户 CRUD、设备绑定、ovpn 生成、批量导入/下载
│   ├── group/         # 组 CRUD、子网
│   ├── peer_instance/ # 对端实例、CCD iroute/mesh、远端 SSH/OpenVPN/iptables
│   ├── download/      # 一次性链接、bundle_zip（API 批量 zip）
│   ├── notify/        # 注册表 + 插件（钉钉、企业微信等）
│   ├── monitor/       # 服务监控辅助
│   └── config_editor/ # 配置备份等
├── ui/pages/          # NiceGUI 页面
├── ui/components/     # 可复用组件
├── ui/theme.py        # 全局样式（含初始化向导布局）
├── utils/             # CIDR、文件锁、审计、api_basic_credentials、public_base_url 等
└── cli/               # python main.py cli …
data/                  # JSON 等运行时数据（勿入库）
tests/unit/            # 单元测试（主要）
tests/integration/     # 集成占位，以 unit 为准
main.py                # python main.py → Web；python main.py cli …
api.md                 # 公网 VPN API 说明（Basic、批量、下载）
README.md              # 入门与文档导航
docs/overview.md       # 产品能力说明（设备绑定、Mesh 等）
```

## 常用命令

```bash
python main.py              # 启动 Web（默认 0.0.0.0:8880）
python main.py cli --help   # 命令行子命令
pytest tests/unit -q        # 单元测试（推荐）
pip install -r requirements.txt
```

## 代码规范

- 标识符使用英文，遵循 PEP 8；**注释与文档字符串使用中文**（与本仓库 `AGENTS.md` 一致）。
- 复杂逻辑（设备绑定、iptables 重建、EasyRSA/PKI 调用、对端同步）须有可读中文注释；关键点打日志。
- 外部工具通过 subprocess 或既有封装调用，避免在业务里「手写 PKI / 包过滤核心逻辑」。
- JSON 持久化使用 `utils/file_lock.py` 等既有并发安全方式。

## 最近变更

- 公网 **`/api/vpn`**：创建用户、批量合并 zip（`vpns_*.zip`）、按前缀删除与重置设备绑定；详情见 **api.md**。
- **api** 层已与 **download** 分离；下载令牌仍为无 Basic 的 `GET /download/{token}`。

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
