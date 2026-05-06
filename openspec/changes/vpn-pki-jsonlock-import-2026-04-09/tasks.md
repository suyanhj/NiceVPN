# 任务清单 — vpn-pki-jsonlock-import-2026-04-09

> 状态：**已完成**（与仓库当前实现一致，供归档与审计）

## 用户删除与 PKI

- [x] `UserService.delete`：`revoke` 与 `gen_crl` 分离；`gen_crl` 失败中止并抛错  
- [x] 按 `get_cert_info` 决定是否调用 `_remove_user_pki_disk_files`  
- [x] CN 路径：`private`、`reqs`、`issued`、`tc2-clients`  
- [x] 序列号路径：`certs_by_serial`、`revoked/certs_by_serial`、`revoked/private_by_serial`、`revoked/reqs_by_serial`、`revoked/certs`  

## 文件锁

- [x] `file_lock`：POSIX 释放锁后 `unlink` `.lock`  
- [x] `write_json_atomic` 注释乱码修复（UTF-8）  

## 批量导入

- [x] `users.py`：`on_upload` 使用 `await e.file.read()` 与 UTF-8 解码填入 `textarea`  
- [x] 成功/失败通知与日志  

## OpenSpec

- [x] 本 change：`proposal.md`、`design.md`、`tasks.md`、`.openspec.yaml`  
