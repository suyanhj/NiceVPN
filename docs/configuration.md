# NiceVPN 配置与后台认证

## 配置文件位置

- 可复制模板：项目根目录 `config.example.yml`（带默认值，Git 跟踪）
- 实际配置：项目根目录 `config.yml`（不进 Git；建议权限 `600`）
- 运行数据：`data/` 启动时自动按需创建，保存 SQLite、会话密钥、审计信息等，不再存系统运行配置。

新部署：

```bash
cp config.example.yml config.yml
chmod 600 config.yml
./start.sh
```

不复制模板也会使用 `SystemConfig` 的默认值；首次通过系统向导保存后生成 `config.yml`。系统设置页写回时会规范化并重写 YAML，因此注释保留在模板里。

**旧环境：**如果 `data/config.json` 已存在且项目根目录没有 `config.yml`，启动时自动验证、写入根目录 YAML，并保留原 JSON 不删除。若根目录放了未初始化模板而旧 JSON 已初始化，会拒绝启动并提示移走模板，防止覆盖既有 VPN、SSO、服务端设置。

## 管理后台登录（8880）

配置模板默认关闭登录：

```yaml
admin_login_enabled: false
admin_username: admin
admin_password: null
admin_password_hash: null
admin_session_lifetime_minutes: 480
admin_reconnect_timeout_seconds: 300
```

开启登录，只需要改：

```yaml
admin_login_enabled: true
admin_username: admin
admin_password: "自行设置的强密码"
```

启动时将明文密码转成 Argon2id 并写回 `admin_password_hash`，同时清空 `admin_password`。以后不要同时填写两项。

若开启但没有设置任何密码，程序将自动生成一份**机器专属随机初始密码**，哈希保存在 `config.yml`，明文只存在 `data/admin_initial_password`（仅 root/当前服务用户可读）。查看后使用 CLI 重置：

```bash
sudo cat data/admin_initial_password
ovpn-cli admin password reset
# 或 ./start.sh cli admin password reset
```

重置成功后自动删除初始密码文件。CLI 会在终端交互输入并确认新密码，只保存哈希。若需要手动指定哈希，还可使用 `ovpn-cli admin password hash`。

修改认证配置或 CLI 重置密码后重启 `openvpn-mgmt` 生效。单机管理员认证不影响 OpenVPN 客户端连接和公网 API 的独立 Basic/SSO 验证。

## 原配置兼容及完整备份

老版本通过 `admin_username` 与 `admin_password_hash` 同时存在启用登录；迁移后继续兼容。设置 `admin_login_enabled: false` 可以明确关闭后台登录。

完整备份包里的配置快照继续使用 `app/config.json` 这一**备份交换格式**，兼容已有备份；恢复时会写入根目录 `config.yml`，而不是再次写入旧 `data/config.json`。如通过 Docker 部署，建议挂载 `config.yml` 到容器 `/app/config.yml`，不要把私有配置打进镜像（`.dockerignore` 已排除该文件）。
