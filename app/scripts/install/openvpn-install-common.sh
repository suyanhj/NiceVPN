#!/usr/bin/env bash
# OpenVPN 安装公共函数（由 debian/rhel 入口脚本 source）
# 环境变量见各入口脚本头部说明；未设置时使用下列默认值。

: "${OPENVPN_MIN_VERSION:=2.7.0}"
: "${OPENVPN_TAG:=v2.7.0}"
: "${OPENVPN_ASSET:=openvpn-2.7.0.tar.gz}"
: "${OPENVPN_PREFIX:=/opt/openvpn}"
: "${OPENVPN_SBIN_DIR:=${OPENVPN_PREFIX}/sbin}"
: "${OPENVPN_BIN:=${OPENVPN_SBIN_DIR}/openvpn}"
: "${OPENVPN_CACHE:=/tmp/${OPENVPN_ASSET}}"
: "${OPENVPN_SRC_ROOT:=/tmp/ovpn-mgmt-openvpn-src}"
: "${EASYRSA_INSTALL_DIR:=/opt/easy-rsa}"
: "${EASYRSA_TAG:=v3.2.4}"
: "${EASYRSA_ASSET:=EasyRSA-3.2.4.tgz}"
: "${EASYRSA_CACHE:=/tmp/${EASYRSA_ASSET}}"
: "${INSTALL_EASYRSA:=0}"
: "${INSTALL_SERVER_SYSTEMD:=0}"
: "${INSTALL_CLIENT_SYSTEMD:=0}"
: "${INSTALL_SERVER_OVERRIDE:=0}"
: "${OPENVPN_CREATE_SYMLINK:=1}"
: "${OPENVPN_FORCE_SOURCE:=0}"
: "${OPENVPN_DCO_MIN_LIBNL_VERSION:=3.4.0}"
: "${EASYRSA_FORCE_INSTALL:=0}"

: "${OPENVPN_SRC_DIR:=${OPENVPN_SRC_ROOT}/${OPENVPN_ASSET%.tar.gz}}"

version_ge() {
    # 用法: version_ge <ver> <min>
    [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n 1)" = "$2" ]
}

_log() {
    echo "[openvpn-install] $*"
}

# 返回系统中已有且可执行的 EasyRSA，不限定 NiceVPN 自己的安装目录。
easyrsa_find_existing_binary() {
    local candidate
    local -a candidates=(
        "${EASYRSA_INSTALL_DIR}/current/easyrsa"
        "${EASYRSA_INSTALL_DIR}/easyrsa"
        "${OPENVPN_PREFIX}/share/easy-rsa/easyrsa"
        "${OPENVPN_PREFIX}/share/easy-rsa/3/easyrsa"
        "/usr/share/easy-rsa/easyrsa"
        "/usr/share/easy-rsa/3/easyrsa"
        "/usr/share/easy-rsa/3.0/easyrsa"
        "/usr/local/share/easy-rsa/easyrsa"
    )
    for candidate in "${candidates[@]}"; do
        if [[ -f "${candidate}" && -x "${candidate}" ]]; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done

    candidate="$(command -v easyrsa 2>/dev/null || true)"
    if [[ -n "${candidate}" && -f "${candidate}" && -x "${candidate}" ]]; then
        printf '%s\n' "${candidate}"
        return 0
    fi
    return 1
}

# 已有 EasyRSA 默认复用；只有显式 EASYRSA_FORCE_INSTALL=1 才允许覆盖当前安装入口。
easyrsa_reuse_existing() {
    [[ "${INSTALL_EASYRSA}" == "1" ]] || return 0
    [[ "${EASYRSA_FORCE_INSTALL}" == "1" ]] && return 0

    local existing_bin
    if existing_bin="$(easyrsa_find_existing_binary)"; then
        _log "检测到已有 EasyRSA: ${existing_bin}，跳过下载、安装和覆盖"
        INSTALL_EASYRSA=0
    fi
}

