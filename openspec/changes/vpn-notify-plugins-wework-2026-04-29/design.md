# 设计说明 — vpn-notify-plugins-wework-2026-04-29

## 1. 注册表与统一入口

- **`register_notify_sender(provider_id, fn)`**：`fn` 签名为 `(config, username, download_url) -> bool`；`config` 为 `load_config()` 返回的 Box 或兼容 `dict.get`。
- **`send_download_link(username, url)`**：读 `notify_enabled`、`notify_provider`；未启用 / `none` / 未知通道写 **`notify_push`** 审计并返回 `False`；否则调用已注册实现。
- **`_register_builtin_plugins()`**：在 **`registry`** 模块加载末尾 `import` **`dingtalk_notify`**、**`wework_notify`**，保证任意从 `registry` 或 **`app.services.notify`** 导入入口时插件已注册。

## 2. 钉钉插件

- **`plugins/dingtalk_notify.py`**：读取 `dingtalk_webhook`、`dingtalk_secret`；调用 **`send_dingtalk_text`**；审计动作 **`dingtalk_push`**。
- **`dingtalk.py`**：仅 **`send_dingtalk_text`**（`DingtalkChatbot`），供设置页「测试」与插件共用。

## 3. 企业微信插件

- **`wework.py`**：URL 校验（须含 `qyapi.weixin.qq.com` 与 `/cgi-bin/webhook/send`）；**`wechat_work_webhook.connect(url).text(content)`** 返回接口 JSON。
- **`plugins/wework_notify.py`**：读 **`wework_webhook`**；**`errcode == 0`** 为成功；审计 **`wework_push`**；捕获 **`ValueError`** 与 **`requests.RequestException`** 等。

## 4. 配置与迁移

- **`notify_provider`**：`Literal["none", "dingtalk", "wework"]`。
- **`@model_validator(mode="before")`**：若输入 dict 中 **无** `notify_enabled` **且** 无 `notify_provider`，但存在非空 **`dingtalk_webhook`**，则补 **`notify_enabled=True`**、**`notify_provider="dingtalk"`**（兼容升级前配置）。

## 5. 设置页 UI

- **通道** 下拉：`none` / `dingtalk` / `wework`。
- **钉钉**：独立 **`ui.column`**，**`bind_visibility_from(notify_provider_select, "value", value="dingtalk")`**；含 Webhook、SEC、测试按钮。
- **企业微信**：独立 **`ui.column`**，**`value="wework"`**；含 Webhook、测试按钮。
- **保存**：「保存通知配置」按钮始终可见；持久化 `notify_enabled`、`notify_provider`、`dingtalk_webhook`、`dingtalk_secret`、`wework_webhook`。

## 6. 用户列表

- **推送下载链接**：**`app.services.notify.send_download_link`**；成功/失败文案引导检查「系统设置 → 通知」。
