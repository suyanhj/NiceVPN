# NiceVPN CLI

CLI 与 Web 共用同一套 Service 和 SQLite 数据。

## 启动

推荐使用项目入口，确保选用正确的 Python 环境：

```bash
./start.sh cli --help
```

等价入口：

```bash
python main.py cli --help
python cli.py --help
```

## 通用选项

- 修改命令支持 `--dry-run`，可放在命令任意位置；只执行解析、查找和校验，不落盘。
- 列表命令支持 `--json`，适合脚本读取。
- 具体参数以当前 CLI 帮助为准。

## 命令速查

| 命令 | 用途 |
|---|---|
| `group list [--json]` | 列出用户组 |
| `group show --group-name NAME` / `--group-id ID` | 查看用户组 |
| `group add --name NAME --subnet CIDR` | 创建用户组 |
| `group update [组选择器] [--name NAME] [--subnet CIDR]` | 修改名称或子网 |
| `group enable/disable/delete [组选择器]` | 启用、停用或删除用户组 |
| `user list [筛选项] [--json]` | 列出用户 |
| `user show USER` | 查看用户 |
| `user add --username USER [组选择器] [--password PASS]` | 创建用户 |
| `user move USER [组选择器]` | 切换用户组 |
| `user enable/disable/delete/kick USER` | 启用、停用、删除或踢下线 |
| `user device show/reset USER` | 查看或重置设备绑定 |
| `user ovpn show USER` | 查看 OVPN 文件状态 |
| `user ovpn export USER --output PATH` | 导出 OVPN |
| `user ovpn regenerate USER` | 重建 OVPN |
| `add-firewall ...` | 添加或导入中心防火墙规则 |
| `hash-admin-password` | 交互生成管理后台 Argon2id 密码哈希 |

组选择器为 `--group-name NAME` 或 `--group-id ID`。

用户列表筛选：`--group-name`、`--group-id`、`--status active|disabled`。

### 常用示例

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

`user add --password` 只用于 EasyRSA 加密客户端私钥，不是 NiceVPN 登录密码，也不会保存为用户密码。

### 防火墙导入

```bash
./start.sh cli add-firewall \\
  --owner-type group \\
  --group-name 研发组 \\
  --iptables-file ./rules.txt
```

单条规则可用 `--iptables-line`；用户规则用 `--owner-type user --username USER`。

## 兼容命令

旧脚本仍可使用 `add-group` 和 `add-user`。新脚本请使用 `group`、`user` 分层命令。

## 实时帮助

```bash
./start.sh cli group --help
./start.sh cli user --help
./start.sh cli user ovpn --help
./start.sh cli add-firewall --help
```

如需 Bash 补全，运行 `bash deploy/shell/install-ovpn-cli-symlink.sh`，再按脚本提示启用 argcomplete。
