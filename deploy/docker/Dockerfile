# syntax=docker/dockerfile:1

FROM node:22-alpine AS frontend-builder

WORKDIR /build/app/ui/vue/frontend

# 先只复制依赖描述文件，最大化 Docker 层缓存命中率。
COPY app/ui/vue/frontend/package.json ./
RUN npm install --no-audit --no-fund --package-lock=false

COPY app/ui/vue/frontend/ ./
RUN npm run check


# 供 start.sh / CI 只导出前端产物，不携带 Node 环境。
FROM scratch AS frontend-assets
COPY --from=frontend-builder /build/app/ui/vue/dist/ /


FROM python:3.14-slim AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

# 安装系统依赖（iptables、ipset、OpenVPN 将在容器内由系统自动安装）
RUN apt update && apt install -y --no-install-recommends \
    iptables \
    ipset \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=frontend-builder /build/app/ui/vue/dist/ /app/app/ui/vue/dist/

# 管理端 data/；OpenVPN 状态目录在宿主机 /etc/openvpn 或自行挂载
RUN mkdir -p data/groups data/users data/firewall data/download_links data/audit data/logs backups \
    /etc/openvpn/ccd /etc/openvpn/mgmt/device_bindings /etc/openvpn/mgmt/ovpn \
    /etc/openvpn/log /etc/openvpn/scripts

EXPOSE 8880

# 注意：需要 --privileged 权限以支持 iptables 操作。
# 推荐配合 --network host 使用，此时无需额外发布 8880 端口。
CMD ["python", "main.py"]