# DCO 是可选性能优化：libnl-genl 不满足要求时，不应阻断 OpenVPN 基础安装。
openvpn_detect_dco_support() {
    local pkg_config_bin="${PKG_CONFIG:-pkg-config}"
    local libnl_version=""

    if ! command -v "${pkg_config_bin}" >/dev/null 2>&1; then
        _log "可选功能 DCO：未找到 ${pkg_config_bin}，编译时禁用 DCO"
        return 1
    fi

    if ! libnl_version="$("${pkg_config_bin}" --modversion libnl-genl-3.0 2>/dev/null)" \
        || [ -z "${libnl_version}" ]; then
        _log "可选功能 DCO：未找到 libnl-genl-3.0 开发包，编译时禁用 DCO"
        return 1
    fi

    if ! version_ge "${libnl_version}" "${OPENVPN_DCO_MIN_LIBNL_VERSION}"; then
        _log "可选功能 DCO：libnl-genl-3.0 ${libnl_version} 低于要求 ${OPENVPN_DCO_MIN_LIBNL_VERSION}，编译时禁用 DCO"
        return 1
    fi

    _log "可选功能 DCO：libnl-genl-3.0 ${libnl_version} 满足要求，保留 DCO 编译支持"
    return 0
}

openvpn_configure_with_optional_features() {
    local -a configure_args=(
        "--prefix=${OPENVPN_PREFIX}"
        --enable-async-push
        --enable-systemd
    )
    local configure_status=0

    if openvpn_detect_dco_support; then
        if ./configure "${configure_args[@]}"; then
            return 0
        else
            configure_status=$?
        fi

        # 处理预检与 configure 实际检测结果不一致的情况，只对明确的 DCO 依赖错误回退。
        if [ -f config.log ] && grep -Eqi \
            'libnl-genl-3\.0 package not found or too old|3\.4\.0 or newer for DCO' config.log; then
            _log "警告：configure 检测到 DCO 依赖不可用，自动禁用 DCO 后重试"
            ./configure "${configure_args[@]}" --disable-dco
            return $?
        fi
        return "${configure_status}"
    fi

    _log "继续编译 OpenVPN 基础功能（DCO 已禁用）"
    ./configure "${configure_args[@]}" --disable-dco
}

openvpn_download_source() {
    # 需要 OPENVPN_DOWNLOAD_URLS（换行分隔的候选 URL 列表）
    if [ -s "${OPENVPN_CACHE}" ] && ! tar -tzf "${OPENVPN_CACHE}" >/dev/null 2>&1; then
        _log "OpenVPN 源码缓存校验失败，删除后重新下载: ${OPENVPN_CACHE}"
        rm -f -- "${OPENVPN_CACHE}"
    fi
    if [ ! -s "${OPENVPN_CACHE}" ]; then
        local ok=0 url
        while IFS= read -r url; do
            [ -n "${url}" ] || continue
            _log "下载 OpenVPN 源码包: ${url}"
            if curl -fL --retry 3 --retry-delay 2 "${url}" -o "${OPENVPN_CACHE}.part" \
                && tar -tzf "${OPENVPN_CACHE}.part" >/dev/null; then
                mv -f "${OPENVPN_CACHE}.part" "${OPENVPN_CACHE}"
                ok=1
                break
            fi
            rm -f "${OPENVPN_CACHE}.part"
        done <<EOF
${OPENVPN_DOWNLOAD_URLS}
EOF
        [ "${ok}" = "1" ] || { echo "下载 OpenVPN 源码包失败" >&2; exit 1; }
    else
        _log "复用 OpenVPN 源码缓存: ${OPENVPN_CACHE}"
        tar -tzf "${OPENVPN_CACHE}" >/dev/null
    fi
}

