# 设计说明 — vpn-public-api-http-2026-05-06

## 1. 路由与认证

- **挂载**：NiceGUI `app` 上 `include_router(vpn_ops.router, prefix="/api")`，`router.prefix="/vpn"` → 业务路径为 **`/api/vpn/...`**。
- **Basic**：`HTTPBasic(auto_error=False)`；密码与用户名比对 `secrets.compare_digest`；失败 `401` / 凭据未就绪 `503`。
- **初始化门禁**：未 `load_config().initialized` 时业务接口 **`503`**。
- **下载**：`GET /download/{token}` 由 **`download.py`** 注册，**不要求** Basic。

## 2. 创建（单次）

- **入参**：`CreateVpnUserBody`：`username`（前缀）、`group_name`（可选，按显示名解析组）、`count`（默认 1，1～500）。
- **展开名**：`count==1` 仅 `username`；否则 `username`、`username_1` … `username_{count-1}`（与控制台批量一致）。
- **幂等**：计划列表中任一名称已存在（非 `deleted`）→ **不创建**、不生成链接、`created=false`、`message` 说明；**不要求**此时配置 `download_base_url`。
- **新建**：解析 `download_base_url`（或请求推断）；逐名 `UserService.create`；单文件走 `create_link(ovpn)`，多文件 `build_ovpn_zip(entries, prefix)` + `create_link(..., download_filename=...)`。

## 3. 创建（批量）

- **入参**：JSON **数组**，元素形状同单次；长度 1～64；**`count` 之和** ≤ 2000（超出 `400`）。
- **处理顺序**：按数组逐项；某项因已存在整段跳过 → 记入 `skip_messages`，继续下一项。
- **产物**：若有任意新建 → **唯一** zip，前缀 **`vpns`**（`build_ovpn_zip(..., "vpns")` → `vpns_YYYYMMDD_HHMMSS.zip`）；`create_link` 的 `username` 字段存 **`vpns`** 以区分令牌归属。
- **响应**：`CreateVpnUserResponse`；`usernames` = 全部条目按序展开后的**计划**列表；`message` 可拼接跳过说明（`；`）；全无新建 → `created=false`，`download_url=null`。

## 4. 删除与重置绑定

- **删除**：路径参数为前缀；`_usernames_matching_prefix` 列出匹配活跃用户；无匹配 **`200`**，`deleted=[]`，`message` 说明（与「必须 404」的重置接口区分见 `api.md`）。
- **重置设备绑定**：无匹配用户 **`404`**；否则按用户清除绑定并返回 `bindings_cleared`。

## 5. 审计与日志

- **`AuditLogger`**：`api_create_vpn_user` / `api_create_vpn_user_batch` 等事件；失败前打 **`logger.exception` 或 `error`**（与本项目错误处理约定一致）。

## 6. 相关 UI / 体验调整（同期代码）

- **初始化向导**：`app/ui/pages/init_page.py` 接入规划表单含 `push_lan_routes`；`app/ui/theme.py` 中 `.setup-shell` / `.setup-panel` 视口与滚动，避免右栏截断。
- **服务管理页**：本机 `systemctl` 异步任务内 **`ui.notify` 需绑定 client**（`outbox.enqueue_message` / `safe_invoke`），避免 NiceGUI 空 slot 报错（实现以 `services.py` 为准）。

## 7. 对外说明

- **`api.md`**：请求/响应表、curl 示例（含 batch 数组体）。
- **`README.md`**：入口链接至 `api.md`。
