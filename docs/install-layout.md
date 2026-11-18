# OpenVPN / EasyRSA 安装目录与版本切换

源码安装统一采用版本目录平铺 + `current` 相对软链接。目录名只使用版本号，不附加 hash，不创建 `releases/` 子目录。

## 目录结构

    /opt/openvpn/
    ├── current -> 2.7.1
    ├── 2.1.1/
    ├── 2.5.1/
    └── 2.7.1/
        ├── sbin/openvpn
        ├── lib/...
        └── share/...

    /opt/ezrsa/
    ├── current -> 3.2.4
    ├── 3.1.7/
    └── 3.2.4/
        ├── easyrsa
        └── x509-types/...

## 安装和升级

- OpenVPN 编译时固定使用 `/opt/openvpn/current` 作为 prefix，构建产物安装到独立 staging，再发布到 `/opt/openvpn/<version>/`。
- EasyRSA 安装到 `/opt/ezrsa/<version>/`。
- 安装完成后通过原子更新相对软链接切换 `current`。
- 旧版本目录保留；回滚只需把 `current` 指回旧版本，再重启服务。
- systemd、NiceVPN 和 CLI 使用固定入口 `/opt/openvpn/current/sbin/openvpn`、`/opt/ezrsa/current/easyrsa`，无需随版本修改。
- 同一版本目录已存在时会校验版本并复用；不会静默覆盖已有目录。
- 系统包安装仍由 apt/dnf/yum 管理，不会切换到源码版本。

NiceVPN 启动时会调用共享 Shell 检查并迁移旧布局：旧 `/opt/openvpn/{sbin,lib,libexec,include,share}` 会按二进制版本整理到 `/opt/openvpn/<version>/`，再创建 `current -> <version>`；旧 `/opt/easy-rsa/releases/<version>/` 会迁移到 `/opt/ezrsa/<version>/`，并重建相对 `current` 软链接。迁移同时更新 NiceVPN 管理的 systemd unit、CLI 软链接和系统设置中保存的旧可执行文件路径。目标目录冲突或旧目录结构异常时会跳过对应迁移并保留原目录，不覆盖已有版本。

## 关键固定路径

| 组件 | 固定入口 | 版本目录 |
|---|---|---|
| OpenVPN | `/opt/openvpn/current/sbin/openvpn` | `/opt/openvpn/<version>/` |
| EasyRSA | `/opt/ezrsa/current/easyrsa` | `/opt/ezrsa/<version>/` |
