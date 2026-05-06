FROM python:3.10-slim

WORKDIR /app

# 安装系统依赖（iptables、ipset、OpenVPN 将在容器内由系统自动安装）
RUN apt-get update && apt-get install -y --no-install-recommends \
    iptables \
    ipset \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 管理端 data/；OpenVPN 状态目录在宿主机 /etc/openvpn 或自行挂载
RUN mkdir -p data/groups data/users data/firewall data/download_links data/audit data/logs backups \
    /etc/openvpn/ccd /etc/openvpn/mgmt/device_bindings /etc/openvpn/mgmt/ovpn \
    /etc/openvpn/log /etc/openvpn/scripts

EXPOSE 8080

# 注意：需要 --privileged 权限以支持 iptables 操作
# docker run -d --privileged --network host -v /opt/openvpn-mgmt/data:/app/data openvpn-mgmt
CMD ["python", "main.py"]