# 如果上次在 prefix 备份切换后被 SIGKILL，先恢复最近的完整旧版本；
# 未发布的 stage 目录只会在持有安装锁时清理。
openvpn_recover_source_install() {
    local prefix_parent prefix_base backup_root candidate candidate_mtime
    local latest_backup="" latest_backup_root="" latest_mtime=0 stage_root
    prefix_parent="$(dirname -- "${OPENVPN_PREFIX}")"
    prefix_base="$(basename -- "${OPENVPN_PREFIX}")"
    mkdir -p -- "${prefix_parent}"

    if [ ! -e "${OPENVPN_PREFIX}" ] && [ ! -L "${OPENVPN_PREFIX}" ]; then
        # 兼容 upgrade.py 的 old_tree 和 Shell 安装流程自己的 backup 目录。
        for backup_root in "${prefix_parent}/${prefix_base}.nicevpn-old-"*; do
            [ -d "${backup_root}" ] || [ -L "${backup_root}" ] || continue
            candidate="${backup_root}"
            candidate_mtime="$(stat -c %Z "${candidate}" 2>/dev/null || echo 0)"
            if [ "${candidate_mtime}" -ge "${latest_mtime}" ]; then
                latest_mtime="${candidate_mtime}"
                latest_backup="${candidate}"
                latest_backup_root=""
            fi
        done
        for backup_root in "${prefix_parent}/.${prefix_base}.backup."*; do
            candidate="${backup_root}/${prefix_base}"
            [ -d "${candidate}" ] && [ ! -L "${candidate}" ] || continue
            candidate_mtime="$(stat -c %Z "${candidate}" 2>/dev/null || echo 0)"
            if [ "${candidate_mtime}" -ge "${latest_mtime}" ]; then
                latest_mtime="${candidate_mtime}"
                latest_backup="${candidate}"
                latest_backup_root="${backup_root}"
            fi
        done
        if [ -n "${latest_backup}" ]; then
            mv -T -- "${latest_backup}" "${OPENVPN_PREFIX}"
            if [ -n "${latest_backup_root}" ]; then
                rmdir -- "${latest_backup_root}" 2>/dev/null || true
            fi
            _log "检测到上次 OpenVPN prefix 切换中断，已恢复旧版本: ${OPENVPN_PREFIX}"
        fi
    fi

    for stage_root in "${prefix_parent}/.${prefix_base}.stage."*; do
        [ -d "${stage_root}" ] || continue
        rm -rf -- "${stage_root}"
    done
}

