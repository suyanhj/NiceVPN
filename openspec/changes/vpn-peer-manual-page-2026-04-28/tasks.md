# 任务清单 — vpn-peer-manual-page-2026-04-28

> 状态：与仓库实现一致（2026-04-28）。

- [x] **`main.py`**：注册 **`/peers/manual`**，`Request.query_params["peer"]` 传给 `PeersPage.render_manual_page`
- [x] **`peers.py`**：列表入口跳转子页；`render_manual_page` 结构化渲染；`_copy_manual_text` 调用 **`copy_text_to_clipboard`**
- [x] **`peer_manual_md.py`**：新增 **`build_peer_site_manual_context`**；**压缩** **`build_peer_site_manual_markdown`**
- [x] **`PeerService`**：新增 **`export_peer_manual_context`**
- [x] **`theme.py`**：新增 **`peer-manual-page*`** / **`peer-manual-step*`** / **`peer-manual-command*`** 等样式
- [x] **单测**：`test_peer_manual_md.py` 扩展上下文断言
- [x] **OpenSpec**：更新 **`vpn-peer-instance-mesh-2026-04-13`** 中已过时的部署说明 / 网卡绑定描述；本条 **proposal/design/tasks** 定稿
