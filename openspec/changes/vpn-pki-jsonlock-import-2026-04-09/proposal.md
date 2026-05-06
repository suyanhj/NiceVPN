# vpn-pki-jsonlock-import-2026-04-09

## 概述

记录三项已落地行为：**删除用户时的 EasyRSA/CRL 与 PKI 磁盘清理**、**Linux 下 JSON 文件锁旁路文件不再残留**、**NiceGUI 3.x 批量导入上传后正确填入文本框**。

## 背景与动机

1. **PKI 残留**：`easyrsa revoke` 会更新 `index.txt` 与 CRL，但常不删除 `private/*.key`、`certs_by_serial` 及 `revoked/*_by_serial` 等；删除用户后磁盘仍留客户端密钥与按序列号归档副本，与「已吊销」预期不符。
2. **吊销与 CRL**：原先 `revoke` 与 `gen_crl` 同在一个 `try` 中，若 `revoke` 失败（例如已吊销）可能导致 **未执行 `gen_crl`**；且静默吞掉异常不利于排障。
3. **`*.json.lock` 泛滥**：`file_lock` 在 POSIX 上创建 `<path>.lock` 供 `flock` 使用，释放锁后 **未删除** 该空文件；`DeviceBindingService._find_binding_file` 遍历目录内每个绑定 JSON 并 `read_json`，会 **为每个文件各创建一个 `.lock`**，表现为「删一个用户，其它绑定旁多出锁文件」。
4. **批量导入**：NiceGUI 3 的 `UploadEventArguments` 使用 **`e.file`（异步 `read`）**，旧代码使用不存在的 **`e.content`**，导致上传成功但文本框为空、预检查报「导入内容为空」。

## 范围（已实现）

| 领域 | 文件 | 行为摘要 |
|------|------|----------|
| 用户删除 | `app/services/user/crud.py` | `revoke` / `gen_crl` 分离；`gen_crl` 失败则中止删除并 `RuntimeError`；`get_cert_info` 为已吊销或查无 CN 时调用 `_remove_user_pki_disk_files` |
| PKI 清理 | 同上 | 按 CN：`private`、`reqs`、`issued`、`tc2-clients`；按序列号：`certs_by_serial`、`revoked/certs_by_serial`、`revoked/private_by_serial`、`revoked/reqs_by_serial`、`revoked/certs`（多扩展名与大小写 serial 尝试） |
| 文件锁 | `app/utils/file_lock.py` | POSIX：`flock` 解锁并 `close` 后对 `lock_path` 执行 `unlink`，避免残留 |
| 设备绑定 | `app/services/user/device_bind.py` | 保留 `_unlink_binding_json_and_lock`，兼容删除绑定时清理历史 `.lock` |
| 批量导入 UI | `app/ui/pages/users.py` | `on_upload`：`await e.file.read()` + UTF-8 `decode(..., errors="replace")` + `textarea.set_value`；成功/失败 `notify` + 日志 |

## 非目标

- 不保证序列号文件名与 `index.txt` 在「前导零」等细节上完全一致时的 100% 删净；若遇漏删需对照实际文件名收紧规则。
- Windows 下 `file_lock` 仍为 `threading.Lock`，不产生 `.lock` 文件（行为不变）。

## 与旧 OpenSpec 的交叉引用

- **`vpn-lan-firewall-devicebind-2026-04-09`**：设备绑定目录与全目录扫描逻辑见该条；**锁文件根因与修复**以本条为权威。
- 其它 `vpn-bugfix-2026-04*`：用户 CRUD 早期若仅描述「吊销」未描述 PKI 磁盘清理，**以本条 + 当前 `crud.py` 为准**。
