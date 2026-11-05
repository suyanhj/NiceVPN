#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="${ROOT_DIR}/app/ui/vue/dist"
SERVICE_TEMPLATE="${ROOT_DIR}/deploy/systemd/openvpn-mgmt.service"
SERVICE_PATH="/etc/systemd/system/openvpn-mgmt.service"
SERVICE_NAME="openvpn-mgmt"
BUILD_UI="${NICEVPN_BUILD_UI:-0}"
PYTHON_BIN="${PYTHON_BIN:-}"

log() {
    printf '[NiceVPN] %s\n' "$*"
}

fail() {
    printf '[NiceVPN] ERROR: %s\n' "$*" >&2
    exit 1
}

python_supported() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

python_version() {
    "$1" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'
}

python_realpath() {
    "$1" -c 'import os, sys; print(os.path.realpath(sys.executable))'
}

resolve_candidate() {
    local candidate="$1"
    if [[ "${candidate}" == */* ]]; then
        [[ -x "${candidate}" ]] || return 1
        printf '%s\n' "${candidate}"
        return 0
    fi
    command -v "${candidate}" 2>/dev/null
}

select_python() {
    local candidate resolved
    local -a auto_candidates=()

    # 1. 显式指定具有最高优先级；指定错误时直接失败，避免静默切换解释器。
    if [[ -n "${PYTHON_BIN}" ]]; then
        resolved="$(resolve_candidate "${PYTHON_BIN}")" || fail "PYTHON_BIN 不可执行或不存在：${PYTHON_BIN}"
        python_supported "${resolved}" || fail "PYTHON_BIN 的 Python 版本过低：$(python_version "${resolved}")，要求 Python >= 3.10"
        PYTHON_BIN="$(python_realpath "${resolved}")"
        return 0
    fi

    # 2. 优先复用当前激活的虚拟环境 / Conda 环境。
    if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
        resolved="${VIRTUAL_ENV}/bin/python"
        python_supported "${resolved}" || fail "当前虚拟环境 Python 版本过低：$(python_version "${resolved}")，要求 Python >= 3.10"
        PYTHON_BIN="$(python_realpath "${resolved}")"
        return 0
    fi
    if [[ -n "${CONDA_PREFIX:-}" && -x "${CONDA_PREFIX}/bin/python" ]]; then
        resolved="${CONDA_PREFIX}/bin/python"
        python_supported "${resolved}" || fail "当前 Conda 环境 Python 版本过低：$(python_version "${resolved}")，要求 Python >= 3.10"
        PYTHON_BIN="$(python_realpath "${resolved}")"
        return 0
    fi

    # 3. 项目内 .venv 优先于系统 Python。
    if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
        resolved="${ROOT_DIR}/.venv/bin/python"
        python_supported "${resolved}" || fail "项目 .venv Python 版本过低：$(python_version "${resolved}")，要求 Python >= 3.10"
        PYTHON_BIN="$(python_realpath "${resolved}")"
        return 0
    fi

    # 4. 再从当前 PATH 和常见版本命令中选择第一个满足要求的解释器。
    auto_candidates=(
        python
        python3
        python3.14
        python3.13
        python3.12
        python3.11
        python3.10
    )
    for candidate in "${auto_candidates[@]}"; do
        resolved="$(resolve_candidate "${candidate}" || true)"
        [[ -n "${resolved}" ]] || continue
        if python_supported "${resolved}"; then
            PYTHON_BIN="$(python_realpath "${resolved}")"
            return 0
        fi
    done

    fail "未找到 Python >= 3.10。可通过 PYTHON_BIN=/path/to/python ./start.sh 显式指定。"
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

    # 运行主机上的审计日志轮转，避免把轮转逻辑插入 conntrack/连接钩子。
    # 重复执行 daemon 安装只会更新配置和同一个 systemd timer。
    if command -v logrotate >/dev/null 2>&1; then
        if bash "$ROOT_DIR/deploy/shell/install-nicevpn-audit-logrotate.sh"; then
            log "NiceVPN 审计日志轮转已启用。"
        else
            log "WARNING: 审计日志轮转安装失败，主服务不受影响；请检查安装日志。"
        fi
    else
        log "WARNING: logrotate 未安装，NiceVPN 审计日志不会自动按大小轮转。"
    fi

    log "NiceVPN 已由 systemd 托管。"
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
    elif [[ "${1:-}" == "--no-build" ]]; then
        BUILD_UI=0
        shift
    fi

    if [[ "${mode}" == "daemon" && "$#" -gt 0 ]]; then
        fail "daemon 模式不接受额外参数。支持：./start.sh daemon [--build-ui|--no-build]"
    fi

    select_python
    log "使用 Python $(python_version "${PYTHON_BIN}")：${PYTHON_BIN}"

    # CLI 不渲染 Web UI，不需要构建前端。
    if [[ "${1:-}" != "cli" ]]; then
        prepare_ui
    fi

    if [[ "${mode}" == "daemon" ]]; then
        install_daemon
        return 0
    fi

    log "启动 NiceVPN..."
    exec "${PYTHON_BIN}" "${ROOT_DIR}/main.py" "$@"
}

main "$@"