openvpn_compile_install() {
    local prefix_parent prefix_base stage_root staged_prefix staged_bin
    local version_output version expected_version backup_root backup_prefix
    OPENVPN_PREFIX="${OPENVPN_PREFIX%/}"
    case "${OPENVPN_PREFIX}" in
        /*) ;;
        *)
            echo "OPENVPN_PREFIX 必须是绝对路径: ${OPENVPN_PREFIX}" >&2
            return 1
            ;;
    esac
    [ "${OPENVPN_PREFIX}" != "/" ] || {
        echo "OPENVPN_PREFIX 不能是根目录" >&2
        return 1
    }
    case "${OPENVPN_BIN}" in
        "${OPENVPN_PREFIX}/"*) ;;
        *)
            echo "OPENVPN_BIN 必须位于 OPENVPN_PREFIX 内，无法安全进行目录切换: ${OPENVPN_BIN}" >&2
            return 1
            ;;
    esac
    openvpn_recover_source_install
    # 恢复旧 prefix 优先于网络下载，确保中断恢复不依赖下载源可用。
    openvpn_download_source

    prefix_parent="$(dirname -- "${OPENVPN_PREFIX}")"
    prefix_base="$(basename -- "${OPENVPN_PREFIX}")"
    mkdir -p -- "${prefix_parent}"
    stage_root="$(mktemp -d "${prefix_parent}/.${prefix_base}.stage.XXXXXX")"
    staged_prefix="${stage_root}${OPENVPN_PREFIX}"
    staged_bin="${stage_root}${OPENVPN_BIN}"
    backup_root=""
    backup_prefix=""
    OPENVPN_LAST_BACKUP_PREFIX=""

    rm -rf -- "${OPENVPN_SRC_ROOT}"
    mkdir -p -- "${OPENVPN_SRC_ROOT}"
    tar -xzf "${OPENVPN_CACHE}" -C "${OPENVPN_SRC_ROOT}"
    if [ ! -d "${OPENVPN_SRC_DIR}" ]; then
        OPENVPN_SRC_DIR="$(find "${OPENVPN_SRC_ROOT}" -mindepth 1 -maxdepth 1 -type d -print -quit)"
    fi
    [ -n "${OPENVPN_SRC_DIR}" ] && [ -d "${OPENVPN_SRC_DIR}" ] || {
        echo "无法识别 OpenVPN 源码目录: ${OPENVPN_SRC_ROOT}" >&2
        rm -rf -- "${stage_root}"
        return 1
    }

    cd "${OPENVPN_SRC_DIR}"
    openvpn_configure_with_optional_features
    make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
    # DESTDIR 把所有安装产物写入同一文件系统的 stage；不直接覆盖正在使用的 prefix。
    make install DESTDIR="${stage_root}"

    [ -f "${staged_bin}" ] && [ -x "${staged_bin}" ] || {
        echo "OpenVPN staged binary 不存在或不可执行: ${staged_bin}" >&2
        rm -rf -- "${stage_root}"
        return 1
    }
    version_output="$("${staged_bin}" --version 2>&1)" || {
        echo "OpenVPN staged binary 无法执行 --version: ${staged_bin}" >&2
        rm -rf -- "${stage_root}"
        return 1
    }
    version="$(printf '%s\n' "${version_output}" | sed -n '1{s/.*OpenVPN[[:space:]]\+\([0-9][0-9.]*\).*/\1/p;}')"
    expected_version="${OPENVPN_TAG#v}"
    if [ -z "${version}" ] || [ "${version}" != "${expected_version}" ]; then
        echo "OpenVPN staged version 校验失败：期望 ${expected_version}，实际 ${version:-unknown}" >&2
        rm -rf -- "${stage_root}"
        return 1
    fi

    if [ -e "${OPENVPN_PREFIX}" ] || [ -L "${OPENVPN_PREFIX}" ]; then
        [ -d "${OPENVPN_PREFIX}" ] && [ ! -L "${OPENVPN_PREFIX}" ] || {
            echo "现有 OPENVPN_PREFIX 不是普通目录，拒绝替换: ${OPENVPN_PREFIX}" >&2
            rm -rf -- "${stage_root}"
            return 1
        }
        backup_root="${prefix_parent}/.${prefix_base}.backup.$(date +%s).$$"
        if ! mkdir -m 0700 -- "${backup_root}"; then
            echo "无法创建 OpenVPN 旧版本备份目录: ${backup_root}" >&2
            rm -rf -- "${stage_root}"
            return 1
        fi
        backup_prefix="${backup_root}/${prefix_base}"
        if ! mv -T -- "${OPENVPN_PREFIX}" "${backup_prefix}"; then
            rmdir -- "${backup_root}" 2>/dev/null || true
            echo "无法备份现有 OpenVPN prefix，未切换新版本" >&2
            rm -rf -- "${stage_root}"
            return 1
        fi
    fi

    if ! mv -T -- "${staged_prefix}" "${OPENVPN_PREFIX}"; then
        if [ -n "${backup_prefix}" ] && [ -d "${backup_prefix}" ]; then
            if mv -T -- "${backup_prefix}" "${OPENVPN_PREFIX}"; then
                rmdir -- "${backup_root}" 2>/dev/null || true
            else
                _log "严重：新 prefix 部署失败且旧版本恢复失败，旧版本保留在 ${backup_prefix}"
            fi
        fi
        echo "OpenVPN prefix 原子切换失败: ${OPENVPN_PREFIX}" >&2
        rm -rf -- "${stage_root}"
        return 1
    fi

    OPENVPN_LAST_BACKUP_PREFIX="${backup_prefix}"
    rm -rf -- "${stage_root}"

    if [ "${OPENVPN_CREATE_SYMLINK}" = "1" ]; then
        if ! nicevpn_atomic_symlink "${OPENVPN_BIN}" /usr/local/sbin/openvpn; then
            _log "警告：无法安全更新 /usr/local/sbin/openvpn（可能存在非符号链接文件），保留现状"
        fi
    fi
    if [ -n "${OPENVPN_LAST_BACKUP_PREFIX}" ]; then
        _log "OpenVPN ${version} 已原子部署到 ${OPENVPN_PREFIX}，旧版本保留在 ${OPENVPN_LAST_BACKUP_PREFIX}"
    else
        _log "OpenVPN ${version} 已原子部署到 ${OPENVPN_PREFIX}"
    fi
}
_parse_openvpn_version_parts() {
    local raw="${1#v}"
    local major minor
    major="${raw%%.*}"
    raw="${raw#*.}"
    minor="${raw%%.*}"
    if [[ "${major}" =~ ^[0-9]+$ && "${minor}" =~ ^[0-9]+$ ]]; then
        echo "${major} ${minor}"
    else
        echo "2 6"
    fi
}

openvpn_install_client_systemd_from_src() {
    [ "${INSTALL_CLIENT_SYSTEMD}" = "1" ] || return 0
    local parts major minor template unit_path
    template="${OPENVPN_SRC_DIR}/distro/systemd/openvpn-client@.service.in"
    if [ ! -f "${template}" ]; then
        _log "源码无 client systemd 模板，跳过"
        return 0
    fi
    parts="$(_parse_openvpn_version_parts "${OPENVPN_TAG}")"
    major="${parts%% *}"
    minor="${parts#* }"
    unit_path="/etc/systemd/system/openvpn-client@.service"
    nicevpn_atomic_write_command "${unit_path}" 0644 sed \
        -e "s#@sbindir@#${OPENVPN_SBIN_DIR}#g" \
        -e "s#@OPENVPN_VERSION_MAJOR@#${major}#g" \
        -e "s#@OPENVPN_VERSION_MINOR@#${minor}#g" \
        "${template}"
    systemctl daemon-reload
    _log "已原子安装 openvpn-client@.service"
}

