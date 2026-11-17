#!/usr/bin/env bash
# 测试环境一次性目录整理，执行完成后可删除。
set -euo pipefail

[[ "$EUID" -eq 0 ]] || { echo "请使用 root 执行" >&2; exit 1; }

OPENVPN=/opt/openvpn
RELEASES=/opt/openvpn-releases

# 当前 /opt/openvpn 是指向 /opt/openvpn-releases/<version>-<hash> 的软链接。
[[ -L "$OPENVPN" && -d "$RELEASES" ]] || {
    echo "当前目录结构与预期不符，停止执行" >&2
    exit 1
}
ACTIVE="$(readlink -f -- "$OPENVPN")"
[[ "$ACTIVE" == "$RELEASES/"* ]] || { echo "当前 OpenVPN 软链接未指向 $RELEASES" >&2; exit 1; }
LOCAL_TARGET="$(readlink -f -- /usr/local/sbin/openvpn 2>/dev/null || true)"
ACTIVE_NAME="$(basename -- "$ACTIVE")"
VERSION="${ACTIVE_NAME%-*}"
ACTIVE_BIN="$ACTIVE/sbin/openvpn"
ACTIVE_REL=sbin/openvpn
[[ -x "$ACTIVE_BIN" ]] || {
    ACTIVE_BIN="$ACTIVE/bin/openvpn"
    ACTIVE_REL=bin/openvpn
}
[[ -x "$ACTIVE_BIN" && "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
    echo "无法识别当前 OpenVPN 版本：$ACTIVE" >&2
    exit 1
}

# 仓库整体归位，版本目录改为纯版本号，current 指向当前版本。
rm -- "$OPENVPN"
mv -T -- "$RELEASES" "$OPENVPN"
for dir in "$OPENVPN"/*; do
    [[ -d "$dir" ]] || continue
    name="$(basename -- "$dir")"
    [[ "$name" =~ ^([0-9]+\.[0-9]+\.[0-9]+)-[[:xdigit:]]{8,64}$ ]] || continue
    mv -T -- "$dir" "$OPENVPN/${BASH_REMATCH[1]}"
done
ln -s "$VERSION" "$OPENVPN/current"

# 更新命令行软链接和 systemd 固定入口。
if [[ -L /usr/local/sbin/openvpn && "$LOCAL_TARGET" == "$RELEASES/"* ]]; then
    ln -sfn "$OPENVPN/current/$ACTIVE_REL" /usr/local/sbin/openvpn
fi
find /etc/systemd/system -type f \( -name '*.service' -o -name '*.conf' \) -exec sed -i \
    -e "s#$OPENVPN/sbin/openvpn#$OPENVPN/current/sbin/openvpn#g" \
    -e "s#$OPENVPN/bin/openvpn#$OPENVPN/current/bin/openvpn#g" {} +
systemctl daemon-reload

# EasyRSA 旧目录改为主线目录名，同时保留旧路径兼容。
if [[ -d /opt/easy-rsa && ! -L /opt/easy-rsa && ! -e /opt/ezrsa ]]; then
    mv -T /opt/easy-rsa /opt/ezrsa
    ln -s /opt/ezrsa /opt/easy-rsa
fi

echo "完成：$OPENVPN/current -> $VERSION"
"$OPENVPN/current/$ACTIVE_REL" --version | head -n 1
