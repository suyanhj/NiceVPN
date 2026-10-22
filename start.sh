#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="${ROOT_DIR}/app/ui/vue/dist"
DOCKERFILE="${ROOT_DIR}/deploy/docker/Dockerfile"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DOCKER_BIN="${DOCKER_BIN:-docker}"
BUILD_UI="${NICEVPN_BUILD_UI:-1}"

log() {
    printf '[NiceVPN] %s\n' "$*"
}

build_frontend() {
    if ! command -v "${DOCKER_BIN}" >/dev/null 2>&1; then
        if [[ -f "${DIST_DIR}/VpnSpotlightSurface.js" ]]; then
            log "未找到 Docker，继续使用已有前端构建产物。"
            return 0
        fi
        log "未找到 Docker，且 app/ui/vue/dist/ 中没有可用构建产物。"
        log "请安装 Docker，或先在其他环境构建前端产物后再启动。"
        return 1
    fi

    local output_dir
    output_dir="$(mktemp -d "${TMPDIR:-/tmp}/nicevpn-ui.XXXXXX")"

    log "使用 Docker 多阶段构建 V2 前端..."
    if ! DOCKER_BUILDKIT=1 "${DOCKER_BIN}" build \
        --file "${DOCKERFILE}" \
        --target frontend-assets \
        --output "type=local,dest=${output_dir}" \
        "${ROOT_DIR}"; then
        rm -rf "${output_dir}"
        return 1
    fi

    rm -rf "${DIST_DIR}"
    mkdir -p "${DIST_DIR}"
    cp -a "${output_dir}/." "${DIST_DIR}/"
    rm -rf "${output_dir}"

    if [[ ! -f "${DIST_DIR}/VpnSpotlightSurface.js" ]]; then
        log "前端构建未生成 VpnSpotlightSurface.js。"
        return 1
    fi

    log "前端构建完成：${DIST_DIR}"
}

main() {
    cd "${ROOT_DIR}"

    # CLI 不渲染 Web UI，不需要为了命令行操作启动 Docker 构建。
    if [[ "${1:-}" != "cli" && "${BUILD_UI}" != "0" ]]; then
        build_frontend
    fi

    log "启动 NiceVPN..."
    exec "${PYTHON_BIN}" "${ROOT_DIR}/main.py" "$@"
}

main "$@"
