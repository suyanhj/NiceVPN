# 本地第三方前端依赖

新增依赖必须记录名称、固定版本、上游地址、许可证、用途和 SHA-256，并通过离线加载测试。NiceGUI 已内置的前端包不得重复放入本目录。
# 第三方浏览器资源

本目录只保存 NiceGUI 未提供、又确有必要随应用离线分发的固定版本前端库。运行时不得从 CDN 加载依赖，也不得在这里重复打包 NiceGUI 已提供的 Quasar、AG Grid、ECharts 或 SortableJS。

每个新增文件必须同步登记到 `manifest.toml`，包含：

- `name`：上游项目名称；
- `version`：固定版本；
- `file`：本目录内的相对文件名；
- `source_url`：可追溯的上游发布地址；
- `license`：许可证标识；
- `purpose`：本项目中的单一用途；
- `sha256`：本地文件的小写 SHA-256。

当前补充分发 `clipboard.js 2.0.11`，仅用于局域网 HTTP 非安全上下文的文本复制兼容；许可证原文保存为 `clipboard-2.0.11.LICENSE.txt`。HTTPS 和 localhost 仍使用 NiceGUI 的标准剪贴板能力。
