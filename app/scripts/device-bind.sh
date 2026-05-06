#!/bin/bash
# ============================================================
# OpenVPN client-connect 设备指纹绑定脚本
# 策略由管理系统写入 mgmt/device_bind_mode（weak_log / weak_fingerprint / strict_hwaddr）
#
# weak_log：仅记录 peer 信息，不拒绝、不写绑定 JSON（无绑定文件则管理端无法展示 IV_PLAT）
# weak_fingerprint：第一级全体看 IV_HWADDR；第二级退化——iOS/Mac/Win 用 UV_UUID，安卓用 IV_PLAT|IV_PLAT_VER；
#   其它平台回退 IV_PLAT_VER|IV_GUI_VER。升级脚本后旧绑定可能不匹配，需「重置设备绑定」。
# Linux：社区客户端 2.x 起一般已上报 IV_HWADDR，走第一级即可，不会进入 UV_UUID 分支（该分支仅 IV_PLAT 为 iOS/Mac/Win 时）。
# strict_hwaddr：必须 IV_HWADDR；无则拒绝（提示不兼容 OpenVPN 2.x 核心等）
#
# 成功放行时：将 IV_PLAT、time_ascii（会话开始时间，与 status 一致）写入设备绑定 JSON，供管理端展示。
# ============================================================
# 与 app.core.constants.OPENVPN_ETC_DIR 一致；直接拷贝到服务器即可用。
# 初始化向导仍会执行 __OPENVPN_ETC_DIR__ → 实际路径 的替换（兼容旧模板）。
OVPN_ETC="/etc/openvpn"
# 由 script_sync 将 __DEVICE_BIND_LOG_FILE__ 替换为 constants.DEVICE_BIND_LOG_FILE（唯一真相源）
BIND_LOG="__DEVICE_BIND_LOG_FILE__"
case "$BIND_LOG" in *"__DEVICE_BIND_LOG_FILE__"*)
    BIND_LOG="${OVPN_ETC}/log/openvpn-device-bind.log"
    ;;
esac
BINDINGS_DIR="${OVPN_ETC}/mgmt/device_bindings"
MODE_FILE="${OVPN_ETC}/mgmt/device_bind_mode"

CN="${common_name}"
# 与 device_binding_json.py 同目录（部署在 /etc/openvpn/scripts/）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BD_JSON_PY="${SCRIPT_DIR}/device_binding_json.py"

# 选用 PATH 中版本号最高的 python3.x（多版本并存时避免 python3 指向旧解释器），最后回退 python3
init_py3_for_bind() {
    [ -n "${PY3_BIND:-}" ] && return 0
    local v name
    for v in 20 19 18 17 16 15 14 13 12 11 10 9 8 7 6; do
        name="python3.${v}"
        if command -v "$name" >/dev/null 2>&1; then
            PY3_BIND="$name"
            return 0
        fi
    done
    if command -v python3 >/dev/null 2>&1; then
        PY3_BIND="python3"
        return 0
    fi
    return 1
}

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [device-bind] $1" >> "${BIND_LOG}"
}

DEVICE_BIND_MODE="weak_fingerprint"
if [ -f "${MODE_FILE}" ]; then
    DEVICE_BIND_MODE=$(head -1 "${MODE_FILE}" | tr -d '\r\n' | xargs)
fi
case "${DEVICE_BIND_MODE}" in
    weak_log|weak_fingerprint|strict_hwaddr) ;;
    *) DEVICE_BIND_MODE="weak_fingerprint" ;;
esac

if [ -z "${CN}" ]; then
    log "错误: common_name 为空，拒绝连接"
    exit 1
fi

# ---------- 模式 A：仅记录 ----------
if [ "${DEVICE_BIND_MODE}" = "weak_log" ]; then
    log "mode=weak_log CN=${CN} IV_PLAT=${IV_PLAT:-} UV_UUID=${UV_UUID:-} IV_VER=${IV_VER:-} IV_HWADDR=${IV_HWADDR:-} IV_AUTO_SESS=${IV_AUTO_SESS:-} IV_PLAT_VER=${IV_PLAT_VER:-} IV_GUI_VER=${IV_GUI_VER:-}"
    exit 0
fi

# ---------- 模式 C：强绑定，必须 IV_HWADDR ----------
if [ "${DEVICE_BIND_MODE}" = "strict_hwaddr" ]; then
    if [ -z "${IV_HWADDR:-}" ]; then
        log "mode=strict_hwaddr 拒绝: CN=${CN} 未提供 IV_HWADDR。请使用带 OpenVPN 3.x 核心且上报硬件地址的客户端；仅 2.x 核心/老安卓常无此项。"
        exit 1
    fi
    FINGERPRINT="${IV_HWADDR}"
    FINGERPRINT_SOURCE="IV_HWADDR"
