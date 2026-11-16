# NiceVPN 开发约束

本文只记录开发时必须遵守的架构和安全边界。具体行为以当前代码和测试为准。

## 架构

- Python 3.10+；依赖版本以 `requirements.txt` 为准。
- `main.py` 启动一个 Python 进程：NiceGUI 管理端默认 8880，FastAPI 公网 API 默认 8881。
- systemd 只使用 `openvpn-mgmt.service`；不要拆出第二个 API 服务。
- API 端口只挂载业务 API、一次性下载和 SSO OAuth 流程；不得挂载管理页面或管理路由。

## 数据

- 当前结构化业务数据使用 `data/nicevpn.db`；旧 JSON 只允许用于一次性迁移。
- 配置、PKI/CRL、CCD、OVPN、下载文件和运行日志仍按文件管理，不要强行迁入 SQLite。
- Schema 变更必须使用版本化 migration；历史迁移不可改写，迁移失败必须回滚。
- SQL 放在 repository / storage 层，不要散落在 UI 回调中。

## Linux 与 OpenVPN

- 安装、依赖、systemd、权限和文件部署优先使用 `app/scripts/`；Python 负责业务编排，不重复实现发行版包管理逻辑。
- Debian/RHEL 共用函数位于 `app/scripts/common/linux-packages.sh`。新增安装流程应复用它。
- 已安装的 OpenVPN、EasyRSA 和完整 PKI 应优先识别、复用；补装 EasyRSA 不得重装 OpenVPN，也不得覆盖已有 CA。
- DCO 是可选优化。依赖不满足时关闭 DCO，不得因此阻断常规 OpenVPN 安装。
- OpenVPN 负责证书、CA、CRL、TLS 和隧道协商；NiceVPN 不重复实现这些校验。
- 独立运行的 OpenVPN hooks 必须使用部署时写入的 Python 绝对路径，不依赖系统默认 `python3` 或交互式虚拟环境。

## 防火墙与标签

- iptables / ipset 使用声明式期望状态；集合更新遵循 staging、swap、失败回滚和安全 GC。
- 中心与每个对端的 ipset 命名空间相互隔离；不得清理未知前缀或其他对端的集合。
- 标签成员变化需同步刷新被引用的中心规则、Mesh 路由和远端规则。
- 删除组或标签前必须检查全部用户、规则和路由引用；UI、Service、Repository 都不能允许绕过检查。

## 身份与安全

- 企业 SSO 默认关闭；仅全员策略或明确启用 SSO 的组/标签触发认证。
- VPN 权限仍由证书、用户、组、标签和防火墙决定；办公系统账号不映射或创建 VPN 用户。
- 企业身份必须由服务端 OAuth 响应核验；不得信任浏览器传入的企业 ID，也不得按客户端自报平台绕过 SSO。
- OpenVPN 原生证书校验不能被 NiceVPN 缓存替代；跨重连复用必须使用可信 Management 证书指纹，不能只凭 CN。
- SSO 成功只改变该连接的设备绑定处理，不改变证书和防火墙权限。密钥、Token、OAuth Code 不得写入日志。
- `data/sso_provider_master.key` 必须与 SQLite 一起备份，权限保持 0600；Management Socket 不得暴露公网。

## UI

- 管理壳使用 NiceGUI，表格优先复用共享 AG Grid 组件。
- 生产前端产物位于 `app/ui/vue/dist/`；生产启动不依赖 Node/npm。
- 保持分页、筛选确认、表格操作和弹窗交互的一致性；不要新增重复标题或无意义说明。

## 常用命令

```bash
./start.sh
sudo -E ./start.sh daemon
./start.sh cli --help
python -m pytest tests/unit -q
./start.sh --build-ui
```

修改 Shell 时执行对应脚本测试；涉及 OpenVPN、iptables、ipset 或 systemd 的改动，除单测外应说明真实 Linux 验证范围。

## 文档职责

- `README.md`：项目入口与部署。
- `api.md` / `cli.md`：稳定接口和命令。
- `docs/`：长期有效的功能与运维说明。
- `openspec/changes/`：变更设计和历史任务，不复制到其他文档。

同一事实只维护一处权威说明，其他文档使用链接，不重复粘贴实现细节。
