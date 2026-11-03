#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKERFILE="${ROOT_DIR}/deploy/docker/Dockerfile"
FRONTEND_DIR="${ROOT_DIR}/app/ui/vue/frontend"
DOCKER_BIN="${DOCKER_BIN:-docker}"
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

main() {
    local output_dir temp_dir
    output_dir="$(resolve_output_dir "${OUTPUT_ARG}")"

    if [[ "${output_dir}" == "/" || "${output_dir}" == "${ROOT_DIR}" ]]; then
        log "拒绝使用危险输出目录：${output_dir}"
        return 1
    fi

    temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/nicevpn-ui.XXXXXX")"
    trap 'rm -rf "${temp_dir:-}"' RETURN

    if command -v npm >/dev/null 2>&1; then
        log "检测到 npm，使用本机 Node.js 构建 Vue 前端..."
        (
            cd "${FRONTEND_DIR}"
            npm config set registry "${NPM_REGISTRY:-https://registry.npmmirror.com}"
            npm ci --no-audit --no-fund
            npm run check
        )
        cp -a "${ROOT_DIR}/app/ui/vue/dist/." "${temp_dir}/"
    elif command -v "${DOCKER_BIN}" >/dev/null 2>&1; then
        log "未找到 npm，使用 Docker 构建 frontend-assets..."
        DOCKER_BUILDKIT=1 "${DOCKER_BIN}" build \
            --file "${DOCKERFILE}" \
            --target frontend-assets \
            --output "type=local,dest=${temp_dir}" \
            "${ROOT_DIR}"
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

    rm -rf "${output_dir}"
    mkdir -p "${output_dir}"
    cp -a "${temp_dir}/." "${output_dir}/"

    log "构建完成：${output_dir}"
    find "${output_dir}" -maxdepth 1 -type f -printf '  %f\n' 2>/dev/null || true
}

main "$@"
