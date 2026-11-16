#!/usr/bin/env bash
# NiceVPN 原子文件操作函数。调用方负责提供同目录目标路径。
# 通过同目录临时文件 + rename，避免 systemd / logrotate 读取到半写入内容。

nicevpn_atomic_write_stdin() (
    set -euo pipefail
    local target="${1:?必须指定目标文件}"
    local mode="${2:-0644}"
    local dir base tmp

    dir="$(dirname -- "${target}")"
    base="$(basename -- "${target}")"
    install -d -m 0755 -- "${dir}"

    if [[ -L "${target}" ]]; then
        echo "[atomic-files] 拒绝覆盖符号链接目标: ${target}" >&2
        exit 1
    fi
    if [[ -e "${target}" && ! -f "${target}" ]]; then
        echo "[atomic-files] 目标不是普通文件: ${target}" >&2
        exit 1
    fi

    tmp="$(mktemp "${dir}/.${base}.nicevpn.XXXXXX")"
    trap 'rm -f -- "${tmp}"' EXIT
    cat > "${tmp}"
    chmod "${mode}" "${tmp}"
    mv -f -- "${tmp}" "${target}"
    trap - EXIT
)

nicevpn_atomic_write_command() (
    set -euo pipefail
    local target="${1:?必须指定目标文件}"
    local mode="${2:-0644}"
    local dir base tmp
    shift 2

    (( $# > 0 )) || {
        echo "[atomic-files] 缺少生成文件的命令" >&2
        exit 2
    }

    dir="$(dirname -- "${target}")"
    base="$(basename -- "${target}")"
    install -d -m 0755 -- "${dir}"

    if [[ -L "${target}" ]]; then
        echo "[atomic-files] 拒绝覆盖符号链接目标: ${target}" >&2
        exit 1
    fi
    if [[ -e "${target}" && ! -f "${target}" ]]; then
        echo "[atomic-files] 目标不是普通文件: ${target}" >&2
        exit 1
    fi

    tmp="$(mktemp "${dir}/.${base}.nicevpn.XXXXXX")"
    trap 'rm -f -- "${tmp}"' EXIT
    "$@" > "${tmp}"
    chmod "${mode}" "${tmp}"
    mv -f -- "${tmp}" "${target}"
    trap - EXIT
)

nicevpn_atomic_symlink() (
    set -euo pipefail
    local target="${1:?必须指定符号链接目标}"
    local link_path="${2:?必须指定符号链接路径}"
    local dir base tmp_dir

    dir="$(dirname -- "${link_path}")"
    base="$(basename -- "${link_path}")"
    install -d -m 0755 -- "${dir}"

    if [[ -e "${link_path}" && ! -L "${link_path}" ]]; then
        echo "[atomic-files] 拒绝替换非符号链接路径: ${link_path}" >&2
        exit 1
    fi

    tmp_dir="$(mktemp -d "${dir}/.${base}.nicevpn.XXXXXX")"
    trap 'rm -rf -- "${tmp_dir}"' EXIT
    ln -s -- "${target}" "${tmp_dir}/link"
    mv -Tf -- "${tmp_dir}/link" "${link_path}"
    rmdir -- "${tmp_dir}"
    trap - EXIT
)
