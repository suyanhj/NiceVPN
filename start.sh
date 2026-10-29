#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="${ROOT_DIR}/app/ui/vue/dist"
PYTHON_BIN="${PYTHON_BIN:-python3}"
BUILD_UI="${NICEVPN_BUILD_UI:-1}"

log() {
    printf '[NiceVPN] %s\n' "$*"
}

main() {
    cd "${ROOT_DIR}"

    # CLI 不渲染 Web UI，不需要为了命令行操作构建前端。
    if [[ "${1:-}" != "cli" && "${BUILD_UI}" != "0" ]]; then
        "${ROOT_DIR}/build-ui.sh" "${DIST_DIR}"
    elif [[ "${1:-}" != "cli" && ! -f "${DIST_DIR}/VpnSpotlightSurface.js" ]]; then
        log "已跳过前端构建，但 app/ui/vue/dist/ 中没有正式 V2 构建产物。"
        log "NiceGUI 将回退到开发用原生 Vue SFC。"
    fi

    log "启动 NiceVPN..."
    exec "${PYTHON_BIN}" "${ROOT_DIR}/main.py" "$@"
}

main "$@"
