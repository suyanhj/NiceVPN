# 设计说明 — vpn-peer-manual-page-2026-04-28

## 1. 路由与导航

| 项 | 说明 |
|----|------|
| URL | `/peers/manual?peer=<uuid>` |
| 注册位置 | `main.py`：`@ui.page("/peers/manual")` |
| 布局 | 与其它功能页一致：`_ensure_initialized()` → `_nav_layout("对端部署说明", "/peers")`，侧栏「对端站点」仍为激活态 |

**无效 peer**：若无对应 JSON，页面展示空态提示与「返回对端站点」按钮。

## 2. UI 结构（`PeersPage.render_manual_page`）

1. **顶栏**：标题、副文案、Chip（对端名、绑定用户、全局 VPN 池）。
2. **操作**：返回 `/peers`、`复制全部命令`、`下载 Markdown`（字节 `utf-8`）。
3. **双列网格**（小屏单列）：左侧 **关键提醒**（短时列表）；右侧 **元信息**（对端 ID、默认 `client.conf` 路径、日志路径、LAN CIDR）。
4. **步骤卡片**：每步含标题、摘要、`bash` 代码块；代码块头 **复制** 按钮。

样式类名前缀 **`peer-manual-*`**，定义于 `app/ui/theme.py`（与旧 **`.peer-manual-preview`** 侧栏样式并存，侧栏仍可用于其它场景如远程日志若复用类名需留意）。

## 3. 数据与生成

| 来源 | 说明 |
|------|------|
| `PeerService.export_peer_manual_context(peer_id)` | 聚合 `load_config().global_subnet` 与对端行，调用 `build_peer_site_manual_context` |
| `PeerService.export_peer_manual_markdown(peer_id)` | 下载用；与结构化内容 **同一实现源**，避免漂移 |
| `build_peer_site_manual_context` | `app/services/peer_instance/peer_manual_md.py`：返回 `overview`、`highlights`、`steps`（每步 `command` 字符串）、`commands` 扁平列表 |

**复制「全部命令」**：`"\n\n".join(ctx["commands"])`。

## 4. 安全与剪贴板

使用 `app/ui/copy_clipboard.copy_text_to_clipboard`：**仅在按钮 `on_click`** 调用，以满足浏览器剪贴板用户手势约束；局域网 HTTP 下依赖模块内 **`textarea` 回退**。

## 5. 测试

单测：`tests/unit/test_peer_manual_md.py` 覆盖 Markdown  essentials 与 **`build_peer_site_manual_context`** 命令片段（示例：不含 `-i tun0`）。

## 6. 与 mesh 文档对齐

更新 **`vpn-peer-instance-mesh-2026-04-13`** 的 **`tasks.md` / `design.md` / `proposal.md`**：移除 **`peer_ssh_tun_interface`**、**`firewall_source_group_id`**、对端 **`FORWARD -i tun0`** 等过时描述；指向本条 **独立页** 取代「弹窗预览」表述。
