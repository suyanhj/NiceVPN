# =============================================================================
# 本文件不是可执行脚本，不会被 script_sync 部署到服务器。
# 用途：保留历史上 weak_fingerprint（B 模式）实现，需回退时自行粘贴到 device-bind.sh。
#
# 替换范围：在 device-bind.sh 里「模式 C」的 else（整个 B 模式）中，删除从
#   「# 历史 B 模式备份」或「# ---------- 模式 B」起，到与之配对、紧挨「mkdir -p "${BINDINGS_DIR}"」
#   之前的那一个闭合 fi」为止的中间全部行，再粘贴下面某一整段（含缩进）。
#
# 注意：指纹与现网绑定 JSON 不一致会拒连，切换后一般要「重置设备绑定」。
# =============================================================================

# -----------------------------------------------------------------------------
# Legacy v1（引入 UV_UUID / 分平台安卓 之前的主流写法）
# 逻辑：IV_HWADDR；否则 IV_PLAT_VER|IV_GUI_VER；全无则仅证书放行。
# -----------------------------------------------------------------------------
    # ---------- 模式 B：弱指纹（legacy v1：IV_HWADDR → IV_PLAT_VER|IV_GUI_VER）----------
    if [ -n "${IV_HWADDR:-}" ]; then
        FINGERPRINT="${IV_HWADDR}"
        FINGERPRINT_SOURCE="IV_HWADDR"
    else
        if [ -z "${IV_PLAT_VER:-}" ] && [ -z "${IV_GUI_VER:-}" ]; then
            log "mode=weak_fingerprint CN=${CN} 无 IV_HWADDR 且无 IV_PLAT_VER/IV_GUI_VER，仅证书认证通过"
            exit 0
        fi
        FINGERPRINT="${IV_PLAT_VER:-}|${IV_GUI_VER:-}"
        FINGERPRINT_SOURCE="weak_peer_info"
    fi

# -----------------------------------------------------------------------------
# Legacy v2（变体：无 HW 时用 IV_PLAT|IV_PLAT_VER，不含 IV_GUI_VER）
# 与 v1 二选一，不要两段同时贴进 device-bind.sh。
# -----------------------------------------------------------------------------
    # ---------- 模式 B：弱指纹（legacy v2：IV_HWADDR → IV_PLAT|IV_PLAT_VER）----------
    if [ -n "${IV_HWADDR:-}" ]; then
        FINGERPRINT="${IV_HWADDR}"
        FINGERPRINT_SOURCE="IV_HWADDR"
    else
        if [ -z "${IV_PLAT_VER:-}" ]; then
            log "mode=weak_fingerprint CN=${CN} 无 IV_HWADDR 且缺 IV_PLAT_VER，仅证书认证通过"
            exit 0
        fi
        FINGERPRINT="${IV_PLAT:-}|${IV_PLAT_VER}"
        FINGERPRINT_SOURCE="weak_plat_platver"
    fi