else
    # 历史 B 模式（IV_HWADDR → IV_PLAT_VER|IV_GUI_VER 等）备份：同目录 device-bind.weak_b_mode.legacy.bash.inc，自行粘贴替换本节。
    # ---------- 模式 B：弱指纹（全体 HW；再按平台：iOS/Mac/Win→UV_UUID，安卓→IV_PLAT|IV_PLAT_VER，其它→弱串）----------
    if [ -n "${IV_HWADDR:-}" ]; then
        FINGERPRINT="${IV_HWADDR}"
        FINGERPRINT_SOURCE="IV_HWADDR"
    else
        _plat_lc=$(printf '%s' "${IV_PLAT:-}" | tr '[:upper:]' '[:lower:]')
        case "${_plat_lc}" in
            ios|iphone|ipad|mac|macos|macosx|darwin|osx|win|windows)
                if [ -z "${UV_UUID:-}" ]; then
                    log "mode=weak_fingerprint CN=${CN} 平台 IV_PLAT=${IV_PLAT:-?}（iOS/Mac/Win）无 IV_HWADDR 且缺 UV_UUID，仅证书认证通过"
                    exit 0
                fi
                FINGERPRINT="${UV_UUID}"
                FINGERPRINT_SOURCE="UV_UUID"
                ;;
            android)
                if [ -z "${IV_PLAT_VER:-}" ]; then
                    log "mode=weak_fingerprint CN=${CN} 安卓无 IV_HWADDR 且缺 IV_PLAT_VER，仅证书认证通过"
                    exit 0
                fi
                FINGERPRINT="${IV_PLAT:-android}|${IV_PLAT_VER}"
                FINGERPRINT_SOURCE="android_peer_info"
                ;;
            *)
                if [ -z "${IV_PLAT_VER:-}" ] && [ -z "${IV_GUI_VER:-}" ]; then
                    log "mode=weak_fingerprint CN=${CN} 无 IV_HWADDR，IV_PLAT=${IV_PLAT:-?} 非 iOS/Mac/Win/安卓或未上报 PLAT_VER|GUI_VER，仅证书认证通过"
                    exit 0
                fi
                FINGERPRINT="${IV_PLAT_VER:-}|${IV_GUI_VER:-}"
                FINGERPRINT_SOURCE="weak_peer_info"
                ;;
        esac
    fi
fi

mkdir -p "${BINDINGS_DIR}"

BINDING_FILE=""
for f in "${BINDINGS_DIR}"/*.json; do
    [ -f "$f" ] || continue
    if grep -q "\"username\": \"${CN}\"" "$f" 2>/dev/null; then
        BINDING_FILE="$f"
        break
    fi
done

write_binding_json() {
    if [ ! -f "${BD_JSON_PY}" ]; then
        log "错误: 未找到 ${BD_JSON_PY}，请与 device-bind.sh 一并部署到 scripts 目录，CN=${CN}"
        exit 1
    fi
    if ! init_py3_for_bind; then
        log "错误: 未找到 python3 / python3.x，无法写入绑定 JSON，CN=${CN}"
        exit 1
    fi
    if ! "${PY3_BIND}" "${BD_JSON_PY}" write-new \
        --bindings-dir "${BINDINGS_DIR}" \
        --username "${CN}" \
        --fingerprint "${FINGERPRINT}" \
        --fingerprint-source "${FINGERPRINT_SOURCE}" \
        --iv-plat "${IV_PLAT:-}" \
        --time-ascii "${time_ascii:-}" \
        2>>"${BIND_LOG}"; then
        log "错误: 写入绑定 JSON 失败（${PY3_BIND} 执行 device_binding_json.py），CN=${CN}"
        exit 1
    fi
}

update_binding_seen_and_plat() {
    if [ ! -f "${BD_JSON_PY}" ]; then
        log "错误: 未找到 ${BD_JSON_PY}，CN=${CN}"
        exit 1
    fi
    if ! init_py3_for_bind; then
        log "错误: 未找到 python3 / python3.x，无法更新绑定 JSON，CN=${CN}"
        exit 1
    fi
    if ! "${PY3_BIND}" "${BD_JSON_PY}" update \
        --file "${BINDING_FILE}" \
        --iv-plat "${IV_PLAT:-}" \
        --time-ascii "${time_ascii:-}" \
        2>>"${BIND_LOG}"; then
        log "错误: 更新绑定 JSON 失败（${PY3_BIND} 执行 device_binding_json.py），CN=${CN}"
        exit 1
    fi
}

if [ -z "${BINDING_FILE}" ]; then
    write_binding_json
    log "新设备绑定: mode=${DEVICE_BIND_MODE} 用户=${CN}, 指纹=${FINGERPRINT}, source=${FINGERPRINT_SOURCE}"
    exit 0
fi

STORED_FP=$(grep -o '"fingerprint": "[^"]*"' "${BINDING_FILE}" | head -1 | cut -d'"' -f4)

if [ "${FINGERPRINT}" = "${STORED_FP}" ]; then
    update_binding_seen_and_plat
    log "设备指纹匹配: CN=${CN} mode=${DEVICE_BIND_MODE}"
    exit 0
fi

log "设备指纹不匹配! mode=${DEVICE_BIND_MODE} CN=${CN}, 期望=${STORED_FP}, 收到=${FINGERPRINT}"
exit 1
