#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="${ROOT_DIR}/app/ui/vue/dist"
SERVICE_TEMPLATE="${ROOT_DIR}/deploy/systemd/openvpn-mgmt.service"
SERVICE_PATH="/etc/systemd/system/openvpn-mgmt.service"
SERVICE_NAME="openvpn-mgmt"
BUILD_UI="${NICEVPN_BUILD_UI:-0}"
PYTHON_BIN="${PYTHON_BIN:-}"

source "${ROOT_DIR}/deploy/shell/python-runtime.sh"

log() {
    printf '[NiceVPN] %s\n' "$*"
}

fail() {
    printf '[NiceVPN] ERROR: %s\n' "$*" >&2
    exit 1
}

select_python() {
    nicevpn_select_python "${ROOT_DIR}" || exit 1
}

frontend_assets_ready() {
    [[ -s "${DIST_DIR}/VpnSpotlightSurface.js" ]] &&
        [[ -s "${DIST_DIR}/VpnInteractiveGrid.js" ]]
}

prepare_ui() {
    if [[ "${BUILD_UI}" == "1" ]]; then
        log "按要求重新构建 Vue 前端资源..."
        "${ROOT_DIR}/build-ui.sh" "${DIST_DIR}"
        frontend_assets_ready || fail "Vue 构建完成但正式运行产物不完整：${DIST_DIR}"
        return 0
    fi

    if frontend_assets_ready; then
        log "使用仓库内预编译 Vue 前端资源：app/ui/vue/dist/"
        return 0
    fi

    log "未找到完整的预编译 Vue 前端资源：app/ui/vue/dist/"
    log "当前环境无需安装 Node/npm/Docker；请使用包含 CI 构建产物的正式提交。"
    log "本次将回退到开发用原生 Vue SFC。需要本机重建时使用：./start.sh --build-ui"
}

install_daemon() {
    [[ "${EUID}" -eq 0 ]] || fail "daemon 模式需要 root 权限，请使用 sudo -E ./start.sh daemon，或显式传入 PYTHON_BIN。"
    [[ -f "${SERVICE_TEMPLATE}" ]] || fail "systemd 模板不存在：${SERVICE_TEMPLATE}"
    command -v systemctl >/dev/null 2>&1 || fail "当前系统未找到 systemctl，无法使用 daemon 模式。"

    local temp_service
    temp_service="$(mktemp "${TMPDIR:-/tmp}/nicevpn-systemd.XXXXXX")"
    trap 'rm -f "${temp_service:-}"' RETURN

    "${PYTHON_BIN}" - "${SERVICE_TEMPLATE}" "${temp_service}" "${ROOT_DIR}" "${PYTHON_BIN}" <<'PY'
from pathlib import Path
import sys

template_path, output_path, root_dir, python_bin = sys.argv[1:]
text = Path(template_path).read_text(encoding="utf-8")
text = text.replace("@NICEVPN_ROOT@", root_dir)
text = text.replace("@PYTHON_BIN@", python_bin)
Path(output_path).write_text(text, encoding="utf-8")
PY

    install -m 0644 "${temp_service}" "${SERVICE_PATH}"

    log "已安装 systemd 服务：${SERVICE_PATH}"
    log "WorkingDirectory=${ROOT_DIR}"
    log "Python=${PYTHON_BIN}"

    systemctl daemon-reload
    systemctl enable "${SERVICE_NAME}" >/dev/null
    systemctl restart "${SERVICE_NAME}"

    # 使用系统 logrotate.timer/cron，直接在 /etc/logrotate.d 下配置。
    if command -v logrotate >/dev/null 2>&1; then
        if bash "$ROOT_DIR/deploy/shell/install-nicevpn-logrotate.sh"; then
            log "NiceVPN/OpenVPN 日志轮转配置已安装。"
        else
            log "WARNING: 日志轮转配置安装失败，主服务不受影响；请检查安装日志。"
        fi
    else
        log "WARNING: logrotate 未安装，OpenVPN/NiceVPN 日志不会自动轮转。"
    fi

    log "NiceVPN 管理端与公网 API 已由同一个 systemd 服务托管。"
    systemctl --no-pager --full status "${SERVICE_NAME}" || true
}

main() {
    cd "${ROOT_DIR}"

    local mode="run"
    if [[ "${1:-}" == "daemon" ]]; then
        mode="daemon"
        shift
    fi

    if [[ "${1:-}" == "--build-ui" ]]; then
        BUILD_UI=1
        shift
    fi

    if [[ "${mode}" == "daemon" && "$#" -gt 0 ]]; then
        fail "daemon 模式不接受额外参数。支持：./start.sh daemon [--build-ui]"
    fi

    select_python
    log "使用 Python $(nicevpn_python_version "${PYTHON_BIN}")：${PYTHON_BIN}"

    if [[ "${mode}" == "daemon" ]]; then
        install_daemon
        return 0
    fi

    # CLI 使用独立入口，不加载 Web 管理端，也不构建前端。
    if [[ "${1:-}" == "cli" ]]; then
        shift
        log "运行 NiceVPN CLI..."
        exec "${PYTHON_BIN}" "${ROOT_DIR}/cli.py" "$@"
    fi

    prepare_ui
    log "启动 NiceVPN（管理端 + 公网 API 双端口）..."
    exec "${PYTHON_BIN}" "${ROOT_DIR}/main.py" "$@"
}

main "$@"
