# 任务列表

## 权限与设备绑定

- [x] 新增 `app/utils/posix_data_perms.py`：`apply_openvpn_shared_data_permissions`、`fix_path_for_openvpn_shared_data`、`ensure_device_bind_log_file`
- [x] `config._ensure_data_dirs` 末尾调用 `apply_openvpn_shared_data_permissions`；`save_config` 对 `config.json` 执行 `chmod 600`
- [x] `UserService` / `DeviceBindingService` 写入 CCD 或 `device_bindings` 后调用 `fix_path_for_openvpn_shared_data`
- [x] `device-bind.sh`：指纹逻辑（`IV_HWADDR`、无指纹放行）、`BIND_LOG` 路径、`__VPN_DATA_DIR__` 占位
- [x] `init_wizard`：部署脚本时 LF、`__VPN_DATA_DIR__` 替换、`chmod 755`、调用权限与 `ensure_device_bind_log_file`

## 日志路径归集

- [x] `constants.py`：`OPENVPN_DAEMON_LOG_DIR`、`DEVICE_BIND_LOG_FILE` 与注释说明
- [x] `config.py`：创建 `OPENVPN_DAEMON_LOG_DIR`；`posix` 中创建 `data/logs/openvpn` 并处理设备绑定日志文件
- [x] `instance.generate_server_conf`：`status` / `log-append` 使用 `{data_dir}/logs/openvpn/...`
- [x] `instance.get_status`：优先 `data/logs/openvpn/{name}-status.log`，回退 `/var/log/openvpn-{name}-status.log`
- [x] `services._show_log_viewer`：默认 `data/logs/openvpn/{name}.log`，不存在时回退 `/var/log/openvpn-{name}.log`
- [x] `logging_setup.py`：模块文档字符串说明与 `constants` 的布局关系
