# vpn-notify-plugins-wework-2026-04-29

## 概述

将以下**已实现、此前未单独写入 OpenSpec** 的通知相关能力纳入变更记录，便于归档与审计：

1. **通知插件注册表**：`register_notify_sender` + 内置插件导入注册；统一入口 **`send_download_link`**。
2. **钉钉**：下载链接推送迁至 **`plugins/dingtalk_notify`**；`dingtalk.py` 保留 **`send_dingtalk_text`**（设置页测试与插件共用）。
3. **配置**：**`notify_enabled`**、**`notify_provider`**（`none` / `dingtalk` / `wework`）；旧 JSON 仅有 **`dingtalk_webhook`** 且无 `notify_*` 时迁移为启用钉钉。
4. **企业微信**：**`wework_webhook`**；发送委托 **`wechat-work-webhook`**（`wechat_work_webhook.connect(url).text(...)`）。
5. **设置页**：按所选通道显隐钉钉 / 企业微信参数块；**`bind_visibility_from(..., "value", …)`** 绑定下拉 **`value`**（避免默认绑 `visible` 导致永远不显示）。
6. **审计**：未启用或未选通道记 **`notify_push`**；钉钉成功/失败仍记 **`dingtalk_push`**；企业微信记 **`wework_push`**。
7. **用户列表**：推送调用 **`from app.services.notify import send_download_link`**。

## 背景与动机

- 多通道（钉钉、企业微信及后续 TG 等）需统一分发与扩展点，避免 UI 与业务硬编码单一厂商。
- 企业微信机器人与钉钉同为 Webhook JSON，宜用成熟小包减少手写 HTTP。
- NiceGUI `bind_visibility_from` 默认 `target_name='visible'`，误用会导致「选了钉钉仍无表单」。

## 范围（已实现）

| 领域 | 主要路径 |
|------|-----------|
| 注册表与入口 | `app/services/notify/registry.py`、`app/services/notify/__init__.py` |
| 钉钉插件 | `app/services/notify/plugins/dingtalk_notify.py`、`app/services/notify/dingtalk.py` |
| 企业微信 | `app/services/notify/wework.py`、`app/services/notify/plugins/wework_notify.py` |
| 配置模型 | `app/models/config.py`（`notify_enabled`、`notify_provider`、`wework_webhook`、迁移 validator） |
| 配置持久化 | `app/core/config.py`（`wework_webhook` 归入可选字符串归一化） |
| UI | `app/ui/pages/settings.py`（通知 Tab）、`app/ui/pages/users.py`（推送下载链接） |
| 依赖 | `requirements.txt`：`wechat-work-webhook>=0.0.2` |
| 测试 | `tests/unit/test_notify_registry.py`、`tests/unit/test_wework_webhook.py` |

## 非目标

- 不在此变更内定义 Telegram、Slack 等通道实现（仅保留注册机制便于后续加插件）。
- 不修改钉钉 **`send_dingtalk_text`** 与 **`dingtalkchatbot`** 依赖策略（仍见 **`vpn-cli-dingtalk-download-models-2026-04-10`**）。

## 与旧 OpenSpec 的交叉引用

- **`vpn-cli-dingtalk-download-models-2026-04-10`**：钉钉 **加签、`DingtalkChatbot`、测试消息** 等仍以该变更为准；本变更在其之上将 **下载链接推送** 收束为插件 + 统一 **`send_download_link`**。
