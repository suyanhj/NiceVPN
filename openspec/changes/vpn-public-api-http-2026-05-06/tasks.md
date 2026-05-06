# 任务清单 — vpn-public-api-http-2026-05-06

> 状态：**已完成**（与仓库当前实现一致，供归档与审计）

## 路由与入口

- [x] `main.py`：`include_router(download_router)`、`include_router(vpn_ops_router, prefix="/api")`
- [x] 已初始化时 `ensure_api_basic_credentials_file`

## 公网 API

- [x] `app/api/vpn_ops.py`：Basic、初始化门禁、默认组/按名解析组
- [x] `POST /api/vpn/users`：单次创建、幂等、单 ovpn / 单前缀 zip
- [x] `POST /api/vpn/users/batch`：数组体、同型响应、合并 `vpns_*.zip`、条数与 count 之和上限
- [x] `DELETE /api/vpn/users/{prefix}`：前缀匹配删除、无匹配 200 空列表
- [x] `POST /api/vpn/users/{prefix}/reset-device-binding`：404 无匹配

## 凭据与下载

- [x] `app/utils/api_basic_credentials.py`
- [x] `app/services/download/bundle_zip.py`：`build_ovpn_zip`
- [x] `app/services/download/link_mgr.py`：`create_link`（含 `download_filename`）
- [x] `app/api/download.py`：消费令牌、流式下载
- [x] `app/utils/public_base_url.py`：`resolve_download_base_url`

## 测试

- [x] `tests/unit/test_vpn_ops_api.py`（含 batch、合并 zip、跳过、限额等）
- [x] `tests/unit/test_api_basic_credentials.py` 等（以仓库为准）

## 文档

- [x] `api.md`：§1 / §1b、curl 示例
- [x] `README.md`：API 指向 `api.md`
- [x] `CLAUDE.md`：`api/`、`vpn_ops`、公网 API 简述（若与本文档不一致以代码为准）

## OpenSpec

- [x] 本 change：`proposal.md`、`design.md`、`tasks.md`、`.openspec.yaml`