openvpn_install_server_systemd_from_src() {
    [ "${INSTALL_SERVER_SYSTEMD}" = "1" ] || return 0
    local parts major minor template unit_path exec_old exec_new
    template="${OPENVPN_SRC_DIR}/distro/systemd/openvpn-server@.service.in"
    if [ ! -f "${template}" ]; then
        echo "OpenVPN 源码缺少 server systemd 模板: ${template}" >&2
        return 1
    fi
    parts="$(_parse_openvpn_version_parts "${OPENVPN_TAG}")"
    major="${parts%% *}"
    minor="${parts#* }"
    unit_path="/etc/systemd/system/openvpn-server@.service"
    exec_old="ExecStart=${OPENVPN_SBIN_DIR}/openvpn --status %t/openvpn-server/status-%i.log --status-version 2 --suppress-timestamps --config %i.conf"
    exec_new="ExecStart=${OPENVPN_BIN} --suppress-timestamps --config %i.conf"
    nicevpn_atomic_write_command "${unit_path}" 0644 sed \
        -e "s#@sbindir@#${OPENVPN_SBIN_DIR}#g" \
        -e "s#@OPENVPN_VERSION_MAJOR@#${major}#g" \
        -e "s#@OPENVPN_VERSION_MINOR@#${minor}#g" \
        -e "s#${exec_old}#${exec_new}#" \
        -e 's/ProtectSystem=true/ProtectSystem=true\nReadWritePaths=\/etc\/openvpn/' \
        "${template}"
    systemctl daemon-reload
    _log "已原子安装 openvpn-server@.service"
}

openvpn_install_server_systemd_override() {
    [ "${INSTALL_SERVER_OVERRIDE}" = "1" ] || return 0
    local override_dir="/etc/systemd/system/openvpn-server@.service.d"
    nicevpn_atomic_write_stdin "${override_dir}/10-ovpn-mgmt.conf" 0644 <<'EOF'
[Service]
ReadWritePaths=/etc/openvpn
EOF
    systemctl daemon-reload
    _log "已原子安装 openvpn-server systemd override"
}

easyrsa_binary_version() {
    local binary="${1:?必须指定 EasyRSA 可执行脚本}"
    local output
    output="$("${binary}" --version 2>&1)" || return 1
    printf '%s\n' "${output}" | grep -Eo '[0-9]+(\.[0-9]+){1,2}' | sed -n '1p'
}

easyrsa_validate_binary() {
    local binary="${1:?必须指定 EasyRSA 可执行脚本}"
    local expected_version="${2:-}"
    local actual_version
    [ -f "${binary}" ] && [ -x "${binary}" ] || {
        echo "EasyRSA 脚本不存在或不可执行: ${binary}" >&2
        return 1
    }
    actual_version="$(easyrsa_binary_version "${binary}" || true)"
    [ -n "${actual_version}" ] || {
        echo "无法读取 EasyRSA 版本: ${binary}" >&2
        return 1
    }
    if [ -n "${expected_version}" ] && [ "${actual_version}" != "${expected_version}" ]; then
        echo "EasyRSA 版本不匹配：期望 ${expected_version}，实际 ${actual_version}" >&2
        return 1
    fi
    printf '%s\n' "${actual_version}"
}

