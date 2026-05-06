# 设计说明 — vpn-pki-jsonlock-import-2026-04-09

## 1. 删除用户与 CRL

- **连接拒绝**：依赖服务端 `crl-verify` 与更新后的 `crl.pem`；与是否删除 `issued` 下文件无直接关系。
- **流程顺序**：
  1. `EasyRSAWrapper.revoke(username)`：`EasyRSAError` 仅记录 `warning`（可能已吊销或缺 `issued` 证书）。
  2. `EasyRSAWrapper.gen_crl()`：失败则 `logger.exception` 并 **`raise RuntimeError`**，**中止**删除流程（避免在无新 CRL 时继续删用户造成状态不一致）。
  3. `sync_openvpn_runtime_permissions_from_config()`。
  4. `get_cert_info(username)`：
     - 若 **`None`（查无该 CN）** 或 **`status == revoked`**：调用 `_remove_user_pki_disk_files`，序列号优先 `cert_info["serial"]`，否则用户 JSON `cert_serial`。
     - 若仍为 **valid**：**不删** PKI 私钥等，打 `error` 日志要求人工排查吊销。

## 2. `_remove_user_pki_disk_files` 路径与条件

### 2.1 按 CN（用户名）

- `pki/private/{username}.key`
- `pki/reqs/{username}.req`
- `pki/issued/{username}.crt`（若 `revoke` 未移走则删除）
- `pki/tc2-clients/{username}.key`

### 2.2 按证书序列号（须传入 `cert_serial`）

对每个 `(目录, 扩展名元组)`，对 `serial` 与 `serial.upper()` 各尝试：

| 目录 | 扩展名 |
|------|--------|
| `pki/certs_by_serial` | `.crt`, `.pem` |
| `pki/revoked/certs_by_serial` | `.crt`, `.pem` |
| `pki/revoked/private_by_serial` | `.key`, `.pem` |
| `pki/revoked/reqs_by_serial` | `.req` |
| `pki/revoked/certs`（旧布局） | `.crt`, `.pem` |

## 3. `file_lock`（POSIX）

- 打开 `str(resolve(path)) + ".lock"`，`flock(LOCK_EX)` → `yield` → `LOCK_UN` → `close`。
- **新增**：`close` 后对 `lock_path` 执行 `os.unlink`（`OSError` 忽略），避免空锁文件长期堆积。
- **副作用消除**：全目录扫描类调用（如按用户名查找绑定文件）不再为每个 JSON 留下旁路 `.lock`。

## 4. 批量导入上传（NiceGUI 3）

- `ui.upload(..., on_upload=handle_upload)` 回调参数为 `UploadEventArguments`，正文在 **`e.file: FileUpload`**。
- 使用 **`await e.file.read()`** 取 `bytes`，再 `decode("utf-8", errors="replace")`，写入 `textarea.set_value`。
- 异常路径：`logger.exception`、`ui.notify`、`raise RuntimeError`（交由 NiceGUI 全局异常处理）。

## 5. 运维提示

- 升级前已产生的空 `*.json.lock` 可一次性手动删除；新逻辑不再新增持久化锁文件（POSIX）。
- PKI 若使用非标准序列号文件名，需在运维侧核对 `index.txt` 与磁盘文件名是否一致。
