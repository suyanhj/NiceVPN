# NiceVPN 配置与管理后台认证

## 两类配置（字段归属互不重叠）

| 文件 | 维护者 | 职责 |
| --- | --- | --- |
| 根目录 `config.example.yml` | Git | 部署参数默认模板，带注释 |
| 根目录 `config.yml` | 管理员 | 部署参数，只读：登录开关、用户名、Session/重连设置及一次性初始密码 |
| `data/config.json` | NiceVPN | 初始化状态、VPN/PKI、实例、路由、域名、SSO/绑定、通知、SSH 配置和管理员密码哈希等长期持久化设置 |
| `data/nicevpn.db` | NiceVPN | 用户、组、标签、证书关联、对端、防火墙等业务结构化数据 |
| `data/admin_session_secret` | NiceVPN | 管理端 Session 签名密钥 |

系统设置和初始化向导只保存 `data/config.json`；`config.yml` 不由程序反复重写，所以注释、顺序等保留。无关联查询需求的单份全局设置不引入额外 SQLite 配置表。

### 新环境

```bash
cp config.example.yml config.yml
chmod 600 config.yml
./start.sh
```

未复制模板也可启动：没有文件时使用内置默认值，之后由系统初始化向导生成 `data/config.json`。运行数据目录由程序自动创建。

### 旧环境自动拆分

旧版只有 `data/config.json` 时，首次启动会自动提取管理员登录参数到根目录 `config.yml`，将其余配置保留在 `data/config.json`，不重新初始化 VPN。两份写入均采用原子替换，原有运行参数不丢失。

已有 `config.yml` 时，它只允许部署字段；不要从旧 PR #111 的“全量 YAML 模板”复制 VPN/运行参数到此文件，运行参数请通过 Web 管理端修改。

## 管理端登录（8880）

`config.yml`：

```yaml
admin_login_enabled: true
admin_username: admin
admin_session_lifetime_minutes: 480
admin_reconnect_timeout_seconds: 300

# 只在第一次没有密码哈希时使用
admin_password: null
```

管理员密码**永久哈希**保存在 `data/config.json` 的 `admin_password_hash` 字段中（Argon2id），不放在 YAML 中。

初次开启登录时，有两种方式：

1. 在 YAML 中临时写 `admin_password: "自行设置的强密码"`；启动时将其哈希持久化到 JSON。**确认首次启动成功后，手工删除 YAML 中的明文密码**。该值仅用于首次初始化，不会在密码重置后覆盖新密码。
2. 不填 `admin_password`：启动会自动生成本机独有的随机初始密码，仅保存在 `data/admin_initial_password`，权限 0600。可通过 `sudo cat data/admin_initial_password` 查看，随后使用 CLI 重置。

```bash
ovpn-cli admin password reset                       # 无参数：安全交互输入新密码
ovpn-cli admin password reset admin 'YourNewPassword' # 指定用户名和新密码
ovpn-cli admin password reset admin                 # 为该管理员随机生成密码，终端只显示一次
# 未安装 ovpn-cli 时也可使用 ./start.sh cli admin password reset ...
```

NiceVPN 目前仅支持单管理员：命令给出的用户名必须与根目录 `config.yml` 的 `admin_username` 一致，账号更名请修改 YAML。CLI 只修改 `data/config.json` 中的密码哈希，不改 `config.yml`；如尚未启用登录，请先设置 `admin_login_enabled: true`。重置成功后清理初始密码文件及旧 Session 密钥；**重启 NiceVPN** 后新密码生效，旧会话不能沿用。

注意：明文密码作为命令行参数会暴露在 shell 历史与进程列表中。正式环境建议使用无参数交互模式；随机密码只显示一次。

恢复旧备份或升级时，旧版 `admin_username` + `admin_password_hash` 的有效组合会保留其登录开启状态。8881 公网 VPN API 仍采用独立认证。

## 备份与 Docker

完整备份会保存运行配置快照 `app/config.json` 和独立部署配置 `app/config.yml`；备份格式继续兼容旧版（无 YAML）归档，历史备份恢复时会自动提取部署参数。

部署时应同时保留 `config.yml`、`data/`、`backups/`。容器使用绑定挂载 `/app/config.yml` 与 `/app/data`，不要将真实配置打入镜像，`.dockerignore` 已排除根目录实际配置。
