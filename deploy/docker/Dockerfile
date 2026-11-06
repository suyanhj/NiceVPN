# syntax=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/docker/dockerfile:1.6

ARG PYTHON_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.14-slim


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

# Vue 正式运行产物由 GitHub Actions 预编译并随仓库发布；镜像不再安装 Node/npm。
COPY . .

# 防止用缺失或不完整的预编译前端产物制作生产镜像。
RUN test -s app/ui/vue/dist/VpnSpotlightSurface.js \
    && test -s app/ui/vue/dist/VpnInteractiveGrid.js \
    && test -s app/ui/vue/dist/frontend-build.json

EXPOSE 8880 8881

# 需要 --privileged 权限以支持 iptables；推荐配合 --network host。
CMD ["python", "main.py"]
