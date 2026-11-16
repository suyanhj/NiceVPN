# NiceVPN CLI

CLI 与 Web 共用 Service 和 SQLite 数据。

## 入口

```bash
./start.sh cli --help       # 使用项目 Python 环境
python cli.py --help        # 唯一 Python CLI 入口
```

`main.py` 仅启动 Web 管理端和公网 API。

## 命令

| 命令 | 用途 |
|---|---|
| `group list [--json]` | 列出用户组 |
| `group show 组选择器` | 查看用户组 |
| `group add --name NAME --subnet CIDR` | 创建用户组 |
| `group update 组选择器 [--name NAME] [--subnet CIDR]` | 修改用户组 |
| `group enable/disable/delete 组选择器` | 启用、停用或删除用户组 |
| `user list [筛选项] [--json]` | 列出用户 |
| `user show USER` | 查看用户 |
| `user add --username USER 组选择器 [--password PASS]` | 创建用户 |
| `user move USER 组选择器` | 切换用户组 |
| `user enable/disable/delete/kick USER` | 管理用户状态 |
| `user device show/reset USER` | 查看或重置设备绑定 |
| `user ovpn show/export/regenerate USER` | 管理 OVPN |
| `add-firewall ...` | 添加或导入防火墙规则 |
| `hash-admin-password` | 生成管理密码哈希 |

组选择器必须二选一：`--group-name NAME` 或 `--group-id ID`。用户列表还支持 `--status active|disabled`。

旧命令 `add-group`、`add-user` 仍兼容；新脚本使用 `group`、`user` 分层命令。

## 示例

```bash
./start.sh cli group add --name 研发组 --subnet 10.224.1.0/24
./start.sh cli user add --username alice --group-name 研发组
./start.sh cli --dry-run user move alice --group-name 运维组
./start.sh cli user ovpn export alice --output /root/alice.ovpn
./start.sh cli add-firewall --owner-type group --group-name 研发组 --iptables-file ./rules.txt
```

修改命令支持任意位置的 `--dry-run`；列表命令支持 `--json`。`user add --password` 只用于 EasyRSA 加密客户端私钥，不是 NiceVPN 登录密码。

## Bash 自动补全

项目使用 `argcomplete`，执行安装脚本：

```bash
bash deploy/shell/install-ovpn-cli-symlink.sh
# 明确使用系统 Python 3.11
PYTHON_BIN=python3.11 bash deploy/shell/install-ovpn-cli-symlink.sh
```

解释器识别规则与 `start.sh` 共用 `deploy/shell/python-runtime.sh`：优先项目虚拟环境和已激活环境；系统环境先检查通用 `python3`，若低于 3.10，再按版本号检测 `python3.15`、`python3.14`……`python3.10`，最后检查 `python`。低于 3.10 的候选项会跳过，不会因为系统 `python3` 指向旧版本而提前退出。

安装器会生成固定使用所选解释器的 `ovpn-cli` 启动包装器，不依赖 `#!/usr/bin/env python3` 再次选择解释器。脚本会输出两行配置，加入 `~/.bashrc` 后执行 `source ~/.bashrc`：第一行加入命令目录，第二行注册补全。

```bash
ovpn-cli <TAB><TAB>
ovpn-cli group <TAB><TAB>
ovpn-cli user <TAB><TAB>
```

支持子命令、参数和 `--iptables-file` 路径补全。各级帮助：`ovpn-cli user ovpn --help`。
