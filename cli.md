# NiceVPN CLI

CLI 与 Web 共用 Service 和 SQLite 数据。

## 启动

推荐使用项目脚本，自动选择项目 Python 环境：

```bash
./start.sh cli --help
./start.sh cli user list
```

直接使用唯一 Python 入口：

```bash
python cli.py --help
python cli.py user list
```

不要再使用 `python main.py cli`；`main.py` 只负责启动 Web 管理端。

## 命令速查

| 命令 | 用途 |
|---|---|
| `group list [--json]` | 列出用户组 |
| `group show 组选择器` | 查看用户组 |
| `group add --name NAME --subnet CIDR` | 创建用户组 |
| `group update 组选择器 [--name NAME] [--subnet CIDR]` | 修改名称或子网 |
| `group enable/disable/delete 组选择器` | 启用、停用或删除用户组 |
| `user list [筛选项] [--json]` | 列出用户 |
| `user show USER` | 查看用户 |
| `user add --username USER 组选择器 [--password PASS]` | 创建用户 |
| `user move USER 组选择器` | 切换用户组 |
| `user enable/disable/delete/kick USER` | 启用、停用、删除或踢下线 |
| `user device show/reset USER` | 查看或重置设备绑定 |
| `user ovpn show USER` | 查看 OVPN 文件状态 |
| `user ovpn export USER --output PATH` | 导出 OVPN |
| `user ovpn regenerate USER` | 重建 OVPN |
| `add-firewall ...` | 添加或导入中心防火墙规则 |
| `hash-admin-password` | 交互生成管理后台 Argon2id 密码哈希 |

**组选择器**必须二选一：`--group-name NAME` 或 `--group-id ID`。

用户列表筛选：`--group-name`、`--group-id`、`--status active|disabled`。

## 常用示例

```bash
# 创建用户组
./start.sh cli group add --name 研发组 --subnet 10.224.1.0/24

# 创建用户
./start.sh cli user add --username alice --group-name 研发组

# 修改前预演
./start.sh cli --dry-run user move alice --group-name 运维组

# 导出客户端配置
./start.sh cli user ovpn export alice --output /root/alice.ovpn

# 踢下线
./start.sh cli user kick alice
```

`--dry-run` 可放在命令任意位置；只执行解析、查找和校验，不落盘。列表命令支持 `--json`。

`user add --password` 只用于 EasyRSA 加密客户端私钥，不是 NiceVPN 登录密码，也不会保存为用户密码。

### 防火墙导入

```bash
./start.sh cli add-firewall \
  --owner-type group \
  --group-name 研发组 \
  --iptables-file ./rules.txt
```

单条规则可用 `--iptables-line`；用户规则用 `--owner-type user --username USER`。

旧脚本仍可使用 `add-group` 和 `add-user`；新脚本请使用 `group`、`user` 分层命令。

## Bash 自动补全

项目已集成 `argcomplete`，无需额外编写补全规则。

```bash
bash deploy/shell/install-ovpn-cli-symlink.sh

# 如需指定 Python 环境
PYTHON_BIN=/path/to/python bash deploy/shell/install-ovpn-cli-symlink.sh
```

脚本优先检测项目 `.venv`，其次使用当前虚拟环境或系统 Python，并检查 `argcomplete` 是否已安装。它会输出两行配置；将它们加入 `~/.bashrc`，然后执行 `source ~/.bashrc`：

- 第一行将正确的 Python 环境加入 PATH。
- 第二行注册 `ovpn-cli` 的 argcomplete 补全。

测试：

```bash
ovpn-cli <TAB><TAB>
ovpn-cli group <TAB><TAB>
ovpn-cli user <TAB><TAB>
```

可补全子命令、参数和 `--iptables-file` 的文件路径。运行 `ovpn-cli` 时，需使用已安装 NiceVPN 依赖和 `argcomplete` 的 Python 环境。

## 帮助

```bash
./start.sh cli group --help
./start.sh cli user --help
./start.sh cli user ovpn --help
./start.sh cli add-firewall --help
```
