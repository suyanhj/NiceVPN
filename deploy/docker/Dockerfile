# syntax=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/docker/dockerfile:1.6

ARG NODE_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/node:22-alpine
ARG PYTHON_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.14-slim


FROM ${NODE_IMAGE} AS frontend-builder

ARG NPM_REGISTRY=https://registry.npmmirror.com

WORKDIR /build/app/ui/vue/frontend

# 前端源码一次复制、一次 npm ci 构建；依赖由 package-lock.json 锁定，registry 可覆盖，npm 缓存只留在 BuildKit cache。
COPY app/ui/vue/frontend/ ./
RUN --mount=type=cache,target=/root/.npm \
    npm config set registry "${NPM_REGISTRY}" \
    && npm ci --no-audit --no-fund \
    && npm run check


# 仅用于本地、CI、systemd 导出静态产物。
FROM scratch AS frontend-assets
COPY --from=frontend-builder /build/app/ui/vue/dist/ /


# 系统依赖与 Python 依赖分层：
# - Python 基础镜像或 apt 包不变时，requirements.txt 变化不会重新执行 apt。
# - CI 使用 Buildx 外部缓存后，这两个阶段可跨 GitHub Hosted Runner 复用。
FROM ${PYTHON_IMAGE} AS python-system

ARG DEBIAN_MIRROR=mirrors.ustc.edu.cn

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Debian 12/13 容器可能使用 debian.sources，也兼容传统 sources.list。
RUN --mount=type=cache,id=nicevpn-apt-cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=nicevpn-apt-lists,target=/var/lib/apt/lists,sharing=locked \
    set -eux; \
    for file in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do \
        if [ -f "${file}" ]; then \
            sed -i -E \
                -e "s#https?://deb.debian.org#https://${DEBIAN_MIRROR}#g" \
                -e "s#https?://security.debian.org#https://${DEBIAN_MIRROR}#g" \
                "${file}"; \
        fi; \
    done; \
    apt-get update; \
    apt-get install -y --no-install-recommends iptables ipset


FROM python-system AS python-deps

COPY requirements.txt /tmp/requirements.txt

RUN --mount=type=cache,id=nicevpn-pip-cache,target=/root/.cache/pip \
    pip install -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt


FROM python-deps AS runtime

WORKDIR /app

RUN mkdir -p \
        data/groups data/users data/firewall data/download_links data/audit data/logs backups \
        /etc/openvpn/ccd /etc/openvpn/mgmt/device_bindings /etc/openvpn/mgmt/ovpn \
        /etc/openvpn/log /etc/openvpn/scripts

# app/ui/vue/dist 已由 .dockerignore 排除，避免复制旧产物后再覆盖。
COPY . .
COPY --from=frontend-builder /build/app/ui/vue/dist/ /app/app/ui/vue/dist/

EXPOSE 8880

# 需要 --privileged 权限以支持 iptables；推荐配合 --network host。
CMD ["python", "main.py"]
