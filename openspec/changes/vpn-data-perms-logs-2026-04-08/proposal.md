# vpn-data-perms-logs-2026-04-08

> **迁移提示（2026-04-09）**：下文若出现 **`data/` 下 CCD、设备绑定、ipp** 等路径，已与当前代码不一致。权威布局见 **`openspec/changes/vpn-lan-firewall-devicebind-2026-04-09`** 与 **`app/core/constants.py`**（OpenVPN 运行时以 **`/etc/openvpn`** 为主树，`.ovpn` 在 **`mgmt/ovpn`**，用户 JSON 仍在 **`data/users`**）。保留本文仅作历史背景。

## 概述

将「Linux 上 root 管理进程与 nobody OpenVPN 进程」的 data 目录权限策略、client-connect 设备绑定脚本行为，以及**日志与数据文件存放位置**纳入 OpenSpec，与已实现代码对齐。

## 背景与动机

- OpenVPN 以 `user nobody` / `group nobody` 运行，管理端以 root 写 `data/`，曾出现 CCD、设备绑定 JSON、脚本日志等 **Permission denied**。
- 设备绑定脚本依赖 `auth-user-pass` 的旧逻辑与已移除客户端密码提示的决策冲突，需改为 `push-peer-info`（如 `IV_HWADDR`）或允许无指纹时仅证书通过。
- 守护进程日志原在 `/var/log/openvpn-*`，与管理端 `data/logs` 分裂，不利于备份与权限管理。

## 范围

### 1. POSIX 权限统一（`app/utils/posix_data_perms.py`）

- `data/ccd`、`data/device_bindings`：目录 `2775` + `root:nobody` 主组；**文件**属主 `nobody`、模式 `660`，便于脚本 `sed -i` 与 root 管理端写入后修正。
- `data/ipp.txt`：`root:nobody` 组 + `664`，供 nobody 更新地址池。
- 每次 `_ensure_data_dirs()` 后执行 `apply_openvpn_shared_data_permissions`；CCD/绑定 JSON 写入后调用 `fix_path_for_openvpn_shared_data`。
- `config.json` 保存后 `chmod 600`（仅 root 可读）。

### 2. 设备绑定脚本（`app/scripts/device-bind.sh`）

- 部署时替换 `__VPN_DATA_DIR__` 为实际 `DATA_DIR`；换行强制 LF（`init_wizard` 写入）。
- 指纹来源：`IV_HWADDR`；无指纹时记录日志并 `exit 0`（仅证书认证）。
- 日志路径：`${DATA_DIR}/logs/openvpn-device-bind.log`（与 `DEVICE_BIND_LOG_FILE` 一致），避免 `/var/log` 与 nobody 冲突。
- `ensure_device_bind_log_file()`：创建日志文件并 `chown nobody` + `660`。

### 3. 日志与路径归集（`app/core/constants.py` 等）

- 约定：`LOGS_DIR = data/logs`；`OPENVPN_DAEMON_LOG_DIR = data/logs/openvpn`；`DEVICE_BIND_LOG_FILE = data/logs/openvpn-device-bind.log`。
- `generate_server_conf`：`status` / `log-append` 指向 `{data_dir}/logs/openvpn/{实例名}(-status).log`，不再使用 `/var/log/openvpn-*`。
- `get_status`：优先新路径，若不存在则回退旧 `/var/log/openvpn-{name}-status.log`。
- 服务管理页「查看日志」：配置解析失败时的默认路径改为 `data/logs/openvpn/{name}.log`，并回退旧 `/var/log`。
- `config._ensure_data_dirs`：确保创建 `OPENVPN_DAEMON_LOG_DIR`。
- `logging_setup.py` 模块注释说明与 `constants` 的布局关系。

## 非目标

- 不改变审计目录 `data/audit`、告警文件 `data/alerts.json` 的语义与格式。
- 不迁移历史 `/var/log` 下已有日志文件（由运维按需拷贝）；代码仅做路径兼容回退。
