#!/usr/bin/env bash
# NiceVPN shared Python runtime discovery.
# Source this file from start.sh and deployment helpers.

nicevpn_python_supported() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

nicevpn_python_version() {
    "$1" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'
}

nicevpn_python_executable_path() {
    # Keep the venv launcher path; realpath would bypass virtual-environment packages.
    "$1" -c 'import os, sys; print(os.path.abspath(sys.executable))'
}

nicevpn_resolve_python_candidate() {
    local candidate="$1"
    if [[ "${candidate}" == */* ]]; then
        [[ -x "${candidate}" ]] || return 1
        printf '%s\n' "${candidate}"
        return 0
    fi
    command -v "${candidate}" 2>/dev/null
}

nicevpn_select_python() {
    local root_dir="$1"
    local candidate resolved version
    local -a candidates=()

    # An explicit choice is authoritative: fail rather than silently using another runtime.
    if [[ -n "${PYTHON_BIN:-}" ]]; then
        resolved="$(nicevpn_resolve_python_candidate "${PYTHON_BIN}")" || {
            printf '[NiceVPN] ERROR: PYTHON_BIN 不可执行或不存在：%s\n' "${PYTHON_BIN}" >&2
            return 1
        }
        if ! nicevpn_python_supported "${resolved}"; then
            version="$(nicevpn_python_version "${resolved}" 2>/dev/null || printf 'unknown')"
            printf '[NiceVPN] ERROR: 指定的 Python 版本过低：%s（%s），要求 Python >= 3.10\n' "${version}" "${resolved}" >&2
            return 1
        fi
        PYTHON_BIN="$(nicevpn_python_executable_path "${resolved}")" || return 1
        return 0
    fi

    # Prefer the project venv and an activated environment, but skip incompatible versions.
    if [[ -x "${root_dir}/.venv/bin/python" ]]; then
        candidates+=("${root_dir}/.venv/bin/python")
    elif [[ -d "${root_dir}/.venv" ]]; then
        printf '[NiceVPN] WARNING: 项目 .venv 缺少可执行的 bin/python，继续检测系统 Python。\n' >&2
    fi

    if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
        candidates+=("${VIRTUAL_ENV}/bin/python")
    fi
    if [[ -n "${CONDA_PREFIX:-}" && -x "${CONDA_PREFIX}/bin/python" ]]; then
        candidates+=("${CONDA_PREFIX}/bin/python")
    fi

    # Check versioned interpreters first. Generic python3/python may point to an old system runtime.
    candidates+=(
        python3.15
        python3.14
        python3.13
        python3.12
        python3.11
        python3.10
        python3
        python
    )

    for candidate in "${candidates[@]}"; do
        resolved="$(nicevpn_resolve_python_candidate "${candidate}" || true)"
        [[ -n "${resolved}" ]] || continue

        if nicevpn_python_supported "${resolved}"; then
            PYTHON_BIN="$(nicevpn_python_executable_path "${resolved}")" || return 1
            return 0
        fi

        version="$(nicevpn_python_version "${resolved}" 2>/dev/null || printf 'unknown')"
        printf '[NiceVPN] 跳过不兼容的 Python %s：%s（要求 >= 3.10）\n' "${version}" "${resolved}" >&2
    done

    printf '[NiceVPN] ERROR: 未找到 Python >= 3.10。可通过 PYTHON_BIN=python3.11 显式指定。\n' >&2
    return 1
}
