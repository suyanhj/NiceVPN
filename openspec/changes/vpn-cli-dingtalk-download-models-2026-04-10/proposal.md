# vpn-cli-dingtalk-download-models-2026-04-10

## 概述

将以下**已实现、此前未写入 OpenSpec** 的能力纳入变更记录，便于归档与审计：

1. **命令行子命令**（`add-group` / `add-user` / `add-firewall`）、`--dry-run` 任意位置语义、**argcomplete** 补全（含 `--dry-run` 与 `--iptables-file` 路径）。
2. **钉钉推送**：改用 **`dingtalkchatbot`**；配置支持 **`dingtalk_secret`（SEC 加签）**。
3. **下载链接基址**：启动时 **`set_listen_http_base`**（`listen_lan` 优先选如 `eth0` 的 LAN IP）；请求侧 **`public_base_url_from_request` / `resolve_download_base_url`** 与监听快照回退。
4. **剪贴板**：HTTP + 局域网场景下 **`navigator.clipboard` 不可靠** → **`copy_clipboard`**（`textarea` + `execCommand` 回退）。
5. **`app.models` 导出**：**PEP 562 `__getattr__` 惰性加载**，避免 `from app.models.config import SystemConfig` 时强行导入 `firewall` 等子模块。

## 背景与动机

- 运维需在无 Web 场景批量建组/用户/导入 iptables 规则；需与 Web 相同校验，并支持试运行。
- 钉钉自定义机器人常见 **加签**；与 `script/py/crt` 对齐使用同一第三方库。
- 默认 `localhost` 生成下载链接在生产不可用；需在服务端推断可访问的 HTTP 基址。
- 部分浏览器在非 HTTPS 下拒绝剪贴板 API。
- 服务器上若缺某一模型文件，旧式 `__init__.py` 全量 import 会导致无关模块导入失败。

## 范围（已实现）

| 领域 | 主要路径 |
|------|-----------|
| CLI 入口 | `cli.py`、`main.py`（`python main.py cli …`）、`app/cli/entry.py`、`app/cli/iptables_parse.py` |
| Tab 补全 | `argcomplete`、`deploy/shell/install-ovpn-cli-symlink.sh`；`--iptables-file` 使用 `FilesCompleter` |
| 钉钉 | `app/services/notify/dingtalk.py`、`app/models/config.py`（`dingtalk_secret`）、设置页表单项 |
| 监听基址 | `app/utils/listen_lan.py`、`main.run_web` 内 `set_listen_http_base` |
| 下载 URL | `app/utils/public_base_url.py`；用户页等调用 `resolve_download_base_url` |
| 剪贴板 | `app/ui/copy_clipboard.py`；用户/组/证书等页复用 |
| 模型包 | `app/models/__init__.py`（`__getattr__` + `__all__`） |

## 非目标

- 不将 **`python main.py cli`** 整条链路注册为 argcomplete 目标（文档建议独立 `cli.py` 或符号链接）；见 `install-ovpn-cli-symlink.sh`。
- 不保证剪贴板在极端浏览器策略下 100% 成功；失败时应有明确 UI 提示（以当前页实现为准）。

## 与旧 OpenSpec 的交叉引用

- **`vpn-lan-firewall-devicebind-2026-04-09`**：防火墙运行时与 CLI `add-firewall` 导入规则同属 iptables/JSON 规则体系；CLI 侧以 **`FirewallRuleService`** 为准。
- **`vpn-pki-jsonlock-import-2026-04-09`**：CLI `add-user` 依赖已初始化 PKI；与批量导入/证书生成路径相关。