# 使用不可变版本目录；先完整解压、校验，再原子切换 current。
# 旧版本不覆盖、不提前删除，更新中断时 current 始终指向原版本。
easyrsa_download_and_install() (
    easyrsa_reuse_existing
    [ "${INSTALL_EASYRSA}" = "1" ] || exit 0
    : "${EASYRSA_DOWNLOAD_URLS:?必须设置 EASYRSA_DOWNLOAD_URLS}"

    nicevpn_ensure_commands flock util-linux
    mkdir -p "${EASYRSA_INSTALL_DIR}/releases"
    exec 9>"${EASYRSA_INSTALL_DIR}/.install.lock"
    if ! flock -n 9; then
        echo "已有 EasyRSA 安装/升级任务正在执行，请稍后重试" >&2
        exit 1
    fi

    # 上次被 SIGKILL 中断时只可能留下未发布的临时目录；current 不受影响。
    find "${EASYRSA_INSTALL_DIR}/releases" -mindepth 1 -maxdepth 1 \
        -type d -name '.*.stage.*' -exec rm -rf -- {} + 2>/dev/null || true
    find "${EASYRSA_INSTALL_DIR}" -mindepth 1 -maxdepth 1 \
        -type d -name '.extract.*' -exec rm -rf -- {} + 2>/dev/null || true

    if [ -s "${EASYRSA_CACHE}" ] && ! tar -tzf "${EASYRSA_CACHE}" >/dev/null 2>&1; then
        _log "EasyRSA 下载缓存校验失败，删除后重新下载: ${EASYRSA_CACHE}"
        rm -f -- "${EASYRSA_CACHE}"
    fi

    if [ ! -s "${EASYRSA_CACHE}" ]; then
        local ok=0 url
        rm -f -- "${EASYRSA_CACHE}.part"
        while IFS= read -r url; do
            [ -n "${url}" ] || continue
            _log "下载 Easy-RSA: ${url}"
            if curl -fL --retry 3 --retry-delay 2 "${url}" -o "${EASYRSA_CACHE}.part" \
                && tar -tzf "${EASYRSA_CACHE}.part" >/dev/null; then
                if mv -f -- "${EASYRSA_CACHE}.part" "${EASYRSA_CACHE}"; then
                    ok=1
                    break
                fi
            fi
            rm -f -- "${EASYRSA_CACHE}.part"
        done <<EOF
${EASYRSA_DOWNLOAD_URLS}
EOF
        [ "${ok}" = "1" ] || { echo "下载 Easy-RSA 失败" >&2; exit 1; }
    else
        _log "复用已校验的 Easy-RSA 缓存: ${EASYRSA_CACHE}"
    fi

    local tmp_dir stage_dir source_dir expected_version actual_version archive_hash
    local release_id install_dir current_link old_current
    tmp_dir="$(mktemp -d "${EASYRSA_INSTALL_DIR}/.extract.XXXXXX")"
    stage_dir=""
    cleanup_easyrsa_install() {
        rm -rf -- "${tmp_dir}"
        if [ -n "${stage_dir}" ]; then
            rm -rf -- "${stage_dir}"
        fi
    }
    trap cleanup_easyrsa_install EXIT

    tar -xzf "${EASYRSA_CACHE}" -C "${tmp_dir}"
    source_dir="$(find "${tmp_dir}" -mindepth 1 -maxdepth 2 -type d -name easyrsa3 -print -quit)"
    if [ -z "${source_dir}" ]; then
        source_dir="$(find "${tmp_dir}" -mindepth 1 -maxdepth 1 -type d -print -quit)"
    fi
    [ -n "${source_dir}" ] && [ -f "${source_dir}/easyrsa" ] || {
        echo "无法识别 EasyRSA 解压目录或缺少 easyrsa 脚本: ${tmp_dir}" >&2
        exit 1
    }

    expected_version="${EASYRSA_TAG#v}"
    chmod +x "${source_dir}/easyrsa"
    actual_version="$(easyrsa_validate_binary "${source_dir}/easyrsa" "${expected_version}")"
    archive_hash="$(sha256sum "${EASYRSA_CACHE}" | awk '{print $1}')"
    release_id="${actual_version}-${archive_hash:0:12}"
    install_dir="${EASYRSA_INSTALL_DIR}/releases/${release_id}"
    current_link="${EASYRSA_INSTALL_DIR}/current"

    if [ -e "${install_dir}" ] || [ -L "${install_dir}" ]; then
        [ -d "${install_dir}" ] && [ ! -L "${install_dir}" ] || {
            echo "EasyRSA 版本目录不是普通目录，拒绝覆盖: ${install_dir}" >&2
            exit 1
        }
        local existing_version
        existing_version="$(easyrsa_validate_binary "${install_dir}/easyrsa" "${expected_version}")"
        _log "复用已校验的不可变 EasyRSA 版本目录: ${install_dir} (${existing_version})"
    else
        stage_dir="$(mktemp -d "${EASYRSA_INSTALL_DIR}/releases/.${release_id}.stage.XXXXXX")"
        cp -a "${source_dir}/." "${stage_dir}/"
        chmod +x "${stage_dir}/easyrsa"
        easyrsa_validate_binary "${stage_dir}/easyrsa" "${expected_version}" >/dev/null
        # stage 与 releases 位于同一文件系统，rename 后目录一次性可见。
        mv -T -- "${stage_dir}" "${install_dir}"
        stage_dir=""
    fi

    old_current="$(readlink "${current_link}" 2>/dev/null || true)"
    nicevpn_atomic_symlink "${install_dir}" "${current_link}"
    if ! nicevpn_atomic_symlink "${current_link}/easyrsa" /usr/local/bin/easyrsa; then
        _log "警告：无法更新 /usr/local/bin/easyrsa；NiceVPN 将优先使用 ${current_link}/easyrsa"
    fi

    _log "EasyRSA 已原子切换到 ${install_dir}，版本=${actual_version}，旧 current=${old_current:-无}"
)
openvpn_post_compile_setup() {
    openvpn_install_server_systemd_from_src || return 1
    openvpn_install_client_systemd_from_src || return 1
    easyrsa_download_and_install || return 1
    openvpn_install_server_systemd_override || return 1
}

