#!/usr/bin/env bash
# OpenVPN 源码版本升级：只发布 release、切换稳定软链接并重启原运行实例。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../common/atomic-files.sh
source "${SCRIPT_DIR}/../common/atomic-files.sh"
# shellcheck source=../common/linux-packages.sh
source "${SCRIPT_DIR}/../common/linux-packages.sh"
# shellcheck source=openvpn-install-common.sh
source "${SCRIPT_DIR}/openvpn-install-common.sh"

: "${OPENVPN_STAGE_PREFIX:?必须指定候选版本目录}"
: "${OPENVPN_EXPECTED_VERSION:?必须指定候选版本号}"
: "${OPENVPN_EXPECTED_CURRENT_BINARY:?必须指定生成候选时的当前 binary}"

INSTALL_SERVER_SYSTEMD=1
INSTALL_CLIENT_SYSTEMD=0
INSTALL_SERVER_OVERRIDE=0
OPENVPN_CREATE_SYMLINK=0

OPENVPN_ACTIVE_INSTANCES=()
old_target=""
old_binary=""
release_stage=""
switched=0

openvpn_find_binary() {
    local root="${1:?必须指定 OpenVPN 目录}"
    local candidate
    for candidate in "${root}/sbin/openvpn" "${root}/bin/openvpn"; do
        if [[ -f "${candidate}" && -x "${candidate}" ]]; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done
    return 1
}

openvpn_binary_version() {
    local binary="${1:?必须指定 OpenVPN binary}"
    "${binary}" --version 2>&1 |
        sed -n '1{s/.*OpenVPN[[:space:]]\+\([0-9][0-9.]*\).*/\1/p;}'
}

openvpn_upgrade_rollback() {
    local reason="${1:-升级失败}"
    [[ "${switched}" == "1" ]] || return 0

    echo "[openvpn-upgrade] ${reason}，切回旧版本: ${old_target}" >&2
    if ! nicevpn_atomic_symlink "${old_target}" "${OPENVPN_PREFIX}"; then
        echo "[openvpn-upgrade] 严重：旧版本软链接恢复失败，旧版本目录仍保留: ${old_target}" >&2
        return 1
    fi
    if ! openvpn_restart_and_validate_units "${old_binary}" "${OPENVPN_ACTIVE_INSTANCES[@]}"; then
        echo "[openvpn-upgrade] 严重：已切回旧版本，但原 OpenVPN 实例恢复失败" >&2
        return 1
    fi
    switched=0
    echo "[openvpn-upgrade] 已恢复旧版本并重启原运行实例" >&2
}

openvpn_upgrade_exit() {
    local status=$?
    trap - EXIT
    if [[ "${status}" != "0" && "${switched}" == "1" ]]; then
        openvpn_upgrade_rollback "升级过程异常退出" || true
    fi
    if [[ -n "${release_stage}" && -d "${release_stage}" ]]; then
        rm -rf -- "${release_stage}" || true
    fi
    exit "${status}"
}
trap openvpn_upgrade_exit EXIT

openvpn_acquire_install_lock
openvpn_restore_source_transaction
openvpn_recover_source_install

prefix_binary="$(openvpn_find_binary "${OPENVPN_PREFIX}")" || {
    echo "当前 OpenVPN 安装入口缺少可执行 binary: ${OPENVPN_PREFIX}" >&2
    exit 1
}
current_binary="$(readlink -f -- "${prefix_binary}")"
[[ "${current_binary}" == "${OPENVPN_EXPECTED_CURRENT_BINARY}" ]] || {
    echo "候选版本已过期：当前 OpenVPN binary 已变化，请重新生成候选版本" >&2
    exit 1
}

current_version="$(openvpn_binary_version "${current_binary}")"
[[ -n "${current_version}" ]] || {
    echo "无法识别当前 OpenVPN 版本: ${current_binary}" >&2
    exit 1
}

candidate_binary="$(openvpn_find_binary "${OPENVPN_STAGE_PREFIX}")" || {
    echo "候选版本目录缺少可执行 OpenVPN binary: ${OPENVPN_STAGE_PREFIX}" >&2
    exit 1
}
candidate_version="$(openvpn_binary_version "${candidate_binary}")"
[[ "${candidate_version}" == "${OPENVPN_EXPECTED_VERSION}" ]] || {
    echo "候选版本校验失败：期望 ${OPENVPN_EXPECTED_VERSION}，实际 ${candidate_version:-unknown}" >&2
    exit 1
}

