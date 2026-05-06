# 设计方案

## 权限模型

- 与 `instance.py` 中 `user nobody` / `group nobody` 对齐；`pwd.getpwnam("nobody").pw_gid` 作为目录 setgid 的属组，避免发行版上 `nogroup` / `nobody` 组名差异。
- 管理端以 root 写 JSON/CCD 后必须将**文件**修正为属主 nobody，否则 client-connect 脚本无法更新绑定记录。

## 设备绑定脚本

- 脚本内 `DATA_DIR` 由部署步骤字符串替换，与 Python `DATA_DIR` 一致。
- `mkdir -p "${BINDINGS_DIR}"` 在指纹分支执行；目录权限依赖管理端预先 `apply_openvpn_shared_data_permissions`。

## 日志目录结构

```
data/
  logs/
    app.log                    # 管理端 RotatingFileHandler
    openvpn-install.log        # 安装器 / 初始化相关
    openvpn-device-bind.log    # client-connect 脚本（nobody 写）
    openvpn/
      {instance}.log           # log-append
      {instance}-status.log    # status
  audit/                       # 审计 JSONL（独立）
```

- OpenVPN 在 `user/group` 指令之前打开 `status`/`log-append` 描述的文件描述符，日志落在 `data` 下仍由 root 启动阶段打开，行为与原先 `/var/log` 一致。

## 关键文件

| 模块 | 职责 |
|------|------|
| `app/core/constants.py` | `LOGS_DIR`、`OPENVPN_DAEMON_LOG_DIR`、`DEVICE_BIND_LOG_FILE` |
| `app/core/config.py` | `_ensure_data_dirs` + `apply_openvpn_shared_data_permissions`；`save_config` chmod 600 |
| `app/services/openvpn/instance.py` | 生成 `status`/`log-append` 路径；`get_status` 双路径解析 |
| `app/ui/pages/services.py` | 日志查看默认路径与旧路径回退 |
| `app/utils/posix_data_perms.py` | 权限应用与设备绑定日志保障 |
| `app/core/init_wizard.py` | 部署 `device-bind.sh`、替换 `__VPN_DATA_DIR__`、LF、调用权限与日志保障 |
