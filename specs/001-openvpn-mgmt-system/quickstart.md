# 快速入门：可视化 OpenVPN 管理系统

**功能分支**: `001-openvpn-mgmt-system`
**生成日期**: 2026-03-27

---

## 前置条件

- Linux 服务器（CentOS 7+ / Ubuntu 20.04+ / Debian 10+）
- Python 3.10+
- root 或 sudo 权限
- iptables / ipset 已安装（系统通常自带）
- 互联网访问（用于自动安装 OpenVPN，如已安装则不需要）

---

## 安装步骤

### 1. 克隆代码并安装依赖

```bash
git clone <仓库地址> /opt/openvpn-mgmt
cd /opt/openvpn-mgmt
pip install -r requirements.txt
```

### 2. 启动系统

```bash
python main.py
```

首次启动后，浏览器访问 `http://<服务器IP>:8080`，进入初始化引导页。

### 3. 初始化引导流程

系统将自动检测 OpenVPN 是否已安装：

**情况 A：已安装 OpenVPN**
- 系统自动定位 OpenVPN 路径和内置 EasyRSA 目录
- 直接进入网段配置步骤

**情况 B：未安装 OpenVPN**
- 页面显示"未检测到 OpenVPN，点击一键安装最新版本"按钮
- 安装过程需要 root 权限（使用系统包管理器）
- 安装完成后自动定位 EasyRSA，继续引导

**情况 C：自定义安装路径**
- 点击"指定自定义路径"，输入 OpenVPN 可执行文件路径
- 系统验证路径有效性后继续

### 4. 配置全局 VPN 网段

在引导页输入 VPN 网段，例如：

```
10.8.0.0/16
```

系统验证 CIDR 格式合法性后保存，并自动：
- 创建"默认用户组"，绑定子网 `10.8.1.0/24`
- 生成全放行默认防火墙规则
- 完成 PKI 初始化（通过 EasyRSA 生成 CA）

### 5. 启动 VPN 服务

点击"启动 VPN 服务"，系统生成 `server.conf` 并启动 OpenVPN 实例。
仪表盘显示服务状态为"运行中"即表示初始化完成。

---

## 创建第一个 VPN 用户

1. 进入"用户管理"页，点击"新建用户"
2. 填写用户名，选择所属组，可选择是否启用账号密码
3. 点击"创建"，系统自动生成证书和 `.ovpn` 配置文件
4. 点击"发送下载链接"，系统生成一次性链接并通过钉钉机器人推送（或直接复制链接）
5. 用户在设备上导入 `.ovpn` 文件，首次连接时完成设备绑定

---

## 以 systemd 服务方式运行

```bash
# 复制 systemd 服务文件
cp deploy/systemd/openvpn-mgmt.service /etc/systemd/system/

# 启用并启动
systemctl daemon-reload
systemctl enable openvpn-mgmt
systemctl start openvpn-mgmt

# 查看状态
systemctl status openvpn-mgmt
```

---

## Docker 方式部署

```bash
# 构建镜像
docker build -t openvpn-mgmt:latest -f deploy/docker/Dockerfile .

# 运行（需要 --privileged 以执行 iptables 操作）
docker run -d \
  --name openvpn-mgmt \
  --privileged \
  --network host \
  -v /opt/openvpn-mgmt/data:/app/data \
  -v /opt/openvpn-mgmt/backups:/app/backups \
  -p 8080:8080 \
  openvpn-mgmt:latest
```

> **注意**：Docker 方式需要 `--privileged` 权限以支持 iptables 操作。生产环境建议使用 systemd 方式。

---

## 访问地址

| 服务 | 地址 |
|---|---|
| 管理界面 | `http://<服务器IP>:8080` |
| 配置文件下载 | `http://<服务器IP>:8080/download/{token}` |

---

## 常见问题

**Q: 提示"EasyRSA 不可用"怎么办？**
A: 检查 OpenVPN 安装是否完整。在"系统设置"中手动指定 EasyRSA 目录路径。

**Q: 钉钉推送失败怎么办？**
A: 推送失败不影响配置文件的生成。在用户列表中点击用户名，可以直接复制下载链接手动发送。

**Q: 如何备份数据？**
A: 在"系统设置"中点击"导出全量备份"，生成包含所有配置的 ZIP 压缩包（JSON 文件集合）。
