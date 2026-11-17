# OpenVPN / EasyRSA 安装目录与版本切换

NiceVPN 源码安装统一采用「版本目录平铺 + current 软链接」布局。OpenVPN 与 EasyRSA 不再额外套一层 releases/，也不再使用独立的 /opt/openvpn-releases。

## 目录结构

    /opt/openvpn/
    ├── current -> 2.7.9-<binary-sha256前缀>
    ├── 2.7.9-<binary-sha256前缀>/
    │   ├── sbin/openvpn
    │   ├── lib/...
    │   └── share/...
    ├── 2.7.8-<binary-sha256前缀>/
    ├── sbin -> current/sbin
    ├── bin -> current/bin
    └── share -> current/share

    /opt/ezrsa/
    ├── current -> 3.2.4-<tree-sha256前缀>
    ├── 3.2.4-<tree-sha256前缀>/
    │   ├── easyrsa
    │   └── x509-types/...
    └── 3.1.x-<tree-sha256前缀>/

版本目录名由版本号和内容 SHA-256 前缀组成。同一版本但内容不同不会覆盖已有目录。安装完成后只切换相对路径 current，旧版本目录保留。

## 安装模式

| 组件 | 系统包模式 | NiceVPN 源码模式 |
|---|---|---|
| OpenVPN binary | /usr/sbin/openvpn 或 /usr/bin/openvpn | /opt/openvpn/current/sbin/openvpn |
| OpenVPN 版本目录 | 由 apt/dnf/yum 管理 | /opt/openvpn/<version>-<hash>/ |
| OpenVPN 固定入口 | 系统包路径 | /opt/openvpn/current |
| EasyRSA | 发行版包路径，如 /usr/share/easy-rsa/ | /opt/ezrsa/current/easyrsa |
| EasyRSA 版本目录 | 由包管理器管理 | /opt/ezrsa/<version>-<hash>/ |

OpenVPN 编译时的 prefix 固定为 /opt/openvpn/current。构建产物先进入隔离 staging 目录，完成版本和 binary SHA-256 校验后发布到 /opt/openvpn/<version>-<hash>/，最后原子切换 current。根目录的 sbin/bin/share 等兼容链接指向 current，旧 systemd unit 和 CLI 路径无需因版本升级而改写。

EasyRSA 使用相同的目录规则，版本目录直接位于 /opt/ezrsa/ 下，current 指向当前版本。

## 旧测试环境调整

正常安装、升级和应用启动不再自动迁移旧目录。旧测试环境只需手动执行一次：

    bash scripts/migrate-version-layout-once.sh

脚本会：
- 将当前 OpenVPN 版本及 /opt/openvpn-releases 中的历史版本整理到 /opt/openvpn/<version>-<hash>/。
- 将当前 EasyRSA 版本及旧 releases/ 目录中的版本整理到 /opt/ezrsa/<version>-<hash>/。
- 重建 current 和 OpenVPN 根目录兼容链接。
- 原目录和旧版本仓库均保留为备份，不删除 PKI、配置、证书或历史二进制。
- 遇到同版本但内容不同、路径类型异常或无法识别的目录时停止，不覆盖现有数据。

执行前请确保没有 OpenVPN 安装/升级任务正在运行。脚本完成后检查 /opt/openvpn/current/sbin/openvpn --version、/opt/ezrsa/current/easyrsa --version 及 VPN 服务状态。确认正常后，再手动清理备份目录。

## 升级和回滚

- 系统包模式：由 apt/dnf/yum 管理更新。NiceVPN 不会把系统包二进制替换成源码版本。
- NiceVPN 源码模式：候选版本先在临时目录构建，并完成服务端配置、临时客户端配置 preflight；通过后发布到根目录下的独立版本目录。
- 升级只原子切换 /opt/openvpn/current，然后重启升级前处于 active 的 OpenVPN 实例并检查服务状态；不重写 systemd unit、override、CLI 软链接或 VPN 配置。
- 升级失败时，Shell 将 current 原子切回旧版本并重启原运行实例。新旧版本目录都保留，不复制、不移动、不删除。
- 系统包安装仍验证包路径、版本、unit 和原运行实例状态。

安装模式记录在 NiceVPN 配置中，并由 /etc/openvpn/mgmt/openvpn-install-mode 提供安装脚本与 Python 运行时之间的持久化标记。