openvpn_capture_active_instances
openvpn_migrate_legacy_prefix
old_target="$(readlink -f -- "${OPENVPN_PREFIX}")"
[[ -d "${old_target}" ]] || {
    echo "无法确定当前 OpenVPN release 目录: ${OPENVPN_PREFIX}" >&2
    exit 1
}
old_binary="$(openvpn_find_binary "${old_target}")" || {
    echo "当前 release 缺少可执行 OpenVPN binary: ${old_target}" >&2
    exit 1
}

mkdir -p -- "${OPENVPN_RELEASES_DIR}"
find "${OPENVPN_RELEASES_DIR}" -mindepth 1 -maxdepth 1 \
    -type d -name '.nicevpn-openvpn-upgrade.*' -exec rm -rf -- {} + 2>/dev/null || true

candidate_hash="$(sha256sum "${candidate_binary}" | awk '{print $1}')"
release_id="${candidate_version}-${candidate_hash:0:12}"
release_dir="${OPENVPN_RELEASES_DIR}/${release_id}"

if [[ -e "${release_dir}" || -L "${release_dir}" ]]; then
    [[ -d "${release_dir}" && ! -L "${release_dir}" ]] || {
        echo "OpenVPN release 目标不是普通目录，拒绝覆盖: ${release_dir}" >&2
        exit 1
    }
    existing_binary="$(openvpn_find_binary "${release_dir}")" || {
        echo "已有 OpenVPN release 缺少可执行 binary: ${release_dir}" >&2
        exit 1
    }
    existing_version="$(openvpn_binary_version "${existing_binary}")"
    existing_hash="$(sha256sum "${existing_binary}" | awk '{print $1}')"
    [[ "${existing_version}" == "${candidate_version}" && "${existing_hash}" == "${candidate_hash}" ]] || {
        echo "已有 OpenVPN release 校验不匹配，拒绝复用: ${release_dir}" >&2
        exit 1
    }
    echo "[openvpn-upgrade] 复用已校验版本目录: ${release_dir}"
else
    release_stage="$(mktemp -d "${OPENVPN_RELEASES_DIR}/.nicevpn-openvpn-upgrade.XXXXXX")"
    cp -a "${OPENVPN_STAGE_PREFIX}/." "${release_stage}/"
    staged_binary="$(openvpn_find_binary "${release_stage}")" || {
        echo "release staging 缺少 OpenVPN binary: ${release_stage}" >&2
        exit 1
    }
    staged_hash="$(sha256sum "${staged_binary}" | awk '{print $1}')"
    staged_version="$(openvpn_binary_version "${staged_binary}")"
    [[ "${staged_version}" == "${candidate_version}" && "${staged_hash}" == "${candidate_hash}" ]] || {
        echo "release staging 校验失败: ${release_stage}" >&2
        exit 1
    }
    mv -T -- "${release_stage}" "${release_dir}"
    release_stage=""
    echo "[openvpn-upgrade] 已发布版本目录: ${release_dir}"
fi

if [[ "${old_target}" == "${release_dir}" ]]; then
    echo "[openvpn-upgrade] 当前已是目标版本，无需切换"
    exit 0
fi

echo "[openvpn-upgrade] 原子切换稳定入口: ${OPENVPN_PREFIX} -> ${release_dir}"
switched=1
nicevpn_atomic_symlink "${release_dir}" "${OPENVPN_PREFIX}"

active_binary="$(openvpn_find_binary "${OPENVPN_PREFIX}")"
active_version="$(openvpn_binary_version "${active_binary}")"
[[ "${active_version}" == "${OPENVPN_EXPECTED_VERSION}" ]] || {
    echo "稳定入口切换后版本校验失败: ${active_version:-unknown}" >&2
    exit 1
}

openvpn_restart_and_validate_units "${active_binary}" "${OPENVPN_ACTIVE_INSTANCES[@]}"

switched=0
echo "[openvpn-upgrade] OpenVPN ${candidate_version} 升级成功"
echo "[openvpn-upgrade] 当前 release: ${release_dir}"
echo "[openvpn-upgrade] 上一版本保留: ${old_target}"
