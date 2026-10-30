#!/usr/bin/env bash
# 远端 OpenVPN binary/version 探测。由 Python 仅注入候选路径列表，不在代码里拼 shell 逻辑。
set -e

: "${PREFERRED_BINARY:=}"
: "${OPENVPN_SEARCH_PATHS:=}"

probe_path() {
    local candidate="$1"
    [ -n "${candidate}" ] || return 1
    [ -x "${candidate}" ] || return 1
    printf 'BIN:%s\n' "${candidate}"
    "${candidate}" --version 2>&1 | head -1
    exit 0
}

if [ -n "${PREFERRED_BINARY}" ]; then
    probe_path "${PREFERRED_BINARY}" || true
fi

while IFS= read -r candidate; do
    [ -n "${candidate}" ] || continue
    probe_path "${candidate}" || true
done <<EOF
${OPENVPN_SEARCH_PATHS}
EOF

if command -v openvpn >/dev/null 2>&1; then
    candidate="$(command -v openvpn)"
    printf 'BIN:%s\n' "${candidate}"
    "${candidate}" --version 2>&1 | head -1
    exit 0
fi

exit 1
