# OpenVPN / EasyRSA 安装目录与版本切换

NiceVPN 将系统包安装与源码安装分开管理，避免 `git pull`、NiceVPN 重启或 OpenVPN 升级时误用旧二进制。

## 安装模式

| 组件 | 系统包模式 | 源码模式 |
|---|---|---|
| OpenVPN binary | `/usr/sbin/openvpn` 或 `/usr/bin/openvpn` | `/opt/openvpn/sbin/openvpn` |
| OpenVPN 版本目录 | 由 apt/dnf/yum 管理 | `/opt/openvpn-releases/<version>-<sha256前缀>/` |
| OpenVPN 固定入口 | 系统包路径 | `/opt/openvpn` 软链接指向当前 release |
| EasyRSA | 发行版包路径，如 `/usr/share/easy-rsa/` | `/opt/ezrsa/current/easyrsa` |
| EasyRSA 版本目录 | 由包管理器管理 | `/opt/ezrsa/releases/<version>/` |

源码安装时，OpenVPN 的 `--prefix` 始终使用 `/opt/openvpn`。构建产物先进入隔离 staging 目录，通过版本和 binary SHA-256 校验后发布到 `/opt/openvpn-releases/`，最后原子切换 `/opt/openvpn` 软链接。systemd、NiceVPN 配置和 CLI 均引用固定入口，不保存 release 真实目录。源码安装 postflight 会严格验证稳定入口下的 binary 与目标版本；不会拿 `/usr/local` 或系统包中的旧 binary 代替源码安装结果。

EasyRSA 继续使用 `releases/ + current` 的版本管理方式，新安装根目录为 `/opt/ezrsa`。

## 旧目录兼容

- 旧版 `/opt/openvpn` 普通目录会在下一次 NiceVPN 管理的源码安装/升级事务中迁移到 `/opt/openvpn-releases/legacy-*`，再建立 `/opt/openvpn` 稳定软链接。
- 旧版 `/opt/easy-rsa` 会在下一次 EasyRSA 安装入口执行时迁移到 `/opt/ezrsa`；原路径保留指向新目录的兼容软链接。
- 若新旧 EasyRSA 目录同时存在，不自动合并或覆盖；优先使用 `/opt/ezrsa`，旧目录保留供人工核对。
- EasyRSA 迁移与版本安装共用 `/run/lock/nicevpn-easyrsa-install.lock`，避免两个进程同时移动目录或切换 `current`。
- 旧 `config.json` 中的 `openvpn_bin` / `easyrsa_dir` 仍可继续使用。检测会优先遵循持久化安装模式，并保留软链接路径，不会将 `current` 展开为版本目录。
- 自定义路径不自动迁移、不自动覆盖。

目录迁移只发生在安装/升级事务中，不会因为 NiceVPN 启动或拉取新代码就擅自移动系统文件。

## 升级和回滚

- 系统包模式：由 apt/dnf/yum 管理更新。NiceVPN 不会把系统包二进制替换成源码版本。
- NiceVPN 源码模式：候选版本先在临时目录构建，并完成服务端配置、临时客户端配置 preflight；通过后复制到独立 release 目录并校验 binary 版本及 SHA-256。
- 升级时只原子切换 `/opt/openvpn` 软链接，然后重启升级前处于 active 的 OpenVPN 实例并检查服务状态；**不重写 systemd unit、override、CLI 软链接或任何 VPN 配置，也不创建整套 NiceVPN 备份**。这些固定路径始终引用 `/opt/openvpn`，切换目标即可生效。
- 升级失败：Shell 将 `/opt/openvpn` 原子切回旧 release，并重启原运行实例。旧版本目录始终保留，不复制、不移动、不删除。
- 如果升级进程在切换前中断，当前版本不受影响；切换后中断时，稳定入口仍指向一个完整 release，旧版本目录也仍然保留，可再次切换回去。升级流程不再维护 Python manifest、systemd 快照或独立恢复状态机。
- 首次安装仍由 Shell 负责 systemd unit/override 初始化和安装后校验；系统包模式则验证包路径、版本、unit 和原运行实例状态。

安装模式记录在 NiceVPN 配置中，并由 `/etc/openvpn/mgmt/openvpn-install-mode` 提供安装脚本与 Python 运行时之间的持久化标记。