OPENVPN_ACTIVE_INSTANCES=()
OPENVPN_LAST_BACKUP_PREFIX=""

# 只有源码 prefix 存在旧版本备份时才回滚；新装和系统包安装不会误删文件。
openvpn_rollback_previous_prefix() {
    local reason="${1:-安装后校验失败}"
    local backup_prefix="${OPENVPN_LAST_BACKUP_PREFIX:-}"
    local failed_prefix
    [ -n "${backup_prefix}" ] && [ -d "${backup_prefix}" ] || return 0

    failed_prefix="${OPENVPN_PREFIX}.failed.$(date +%s).${BASHPID}"
    if [ -e "${OPENVPN_PREFIX}" ] || [ -L "${OPENVPN_PREFIX}" ]; then
        if ! mv -T -- "${OPENVPN_PREFIX}" "${failed_prefix}"; then
            _log "无法暂存失败的新 OpenVPN prefix，旧版本仍保留在 ${backup_prefix}"
            return 1
        fi
    fi

    if mv -T -- "${backup_prefix}" "${OPENVPN_PREFIX}"; then
        rmdir -- "$(dirname -- "${backup_prefix}")" 2>/dev/null || true
        OPENVPN_LAST_BACKUP_PREFIX=""
        _log "${reason}，已恢复旧 OpenVPN prefix；失败版本保留在 ${failed_prefix}"
        return 0
    fi

    if [ -d "${failed_prefix}" ] && [ ! -e "${OPENVPN_PREFIX}" ]; then
        mv -T -- "${failed_prefix}" "${OPENVPN_PREFIX}" || true
    fi
    _log "旧 OpenVPN prefix 恢复失败，备份仍保留在 ${backup_prefix}"
    return 1
}

openvpn_acquire_install_lock() {
    nicevpn_ensure_commands flock util-linux
    mkdir -p /run/lock
    exec 9>/run/lock/nicevpn-install.lock
    if ! flock -n 9; then
        echo "已有 NiceVPN/OpenVPN 安装任务正在执行，请稍后重试" >&2
        return 1
    fi
}

openvpn_capture_active_instances() {
    OPENVPN_ACTIVE_INSTANCES=()
    if [ "${INSTALL_SERVER_SYSTEMD}" != "1" ] && \
        [ "${INSTALL_CLIENT_SYSTEMD}" != "1" ] && \
        [ "${INSTALL_SERVER_OVERRIDE}" != "1" ]; then
        return 0
    fi
    if ! command -v systemctl >/dev/null 2>&1; then
        return 0
    fi
    local output unit
    output="$(systemctl list-units --type=service --state=active --no-legend --no-pager 2>/dev/null || true)"
    while IFS= read -r unit; do
        case "${unit}" in
            openvpn-server@*.service|openvpn-client@*.service|openvpn@*.service|openvpn-client.service)
                OPENVPN_ACTIVE_INSTANCES+=("${unit}")
                ;;
        esac
    done < <(awk 'NF {print $1}' <<< "${output}")
}

