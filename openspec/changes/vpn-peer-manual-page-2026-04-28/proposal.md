# 对端部署说明独立页 — vpn-peer-manual-page-2026-04-28

## 概述

将「对端部署说明」从 **侧边全高对话框 + Markdown 预览** 改为 **独立浏览器子页**，以 runbook 形式呈现：**概览、关键提醒、执行步骤卡片、可复制命令块**，并保留 **下载 `.md`**。与 **`vpn-peer-instance-mesh-2026-04-13`** 中的对端 SSH/iptables 能力正交：本条仅 **展示与导出**，不新增对端下发逻辑。

## 背景

- **（历史上）**曾用侧边弹窗预览 Markdown，宽度有限，长命令与多步骤阅读体验差。
- Markdown 全文偏长时 **重点不突出**，运维需要 **一键复制** 示例命令。
- 实现上应避免 **从 Markdown 反解析**：由 `peer_manual_md` 产出 **结构化上下文**（`build_peer_site_manual_context`），UI 与下载文本 **同源**。

## 目标

1. 注册路由 **`GET /peers/manual?peer=<peer_id>`**，导航仍归属「对端站点」高亮。
2. 对端列表 **部署说明** 入口跳转到上述子页。
3. 页面：**返回对端列表**、**复制全部命令**、**下载 Markdown**、每步命令 **复制**（`copy_text_to_clipboard`，须在用户手势内调用）。
4. 压缩 **`export_peer_manual_markdown`** 正文；与结构化步骤一致。

## 非目标

- 不改动对端 iptables/OpenVPN **业务下发**语义（见 `vpn-peer-instance-mesh` / `remote_peer_iptables`）。
- 不替代 **`/peers`** 列表与 CRUD。

## 与既有变更的关系

- **`vpn-peer-instance-mesh-2026-04-13`**：对端实例、mesh、SSH、对端规则；本条 **更新**其 tasks/design 中关于「部署说明弹窗」「`-i` tun」等 **已过时的表述**。
- **`vpn-css-refactor-2026-04-03`**：共享 `mgmt-*` / `page-shell` 布局习惯；本条增加 **`peer-manual-*`** 专用样式块。
