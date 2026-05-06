# 接口契约：一次性配置文件下载 API

**功能分支**: `001-openvpn-mgmt-system`
**生成日期**: 2026-03-27
**类型**: HTTP REST 端点（FastAPI 路由，挂载于 NiceGUI 同一进程）

---

## 端点：下载 .ovpn 配置文件

### `GET /download/{token}`

管理员生成下载链接后，终端用户通过此端点下载其专属 `.ovpn` 配置文件。
链接为一次性且在 1 小时内过期。

#### 路径参数

| 参数 | 类型 | 说明 |
|---|---|---|
| `token` | string | 32 字节 URL-safe 随机令牌（由 `secrets.token_urlsafe(32)` 生成） |

#### 响应

**成功（200 OK）**

```
HTTP/1.1 200 OK
Content-Type: application/octet-stream
Content-Disposition: attachment; filename="{username}.ovpn"
Content-Length: <file_size>

<.ovpn 文件二进制内容>
```

服务端在返回文件前，先将对应令牌记录的 `used` 字段置为 `true` 并写入磁盘，
然后以流式方式返回文件内容。

**失败响应**

| HTTP 状态码 | 原因 | 响应体（JSON） |
|---|---|---|
| 404 Not Found | 令牌不存在 | `{"detail": "下载链接无效或已过期"}` |
| 410 Gone | 令牌已被使用（`used == true`） | `{"detail": "下载链接已被使用，每个链接仅限下载一次"}` |
| 410 Gone | 令牌已过期（`expires_at < now()`） | `{"detail": "下载链接已过期，请联系管理员重新生成"}` |
| 500 Internal Server Error | 文件不存在或读取失败 | `{"detail": "文件读取失败，请联系管理员"}` |

#### 行为约束

1. **原子性**：`used = true` 写入磁盘操作必须在文件流开始传输前完成。
2. **不可重试**：任何已消费的令牌，即使文件传输中断，也不可重新下载。
3. **不需要认证**：端点为公开访问（通过令牌的不可预测性保证安全）。
4. **不记录 IP**：下载行为记录于审计日志，但不存储请求方 IP（隐私考量）。

#### 审计日志记录

每次下载请求（无论成功与否）均写入一条审计日志：

```json
{
  "action": "download_ovpn",
  "target_type": "download_link",
  "target_id": "{token}",
  "detail": {
    "username": "{username}",
    "result_status": "success | expired | already_used | not_found"
  },
  "result": "success | failure"
}
```

---

## 内部接口：链接生成（服务层，非 HTTP）

此操作由管理员通过 NiceGUI 界面触发，不暴露为独立 HTTP 端点。

### 调用方：`services/download/link_mgr.py`

```python
def create_download_link(username: str) -> str:
    """
    为指定用户生成一次性 .ovpn 下载令牌。
    返回完整下载 URL。
    副作用：
      1. 生成令牌文件 data/download_links/{token}.json
      2. 若 dingtalk_webhook 已配置，调用钉钉推送（可失败，不阻塞返回）
      3. 写入审计日志
    """
```

**输出**: 完整 URL，格式为 `{download_base_url}/download/{token}`
