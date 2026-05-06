# 设计说明 — vpn-cli-dingtalk-download-models-2026-04-10

## 1. CLI 与 `--dry-run`

- **入口**：根目录 `cli.py` 调用 `app.cli.entry.main`；`main.py` 在 `sys.argv[1] == "cli"` 时传入 `sys.argv[2:]`。
- **子命令**：`add-group`（`--name`、`--subnet`）、`add-user`（组 id/名、可选密码）、`add-firewall`（`owner-type`、`owner-id` / `group-name` / `username`、`--iptables-file` / `--iptables-line`）。
- **`--dry-run`**：`_extract_dry_run` 在解析前从 **任意位置** 剥离所有 `--dry-run` 标记；业务与正式命令共用同一套解析与校验，仅在末尾根据布尔值决定打印 JSON 预览或落库。
- **argcomplete**：`build_parser()` 末尾对 parser 调用 `argcomplete.autocomplete`（若已安装）。子解析器与根解析器上声明 `--dry-run`，供补全发现（剥离后运行时仍以预扫描结果为准）。`--iptables-file` 绑定 `FilesCompleter`。部署说明与一键软链见 **`deploy/shell/install-ovpn-cli-symlink.sh`**。

## 2. 钉钉（dingtalkchatbot）

- **`send_dingtalk_text(webhook_url, secret, content)`**：构造 `DingtalkChatbot(webhook, secret=…)`，`secret` 规范化后空串视为未配置；以 `SEC` 开头的密钥由库内走加签逻辑。
- **`send_download_link`**：读取 `config.dingtalk_webhook`、`config.dingtalk_secret`；仅管理员主动触发；失败写审计、不抛到 UI 主流程（与现有 docstring 一致）。

## 3. 下载基址（监听快照 + 请求）

- **`listen_lan`**：在 `set_listen_http_base(scheme, bind_host, port)` 时根据本机地址优选（如 `eth0`）生成 `http://<lan-ip>:<port>` 快照，供无 Request 上下文时使用。
- **`public_base_url`**：`public_base_url_from_request` 从 `Forwarded`/`Host`/`request.base_url` 等推断；`resolve_download_base_url` 在 UI 中结合 `get_ui_request()` 与 `get_listen_http_base()` 回退，避免默认死写 `localhost`（在无合适请求时）。

## 4. 剪贴板

- **`copy_clipboard` 模块**：优先尝试异步 `navigator.clipboard`；不可用时使用隐藏 `textarea` + `document.execCommand('copy')` 回退（适用于 HTTP + 内网 IP）。

## 5. `app.models` 惰性导出

- **`__getattr__(name)`**：按 `name` 映射到对应子模块类，未识别名称抛出 `AttributeError`。
- **效果**：`import app.models.config` 不再因 `__init__.py` 顶层 import 而加载 `firewall` 等；**部署环境仍须保证实际用到的模型文件存在**，否则在首次访问该属性时仍会失败。
