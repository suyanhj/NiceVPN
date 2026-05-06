# 任务清单 — vpn-cli-dingtalk-download-models-2026-04-10

> 状态：**已完成**（与仓库当前实现一致，供归档与审计）

## CLI

- [x] `cli.py` 与 `main.py cli` 双入口
- [x] `app/cli/entry.py`：`add-group` / `add-user` / `add-firewall`、初始化检查、JSON 输出与错误码
- [x] `app/cli/iptables_parse.py`：文件/行解析
- [x] `_extract_dry_run` + 子解析器/根级 `--dry-run` 声明（argcomplete 与帮助）
- [x] `argcomplete` + `FilesCompleter`（`--iptables-file`）
- [x] `deploy/shell/install-ovpn-cli-symlink.sh`（含 argcomplete 说明与一键软链）

## 依赖

- [x] `requirements.txt`：`dingtalkchatbot`、`argcomplete`

## 钉钉

- [x] `dingtalk.py` 使用 `DingtalkChatbot`
- [x] `SystemConfig.dingtalk_secret` 与设置页保存/展示

## 下载基址与监听

- [x] `app/utils/listen_lan.py`：`set_listen_http_base` / `get_listen_http_base`
- [x] `main.run_web` 在 `ui.run` 前调用 `set_listen_http_base`
- [x] `app/utils/public_base_url.py`：`resolve_download_base_url` 等
- [x] 用户页（等）使用 `resolve_download_base_url` 生成下载链接

## UI 剪贴板

- [x] `app/ui/copy_clipboard.py` 及用户/组/证书等页接入

## 模型包

- [x] `app/models/__init__.py`：PEP 562 `__getattr__` 与 `__all__`

## OpenSpec

- [x] 本 change：`proposal.md`、`design.md`、`tasks.md`、`.openspec.yaml`
