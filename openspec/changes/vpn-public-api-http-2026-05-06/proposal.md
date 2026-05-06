# vpn-public-api-http-2026-05-06

## 概述

将 **公网 VPN 管理 HTTP API** 及配套能力纳入 OpenSpec 归档（此前已实现但未写入 changes），包括：

1. **`POST /api/vpn/users`**：按前缀 + `count` 创建用户，返回与控制台一致的账号展开规则；幂等（任一站内账号已存在则整段跳过）；`count==1` 返回单 `.ovpn` 链接，`count>1` 返回该前缀 zip。
2. **`POST /api/vpn/users/batch`**：请求体为 JSON 数组，**每项字段与单次创建相同**；**响应 JSON 与单次接口同型**（`created` / `usernames` / `download_url` / `message`）；凡本单有新建则全部新建账号的 `.ovpn` **合并为一个** `vpns_YYYYMMDD_HHMMSS.zip`（UTC）；`usernames` 为按请求顺序展开后的**计划名**列表，zip **仅含实际新建**文件。
3. **`DELETE /api/vpn/users/{prefix}`**、**`POST /api/vpn/users/{prefix}/reset-device-binding`**：前缀匹配规则与控制台批量一致（`base` 与 `base_数字`）。
4. **认证**：除另有说明外，上述接口 **HTTP Basic**；凭据来自 **`data/api_basic_credentials.json`**（初始化后生成）；**`GET /download/{token}`** 不带 Basic。
5. **文档与测试**：**`api.md`**、**`README.md`**；**`tests/unit/test_vpn_ops_api.py`** 等。

## 背景与动机

- 外部系统需程序化开号、注销与重置设备绑定，且需与 Web 控制台幂等语义一致。
- 批处理需减少协议差异：响应同型、单链下载合并包，降低调用方适配成本。
- OpenSpec 需与仓库实现一致，便于审计与后续变更引用。

## 范围（已实现）

| 领域 | 主要路径 |
|------|-----------|
| 路由 | `main.py` 内 `app.include_router(vpn_ops_router, prefix="/api")` |
| API 实现 | `app/api/vpn_ops.py` |
| Basic 凭据 | `app/utils/api_basic_credentials.py`；启动 `ensure_api_basic_credentials_file`（已初始化时） |
| 合并 zip | `app/services/download/bundle_zip.py`（`build_ovpn_zip`） |
| 下载令牌 | `app/services/download/link_mgr.py`；`app/api/download.py` |
| 下载基址 | `app/utils/public_base_url.py`（`resolve_download_base_url`） |

## 非目标

- 不在本变更中重新定义 OpenVPN 服务端隧道网段、防火墙数据面（参见既有 `vpn-lan-firewall-devicebind`、`vpn-peer-instance-mesh` 等）。
- 不承诺批量请求在**部分写库失败后**自动回滚已创建账号（与多次调用单次接口语义一致）。

## 交叉引用

- **`vpn-cli-dingtalk-download-models-2026-04-10`**：`resolve_download_base_url`、监听基址。
- **`vpn-lan-firewall-devicebind-2026-04-09`**：用户组与站内账号模型延续。
- **`vpn-pki-jsonlock-import-2026-04-09`**：创建用户依赖已初始化 PKI。
