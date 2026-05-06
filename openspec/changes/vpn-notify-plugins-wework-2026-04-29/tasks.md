# 任务清单 — vpn-notify-plugins-wework-2026-04-29

> 状态：**已完成**（与仓库当前实现一致，供归档与审计）

## 注册表与包结构

- [x] `app/services/notify/registry.py`：`register_notify_sender`、`send_download_link`、`_register_builtin_plugins`
- [x] `app/services/notify/__init__.py`：导出统一入口
- [x] `app/services/notify/plugins/__init__.py` 与插件模块

## 钉钉（插件化）

- [x] `app/services/notify/plugins/dingtalk_notify.py`：注册 `dingtalk`，审计 `dingtalk_push`
- [x] `app/services/notify/dingtalk.py`：仅保留 `send_dingtalk_text`

## 企业微信

- [x] `app/services/notify/wework.py`：`wechat_work_webhook` 封装与 URL 校验
- [x] `app/services/notify/plugins/wework_notify.py`：注册 `wework`，审计 `wework_push`
- [x] `requirements.txt`：`wechat-work-webhook>=0.0.2`

## 配置

- [x] `SystemConfig`：`notify_enabled`、`notify_provider`、`wework_webhook`
- [x] 旧配置迁移：仅 `dingtalk_webhook` 且无 `notify_*` → 启用钉钉
- [x] `app/core/config.py`：可选字段 `wework_webhook`

## UI

- [x] `settings.py`：通知 Tab、按通道显隐、`bind_visibility_from(..., "value", …)`
- [x] `users.py`：`send_download_link` 统一入口

## 测试

- [x] `tests/unit/test_notify_registry.py`
- [x] `tests/unit/test_wework_webhook.py`

## OpenSpec

- [x] 本 change：`proposal.md`、`design.md`、`tasks.md`、`.openspec.yaml`
