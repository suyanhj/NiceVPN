# 接口契约：钉钉机器人通知

**功能分支**: `001-openvpn-mgmt-system`
**生成日期**: 2026-03-27
**类型**: 出站 HTTP 调用（系统 → 钉钉 Webhook）

---

## 用途范围

根据宪法原则四，钉钉机器人通知**仅用于分发限时 `.ovpn` 下载链接**，
不得作为系统内告警（服务宕机、证书到期等）的替代通道。

---

## 出站请求规范

### `POST {dingtalk_webhook}`

钉钉机器人接收标准 Webhook POST 请求。

#### 请求头

```
Content-Type: application/json
```

#### 请求体（文本消息类型）

```json
{
  "msgtype": "text",
  "text": {
    "content": "【OpenVPN 配置文件下载】\n用户：{username}\n下载链接（1小时内有效，仅限下载一次）：\n{download_url}"
  }
}
```

**字段说明**:

| 字段 | 说明 |
|---|---|
| `username` | VPN 用户名，用于接收者识别 |
| `download_url` | 完整一次性下载 URL，格式：`{download_base_url}/download/{token}` |

#### 响应

钉钉 Webhook 正常响应：

```json
{"errcode": 0, "errmsg": "ok"}
```

任何 `errcode != 0` 或网络异常均视为推送失败。

---

## 失败处理策略

根据 spec.md 边界情况：推送失败**不影响**配置文件的生成和下载链接的有效性。

```python
# services/notify/dingtalk.py 行为约束
def send_download_link(username: str, download_url: str) -> bool:
    """
    发送下载链接至钉钉群。
    返回 True 表示推送成功，False 表示失败。
    任何异常（网络超时、API 错误）均被捕获并记录到审计日志，
    不向调用方抛出异常，不阻塞主流程。
    超时设置：requests 调用超时 10 秒。
    """
```

推送失败时，系统在 NiceGUI UI 中显示提示："钉钉推送失败，请手动将以下链接发送给用户：{download_url}"

---

## 配置

| 配置项 | 位置 | 说明 |
|---|---|---|
| `dingtalk_webhook` | `data/config.json` | 钉钉机器人 Webhook URL（可选，空则跳过推送） |

若 `dingtalk_webhook` 为 null 或空字符串，跳过推送步骤，仅在 UI 中展示下载链接。