openvpn_validate_systemd_unit() {
    local unit="${1:?必须指定 systemd unit}"
    local output unit_state
    command -v systemctl >/dev/null 2>&1 || {
        echo "未找到 systemctl，无法验证服务单元: ${unit}" >&2
        return 1
    }
    output="$(systemctl list-unit-files --type=service --no-legend --no-pager 2>/dev/null)" || {
        echo "systemctl 无法读取服务单元列表: ${unit}" >&2
        return 1
    }
    unit_state="$(awk -v unit="${unit}" '$1 == unit { print $2; exit }' <<< "${output}")"
    if [ -z "${unit_state}" ] || [ "${unit_state}" = "masked" ]; then
        echo "systemd 服务单元不存在或已屏蔽: ${unit} state=${unit_state:-not-found}" >&2
        return 1
    fi
    return 0
}

_openvpn_post_install_validate_impl() {
    local candidate="" binary="" version_output="" version="" expected_version="" unit state
    for candidate in "${OPENVPN_BIN}" "${OPENVPN_PREFIX}/bin/openvpn" \
        /usr/local/sbin/openvpn /usr/local/bin/openvpn /usr/sbin/openvpn /usr/bin/openvpn; do
        if [ -f "${candidate}" ] && [ -x "${candidate}" ]; then
            binary="${candidate}"
            break
        fi
    done
    if [ -z "${binary}" ]; then
        candidate="$(command -v openvpn 2>/dev/null || true)"
        if [ -n "${candidate}" ] && [ -f "${candidate}" ] && [ -x "${candidate}" ]; then
            binary="${candidate}"
        fi
    fi
    [ -n "${binary}" ] || {
        echo "OpenVPN 安装后未找到可执行二进制" >&2
        return 1
    }

    version_output="$("${binary}" --version 2>&1)" || {
        echo "OpenVPN 二进制无法执行 --version: ${binary}" >&2
        return 1
    }
    version="$(printf '%s\n' "${version_output}" | sed -n '1{s/.*OpenVPN[[:space:]]\+\([0-9][0-9.]*\).*/\1/p;}')"
    [ -n "${version}" ] || {
        echo "无法识别 OpenVPN 二进制版本: ${binary}" >&2
        return 1
    }
    version_ge "${version}" "${OPENVPN_MIN_VERSION}" || {
        echo "OpenVPN 安装后版本不满足要求：${version} < ${OPENVPN_MIN_VERSION}，binary=${binary}" >&2
        return 1
    }
    if [ "${OPENVPN_FORCE_SOURCE}" = "1" ]; then
        expected_version="${OPENVPN_TAG#v}"
        [ "${version}" = "${expected_version}" ] || {
            echo "OpenVPN 源码安装版本不匹配：期望 ${expected_version}，实际 ${version}" >&2
            return 1
        }
    fi

    if [ "${INSTALL_SERVER_SYSTEMD}" = "1" ] || \
        [ "${INSTALL_CLIENT_SYSTEMD}" = "1" ] || \
        [ "${INSTALL_SERVER_OVERRIDE}" = "1" ]; then
        systemctl daemon-reload || {
            echo "systemd daemon-reload 失败，OpenVPN 安装后校验中止" >&2
            return 1
        }
    fi
    if [ "${INSTALL_SERVER_SYSTEMD}" = "1" ]; then
        openvpn_validate_systemd_unit "openvpn-server@.service" || return 1
    fi
    if [ "${INSTALL_CLIENT_SYSTEMD}" = "1" ]; then
        openvpn_validate_systemd_unit "openvpn-client@.service" || return 1
    fi
    if [ "${INSTALL_SERVER_OVERRIDE}" = "1" ]; then
        local override_file="/etc/systemd/system/openvpn-server@.service.d/10-ovpn-mgmt.conf"
        if [ ! -f "${override_file}" ] || ! grep -Fq 'ReadWritePaths=/etc/openvpn' "${override_file}"; then
            echo "OpenVPN server systemd override 安装后不存在或内容不完整" >&2
            return 1
        fi
    fi

    for unit in "${OPENVPN_ACTIVE_INSTANCES[@]}"; do
        state="$(systemctl is-active "${unit}" 2>/dev/null || true)"
        if [ "${state}" != "active" ]; then
            echo "安装后原运行中的 OpenVPN 服务未保持 active: ${unit} state=${state:-unknown}" >&2
            return 1
        fi
    done

    _log "安装后校验通过: binary=${binary} version=${version} active_instances=${#OPENVPN_ACTIVE_INSTANCES[@]}"
}

openvpn_post_install_validate() {
    if _openvpn_post_install_validate_impl; then
        return 0
    fi
    openvpn_rollback_previous_prefix "OpenVPN 安装后校验失败" || true
    return 1
}
