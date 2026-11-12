#!/usr/bin/env bash
# OpenVPN 安装公共函数（由 debian/rhel 入口脚本 source）
# 环境变量见各入口脚本头部说明；未设置时使用下列默认值。

: "${OPENVPN_MIN_VERSION:=2.7.0}"
: "${OPENVPN_TAG:=v2.7.0}"
: "${OPENVPN_ASSET:=openvpn-2.7.0.tar.gz}"
: "${OPENVPN_PREFIX:=/opt/openvpn}"
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

OPENVPN_SRC_DIR="${OPENVPN_SRC_ROOT}/${OPENVPN_ASSET%.tar.gz}"

version_ge() {
    # 用法: version_ge <ver> <min>
    [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n 1)" = "$2" ]
}

_log() {
    echo "[openvpn-install] $*"
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

openvpn_compile_install() {
    openvpn_download_source
    rm -rf "${OPENVPN_SRC_ROOT}"
    mkdir -p "${OPENVPN_SRC_ROOT}"
    tar -xzf "${OPENVPN_CACHE}" -C "${OPENVPN_SRC_ROOT}"
    if [ ! -d "${OPENVPN_SRC_DIR}" ]; then
        OPENVPN_SRC_DIR="$(find "${OPENVPN_SRC_ROOT}" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
    fi
    [ -n "${OPENVPN_SRC_DIR}" ] && [ -d "${OPENVPN_SRC_DIR}" ] || {
        echo "无法识别 OpenVPN 源码目录: ${OPENVPN_SRC_ROOT}" >&2
        exit 1
    }
    cd "${OPENVPN_SRC_DIR}"
    openvpn_configure_with_optional_features
    make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
    make install
    if [ "${OPENVPN_CREATE_SYMLINK}" = "1" ]; then
        ln -sfn "${OPENVPN_PREFIX}/sbin/openvpn" /usr/local/sbin/openvpn
    fi
    _log "OpenVPN 已编译安装到 ${OPENVPN_PREFIX}"
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
    local parts major minor
    if [ ! -f "${OPENVPN_SRC_DIR}/distro/systemd/openvpn-client@.service.in" ]; then
        _log "源码无 client systemd 模板，跳过"
        return 0
    fi
    parts="$(_parse_openvpn_version_parts "${OPENVPN_TAG}")"
    major="${parts%% *}"
    minor="${parts#* }"
    sed \
        -e "s#@sbindir@#${OPENVPN_PREFIX}/sbin#g" \
        -e "s#@OPENVPN_VERSION_MAJOR@#${major}#g" \
        -e "s#@OPENVPN_VERSION_MINOR@#${minor}#g" \
        "${OPENVPN_SRC_DIR}/distro/systemd/openvpn-client@.service.in" \
        > /etc/systemd/system/openvpn-client@.service
    systemctl daemon-reload
    _log "已安装 openvpn-client@.service"
}

openvpn_install_server_systemd_from_src() {
    [ "${INSTALL_SERVER_SYSTEMD}" = "1" ] || return 0
    local parts major minor template unit_path exec_old exec_new
    template="${OPENVPN_SRC_DIR}/distro/systemd/openvpn-server@.service.in"
    if [ ! -f "${template}" ]; then
        echo "OpenVPN 源码缺少 server systemd 模板: ${template}" >&2
        exit 1
    fi
    parts="$(_parse_openvpn_version_parts "${OPENVPN_TAG}")"
    major="${parts%% *}"
    minor="${parts#* }"
    unit_path="/etc/systemd/system/openvpn-server@.service"
    exec_old="ExecStart=${OPENVPN_PREFIX}/sbin/openvpn --status %t/openvpn-server/status-%i.log --status-version 2 --suppress-timestamps --config %i.conf"
    exec_new="ExecStart=${OPENVPN_PREFIX}/sbin/openvpn --suppress-timestamps --config %i.conf"
    sed \
        -e "s#@sbindir@#${OPENVPN_PREFIX}/sbin#g" \
        -e "s#@OPENVPN_VERSION_MAJOR@#${major}#g" \
        -e "s#@OPENVPN_VERSION_MINOR@#${minor}#g" \
        "${template}" \
        | sed "s#${exec_old}#${exec_new}#" \
        | sed 's/ProtectSystem=true/ProtectSystem=true\nReadWritePaths=\/etc\/openvpn/' \
        > "${unit_path}"
    systemctl daemon-reload
    _log "已安装 openvpn-server@.service"
}

openvpn_install_server_systemd_override() {
    [ "${INSTALL_SERVER_OVERRIDE}" = "1" ] || return 0
    local override_dir="/etc/systemd/system/openvpn-server@.service.d"
    mkdir -p "${override_dir}"
    cat > "${override_dir}/10-ovpn-mgmt.conf" <<'EOF'
[Service]
ReadWritePaths=/etc/openvpn
EOF
    systemctl daemon-reload
    _log "已安装 openvpn-server systemd override"
}

easyrsa_download_and_install() {
    [ "${INSTALL_EASYRSA}" = "1" ] || return 0
    if [ ! -s "${EASYRSA_CACHE}" ]; then
        local ok=0 url
        while IFS= read -r url; do
            [ -n "${url}" ] || continue
            _log "下载 Easy-RSA: ${url}"
            if curl -fL --retry 3 --retry-delay 2 "${url}" -o "${EASYRSA_CACHE}.part" \
                && tar -tzf "${EASYRSA_CACHE}.part" >/dev/null; then
                mv -f "${EASYRSA_CACHE}.part" "${EASYRSA_CACHE}"
                ok=1
                break
            fi
            rm -f "${EASYRSA_CACHE}.part"
        done <<EOF
${EASYRSA_DOWNLOAD_URLS}
EOF
        [ "${ok}" = "1" ] || { echo "下载 Easy-RSA 失败" >&2; exit 1; }
    else
        _log "复用 Easy-RSA 缓存: ${EASYRSA_CACHE}"
        tar -tzf "${EASYRSA_CACHE}" >/dev/null
    fi

    local tmp_dir version_name source_dir install_dir current_link
    tmp_dir="$(mktemp -d /tmp/easyrsa-install.XXXXXX)"
    tar -xzf "${EASYRSA_CACHE}" -C "${tmp_dir}"
    source_dir="$(find "${tmp_dir}" -mindepth 1 -maxdepth 2 -type d -name easyrsa3 | head -n 1)"
    if [ -z "${source_dir}" ]; then
        source_dir="$(find "${tmp_dir}" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
    fi
    version_name="${EASYRSA_TAG#v}"
    [ -n "${version_name}" ] || version_name="latest"
    install_dir="${EASYRSA_INSTALL_DIR}/releases/${version_name}"
    current_link="${EASYRSA_INSTALL_DIR}/current"
    mkdir -p "${EASYRSA_INSTALL_DIR}/releases"
    rm -rf "${install_dir}"
    cp -a "${source_dir}/." "${install_dir}/"
    rm -rf "${tmp_dir}"
    ln -sfn "${install_dir}" "${current_link}"
    ln -sfn "${install_dir}/easyrsa" /usr/local/bin/easyrsa
    _log "Easy-RSA 已安装到 ${install_dir}"
}

openvpn_post_compile_setup() {
    openvpn_install_server_systemd_from_src
    openvpn_install_client_systemd_from_src
    easyrsa_download_and_install
    openvpn_install_server_systemd_override
}
