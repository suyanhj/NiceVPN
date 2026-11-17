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

## 当前测试环境一次性调整

之前测试版本目录可能带有 hash 后缀，例如 `2.7.1-a1b2c3d4e5f6`。新布局只保留版本号。拉取本次代码后，在测试机执行一次：

    bash scripts/migrate-version-layout-once.sh

脚本只处理 `/opt/openvpn` 和 `/opt/ezrsa` 下的版本目录：
- 将 `<version>-<hash>` 重命名为 `<version>`。
- 把 `current` 更新为指向纯版本号的相对软链接。
- 删除旧实现创建的 `sbin/bin/share` 等兼容软链接。
- 不复制或删除 PKI、证书、VPN 配置和 systemd 文件。
- 若同版本目录内容冲突，或 `current` 指向安装根目录之外，脚本会停止，不会覆盖。

执行前先停止正在进行的安装/升级任务。执行后检查 OpenVPN、EasyRSA 版本及 VPN 服务状态即可。

## 关键固定路径

| 组件 | 固定入口 | 版本目录 |
|---|---|---|
| OpenVPN | `/opt/openvpn/current/sbin/openvpn` | `/opt/openvpn/<version>/` |
| EasyRSA | `/opt/ezrsa/current/easyrsa` | `/opt/ezrsa/<version>/` |
