# NiceVPN CLI

CLI 与 Web 共用同一套 Service 和 SQLite 数据。命令按资源分组，创建、修改、删除等操作统一放在所属资源下面，不再提供平级的 `add-group`、`add-user`、`add-firewall` 等重复入口。

## 入口

```bash
./start.sh cli --help       # 使用项目 Python 环境
python3.11 cli.py --help    # 直接调用（需 Python >= 3.10）
```

`main.py` 仅启动 Web 管理端和公网 API。安装 `ovpn-cli` 后可在任意目录使用同一套命令。

## 命令结构

| 一级命令 | 二级命令 | 用途 |
|---|---|---|
| `group` | `list/show/add/update/delete/enable/disable` | 用户组管理 |
| `user` | `list/show/add/delete/enable/disable/move/kick` | VPN 用户管理 |
| `user` | `device show/reset` | 设备绑定管理 |
| `user` | `ovpn show/export/regenerate` | 客户端配置管理 |
| `tag` | `list/show/add/update/delete` | 标签及策略属性管理 |
| `tag` | `member list/add/remove/set/clear` | 标签与用户、用户组关联 |
| `firewall` | `rule list/add/delete/enable/disable` | 中心防火墙规则管理 |
| `admin` | `password hash/reset` | 生成密码哈希、交互重置密码 |

## 用户组

```bash
ovpn-cli group list
ovpn-cli group show --group-name "研发组"
ovpn-cli group add --name "研发组" --subnet 10.224.1.0/24
ovpn-cli group update --group-name "研发组" --subnet 10.224.2.0/24
ovpn-cli group enable --group-name "研发组"
ovpn-cli group disable --group-name "研发组"
ovpn-cli group delete --group-name "研发组"
```

组选择器使用 `--group-name NAME` 或 `--group-id ID`，二选一。

## 用户

```bash
ovpn-cli user list
ovpn-cli user list --group-name "研发组" --status active
ovpn-cli user show alice
ovpn-cli user add --username alice --group-name "研发组"
ovpn-cli user move alice --group-name "运维组"
ovpn-cli user enable alice
ovpn-cli user disable alice
ovpn-cli user kick alice
ovpn-cli user delete alice

ovpn-cli user device show alice
ovpn-cli user device reset alice
ovpn-cli user ovpn show alice
ovpn-cli user ovpn export alice --output /root/alice.ovpn
ovpn-cli user ovpn regenerate alice
```

`user add --password` 只用于 EasyRSA 加密客户端私钥，不是 NiceVPN 登录密码。OVPN 的查看、导出和重建统一放在 `user ovpn` 下，不再另设重复的 `user regenerate-ovpn`。

## 标签

标签支持设备绑定豁免、企业 SSO 策略，以及关联用户和用户组。标签成员变化会通过现有 TagService 触发中心防火墙、对端防火墙和 Mesh 路由收敛。

```bash
# 标签 CRUD
ovpn-cli tag list
ovpn-cli tag list --json
ovpn-cli tag add --name "研发人员" --description "研发部门设备"
ovpn-cli tag add --name "预览标签" --dry-run
ovpn-cli tag add --name "免设备绑定" --device-binding-exempt
ovpn-cli tag add --name "企业认证" --sso-required
ovpn-cli tag show "研发人员"
ovpn-cli tag update "研发人员" --description "研发部门 VPN 用户"
ovpn-cli tag update "免设备绑定" --device-binding-exempt
ovpn-cli tag update "企业认证" --no-sso-required
ovpn-cli tag delete "研发人员"

# 查看标签成员
ovpn-cli tag member list "研发人员"

# 增加/移除成员；--user 和 --group 可以重复指定
ovpn-cli tag member add "研发人员" --user alice --user bob --group "研发组"
ovpn-cli tag member remove "研发人员" --user bob
ovpn-cli tag member add "研发人员" --group "运维组"

# 整体替换成员，或明确清空全部成员
ovpn-cli tag member set "研发人员" --user alice --group "研发组"
ovpn-cli tag member clear "研发人员"
```

标签名称和 ID 均可用于 `TAG`。组成员的 `--group` 同样支持组名称或 ID。删除标签时会检查用户、用户组、中心防火墙规则、对端 Mesh 路由和远端防火墙引用；仍在使用的标签不能删除。

## 防火墙规则

```bash
# 查看规则
ovpn-cli firewall rule list
ovpn-cli firewall rule list --owner-type tag --owner-id <标签ID>

# 从 iptables 命令文本导入规则，归属对象支持 group/user/tag
ovpn-cli firewall rule add --owner-type group --owner "研发组" --iptables-file ./rules.txt
ovpn-cli firewall rule add --owner-type user --owner alice --iptables-line "iptables -A FORWARD -s 10.224.1.0/24 -j ACCEPT"
ovpn-cli firewall rule add --owner-type tag --owner "研发人员" --iptables-file ./tag-rules.txt

# 按规则 ID 管理
ovpn-cli firewall rule enable <规则ID>
ovpn-cli firewall rule disable <规则ID>
ovpn-cli firewall rule delete <规则ID>
```

`firewall rule add` 将输入的 iptables 命令解析为 NiceVPN 规则并写入 SQLite，不会直接执行输入的原始命令。批量导入同一归属的规则时统一校验并刷新一次中心防火墙。

## 管理后台工具

```bash
ovpn-cli admin password hash   # 生成哈希
ovpn-cli admin password reset  # 重置密码写入 data/config.json；启用登录须配置根目录 YAML，重启生效
```

`config.yml` 仅管理登录开关、用户名和会话参数，不存密码哈希；密码哈希及其他长期设置写在 `data/config.json`。CLI 重置不修改 YAML，请在需要启用登录时设置 `admin_login_enabled: true`。

## 试运行

所有修改类命令都支持在任意位置添加 `--dry-run`。试运行执行相同的参数解析和业务校验，只输出预览，不写入 SQLite，也不触发内核规则刷新。

```bash
ovpn-cli --dry-run user add --username alice --group-name "研发组"
ovpn-cli tag member add "研发人员" --user alice --dry-run
ovpn-cli firewall rule delete <规则ID> --dry-run
```

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
ovpn-cli tag <TAB><TAB>
ovpn-cli firewall rule <TAB><TAB>
```

支持各级子命令、参数和 `--iptables-file` 路径补全。帮助示例：`ovpn-cli tag member --help`、`ovpn-cli firewall rule add --help`。
