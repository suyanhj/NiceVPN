#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="${ROOT_DIR}/app/ui/vue/frontend"
DOCKER_BIN="${DOCKER_BIN:-docker}"
DOCKER_NODE_IMAGE="${DOCKER_NODE_IMAGE:-swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/node:22-alpine}"
OUTPUT_ARG="${1:-app/ui/vue/dist}"

log() {
    printf '[NiceVPN UI] %s\n' "$*"
}

resolve_output_dir() {
    local raw="$1"
    local path parent base

    if [[ "${raw}" = /* ]]; then
        path="${raw}"
    else
        path="${ROOT_DIR}/${raw}"
    fi

    parent="$(dirname "${path}")"
    base="$(basename "${path}")"
    mkdir -p "${parent}"
    printf '%s/%s\n' "$(cd "${parent}" && pwd)" "${base}"
}

write_build_metadata() {
    local dest="$1"
    local built_by="$2"
    local node_version="$3"
    local source_commit="unknown"

    if command -v git >/dev/null 2>&1; then
        source_commit="$(git -C "${ROOT_DIR}" rev-parse HEAD 2>/dev/null || printf 'unknown')"
    fi

    SOURCE_COMMIT="${source_commit}" BUILT_BY="${built_by}" NODE_VERSION="${node_version}" \
        python3 - "${dest}/frontend-build.json" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.write_text(
    json.dumps(
        {
            "source_commit": os.environ["SOURCE_COMMIT"],
            "node": os.environ["NODE_VERSION"],
            "built_by": os.environ["BUILT_BY"],
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
PY
}


main() {
    local output_dir temp_dir
    output_dir="$(resolve_output_dir "${OUTPUT_ARG}")"

    if [[ "${output_dir}" == "/" || "${output_dir}" == "${ROOT_DIR}" ]]; then
        log "拒绝使用危险输出目录：${output_dir}"
        return 1
    fi

    temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/nicevpn-ui.XXXXXX")"
    trap 'rm -rf "${temp_dir:-}"' RETURN

    local built_by node_version

    if command -v npm >/dev/null 2>&1; then
        built_by="local-npm"
        node_version="$(node --version 2>/dev/null || printf 'unknown')"
        log "检测到 npm，使用本机 Node.js 构建 Vue 前端..."
        (
            cd "${FRONTEND_DIR}"
            npm config set registry "${NPM_REGISTRY:-https://registry.npmmirror.com}"
            npm ci --no-audit --no-fund
            npm run check
        )
        cp -a "${ROOT_DIR}/app/ui/vue/dist/." "${temp_dir}/"
    elif command -v "${DOCKER_BIN}" >/dev/null 2>&1; then
        built_by="local-docker"
        node_version="22"
        log "未找到 npm，使用临时 Node 22 容器构建 Vue 前端..."
        DOCKER_BUILDKIT=1 "${DOCKER_BIN}" build \
            --build-arg "NODE_IMAGE=${DOCKER_NODE_IMAGE}" \
            --build-arg "NPM_REGISTRY=${NPM_REGISTRY:-https://registry.npmmirror.com}" \
            --output "type=local,dest=${temp_dir}" \
            --file - \
            "${ROOT_DIR}" <<'DOCKERFILE'
ARG NODE_IMAGE
FROM ${NODE_IMAGE} AS builder

ARG NPM_REGISTRY=https://registry.npmmirror.com
WORKDIR /build/app/ui/vue/frontend

COPY app/ui/vue/frontend/ ./
RUN npm config set registry "${NPM_REGISTRY}" \
    && npm ci --no-audit --no-fund \
    && npm run check

FROM scratch
COPY --from=builder /build/app/ui/vue/dist/ /
DOCKERFILE
    else
        log "无法重新构建 Vue：未找到 npm 或 Docker。"
        log "生产环境无需构建，请直接使用仓库内 app/ui/vue/dist/ 预编译产物。"
        return 1
    fi

    for artifact in VpnSpotlightSurface.js VpnInteractiveGrid.js; do
        if [[ ! -s "${temp_dir}/${artifact}" ]]; then
            log "构建产物缺失：${artifact}"
            return 1
        fi
    done

    write_build_metadata "${temp_dir}" "${built_by}" "${node_version}"

    rm -rf "${output_dir}"
    mkdir -p "${output_dir}"
    cp -a "${temp_dir}/." "${output_dir}/"

    log "构建完成：${output_dir}"
    find "${output_dir}" -maxdepth 1 -type f -printf '  %f\n' 2>/dev/null || true
}

main "$@"
