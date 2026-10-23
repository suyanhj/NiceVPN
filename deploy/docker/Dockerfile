# syntax=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/docker/dockerfile:1.6

ARG NODE_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/node:22-alpine
ARG PYTHON_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.14-slim


FROM ${NODE_IMAGE} AS frontend-builder

ARG NPM_REGISTRY=https://registry.npmmirror.com

WORKDIR /build/app/ui/vue/frontend

# 前端源码一次复制、一次构建；npm 缓存只留在 BuildKit cache，不进入镜像层。
COPY app/ui/vue/frontend/ ./
RUN --mount=type=cache,target=/root/.npm \
    npm config set registry "${NPM_REGISTRY}" \
    && npm install --no-audit --no-fund --package-lock=false \
    && npm run check


# 仅用于本地、CI、systemd 导出静态产物。
FROM scratch AS frontend-assets
COPY --from=frontend-builder /build/app/ui/vue/dist/ /


FROM ${PYTHON_IMAGE} AS runtime

ARG DEBIAN_MIRROR=mirrors.ustc.edu.cn

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 先复制 Python 依赖描述以保留缓存命中率。
COPY requirements.txt /tmp/requirements.txt

# Debian 12/13 容器可能使用 debian.sources，也兼容传统 sources.list。
# apt / pip 下载缓存使用 BuildKit cache，不写入最终镜像层。
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    --mount=type=cache,target=/root/.cache/pip \
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
    apt-get install -y --no-install-recommends iptables ipset; \
    pip install -r /tmp/requirements.txt; \
    rm -f /tmp/requirements.txt; \
    mkdir -p \
        data/groups data/users data/firewall data/download_links data/audit data/logs backups \
        /etc/openvpn/ccd /etc/openvpn/mgmt/device_bindings /etc/openvpn/mgmt/ovpn \
        /etc/openvpn/log /etc/openvpn/scripts

# app/ui/vue/dist 已由 .dockerignore 排除，避免复制旧产物后再覆盖。
COPY . .
COPY --from=frontend-builder /build/app/ui/vue/dist/ /app/app/ui/vue/dist/

EXPOSE 8880

# 需要 --privileged 权限以支持 iptables；推荐配合 --network host。
CMD ["python", "main.py"]
