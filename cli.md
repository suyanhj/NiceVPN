# NiceVPN CLI

NiceVPN CLI 用于在不打开 Web 管理界面的情况下管理用户、用户组、设备绑定、OVPN 和部分防火墙规则。

CLI 与 Web 使用同一套服务层和 SQLite 数据，不维护独立数据源。

## 启动方式

推荐通过 `start.sh`，这样会自动使用 NiceVPN 当前选择的 Python 环境：

```bash
./start.sh cli --help
```

等价入口：

```bash
python main.py cli --help
python cli.py --help
```

## 通用行为

### `--dry-run`

修改类命令支持 `--dry-run`，并且可以放在命令行任意位置。

```bash
./start.sh cli --dry-run user disable alice
./start.sh cli user disable alice --dry-run
```

dry-run 会执行参数解析、对象查找和业务校验，但不会执行最终修改。

### JSON 输出

列表命令可使用 `--json`：

```bash
./start.sh cli group list --json
./start.sh cli user list --json
```

详情和修改命令通常直接输出 JSON 结果，适合脚本调用。

## 用户组

### 列表

```bash
./start.sh cli group list
./start.sh cli group list --json
```

### 查看

按组名：

```bash
./start.sh cli group show --group-name 开发组
```

按组 ID：

```bash
./start.sh cli group show --group-id <uuid>
```

### 创建

```bash
./start.sh cli group add \
  --name 开发组 \
  --subnet 10.224.1.0/24
```

CIDR 会走与 Web 相同的规范化和冲突校验。

### 修改

```bash
./start.sh cli group update \
  --group-name 开发组 \
  --name 研发组

./start.sh cli group update \
  --group-name 研发组 \
  --subnet 10.224.2.0/24
```

`--name` 和 `--subnet` 可单独使用，也可同时修改。

### 启用 / 停用 / 删除

```bash
./start.sh cli group enable --group-name 研发组
./start.sh cli group disable --group-name 研发组
./start.sh cli group delete --group-name 研发组
```

这些命令同样支持 `--group-id`。

## 用户

### 列表

```bash
./start.sh cli user list
./start.sh cli user list --json
```

按用户组过滤：

```bash
./start.sh cli user list --group-name 开发组
./start.sh cli user list --group-id <uuid>
```

按状态过滤：

```bash
./start.sh cli user list --status active
./start.sh cli user list --status disabled
```

### 查看

```bash
./start.sh cli user show alice
```

结果包含用户元数据和当前 CCD 虚拟 IP。

### 创建

```bash
./start.sh cli user add \
  --username alice \
  --group-name 开发组
```

也可以指定组 ID：

```bash
./start.sh cli user add \
  --username alice \
  --group-id <uuid>
```

客户端私钥需要密码时：

```bash
./start.sh cli user add \
  --username alice \
  --group-name 开发组 \
  --password 'client-key-password'
```

这里的 `--password` **只传给 EasyRSA 用于客户端私钥加密，不保存为 NiceVPN 登录密码**。

### 切换用户组

```bash
./start.sh cli user move alice --group-name 运维组
```

切组会按目标组重新处理用户的 VPN 地址 / CCD，并按当前服务逻辑处理在线会话。

### 启用 / 停用

```bash
./start.sh cli user enable alice
./start.sh cli user disable alice
```

停用用户会按当前用户服务逻辑处理在线状态。

### 踢下线

```bash
./start.sh cli user kick alice
```

通过 OpenVPN Management Interface 处理当前在线会话。

### 删除

```bash
./start.sh cli user delete alice
```

删除会执行用户服务的完整删除流程，包括证书 / CRL / OVPN / CCD / 设备绑定等相关清理。

## 设备绑定

查看：

```bash
./start.sh cli user device show alice
```

重置：

```bash
./start.sh cli user device reset alice
```

重置后用户下次连接时可重新建立设备绑定。

## OVPN

查看文件状态：

```bash
./start.sh cli user ovpn show alice
```

导出：

```bash
./start.sh cli user ovpn export alice --output /root/alice.ovpn
```

`--output` 可以是目标文件，也可以是目录。

重建：

```bash
./start.sh cli user ovpn regenerate alice
```

也保留快捷命令：

```bash
./start.sh cli user regenerate-ovpn alice
```

导出的 OVPN 包含客户端私钥，CLI 会尽量将目标文件权限设置为 `0600`。

## 防火墙导入

CLI 当前提供中心防火墙的添加 / 导入入口：

```bash
./start.sh cli add-firewall \
  --owner-type group \
  --group-name 开发组 \
  --iptables-line 'iptables -A INPUT -p tcp --dport 443 -j ACCEPT'
```

从文件导入：

```bash
./start.sh cli add-firewall \
  --owner-type group \
  --group-name 开发组 \
  --iptables-file ./rules.txt
```

用户归属规则：

```bash
./start.sh cli add-firewall \
  --owner-type user \
  --username alice \
  --iptables-file ./rules.txt
```

组规则可用 `--owner-id` 或 `--group-name`，用户规则使用 `--username`。

## 管理后台密码哈希

交互生成 Argon2id 管理密码哈希：

```bash
./start.sh cli hash-admin-password
```

## 旧命令兼容

以下平铺命令为了兼容历史脚本仍然保留：

```bash
./start.sh cli add-group --name 开发组 --subnet 10.224.1.0/24
./start.sh cli add-user --username alice --group-name 开发组
```

新脚本建议使用分层命令：

```text
group ...
user ...
add-firewall ...
```

## Bash Tab 补全

仓库提供：

```text
deploy/shell/install-ovpn-cli-symlink.sh
```

安装固定命令：

```bash
bash deploy/shell/install-ovpn-cli-symlink.sh
```

默认创建：

```text
/usr/local/bin/ovpn-cli -> <项目目录>/cli.py
```

然后在当前 shell 或 `~/.bashrc` 启用 argcomplete：

```bash
eval "$(register-python-argcomplete /usr/local/bin/ovpn-cli)"
```

之后可以使用：

```bash
ovpn-cli user <TAB><TAB>
ovpn-cli group <TAB><TAB>
ovpn-cli add-firewall --iptables-file <TAB>
```

`requirements.txt` 已包含 argcomplete。若 Python 环境不同，应确保 `register-python-argcomplete` 和 `ovpn-cli` 使用同一个 Python 环境。

## 自动化建议

脚本调用建议：

- 读取类操作使用 `--json`；
- 修改前先运行同一条命令加 `--dry-run`；
- 不要解析普通表格输出；
- OVPN 文件视为敏感凭据，不写入普通共享目录或日志；
- systemd 部署环境优先通过 `./start.sh cli ...`，确保使用与 NiceVPN 服务一致的 Python 环境。

## 查看实时帮助

本文只描述稳定 CLI 契约。具体参数以当前代码为准：

```bash
./start.sh cli --help
./start.sh cli group --help
./start.sh cli user --help
./start.sh cli user ovpn --help
./start.sh cli add-firewall --help
```
