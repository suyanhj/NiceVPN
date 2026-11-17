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
- NiceVPN 源码模式：候选版本先在临时目录构建，并完成服务端配置、客户端配置 preflight；通过后发布为独立 release，再原子切换 `/opt/openvpn`。
- 升级失败：只把 `/opt/openvpn` 切回旧 release，旧版本目录不移动、不删除；systemd 配置和运行实例按原事务流程恢复。切换前会持久化事务 manifest 和 systemd/CLI 快照，事务目录位于 `/opt/openvpn-releases/.nicevpn-upgrade-transaction/`，不依赖 `/tmp`。
- 升级成功：保留旧 release，供人工回退。版本目录清理应作为独立 GC 操作，不能在切换事务中删除唯一回滚目标。
- 如果升级进程被 SIGKILL、断电或服务器重启打断，NiceVPN 下次启动会读取持久化 manifest，恢复旧稳定入口、systemd/CLI 快照，并重启升级前处于 active 的服务实例；恢复不完整时保留事务材料并记录严重日志，不会静默删除。
- 安装成功后，源码安装会重启升级前已运行的 OpenVPN 实例并检查 `active` 状态；包管理器模式则验证包路径、版本、unit 和原运行实例状态。

安装模式记录在 NiceVPN 配置中，并由 `/etc/openvpn/mgmt/openvpn-install-mode` 提供安装脚本与 Python 运行时之间的持久化标记。
