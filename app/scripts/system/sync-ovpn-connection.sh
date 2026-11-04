#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat >&2 <<'EOF'
Usage:
  sync-ovpn-connection.sh <ovpn-dir> <old-host> <old-port> <old-proto> <new-host> <new-port> <new-proto>
EOF
}

[[ $# -eq 7 ]] || { usage; exit 2; }

OVPN_DIR="$1"
OLD_HOST="$2"
OLD_PORT="$3"
OLD_PROTO="$4"
NEW_HOST="$5"
NEW_PORT="$6"
NEW_PROTO="$7"

[[ -d "$OVPN_DIR" ]] || {
    echo "OVPN_DIR_NOT_FOUND=$OVPN_DIR" >&2
    exit 3
}
[[ "$NEW_PORT" =~ ^[0-9]+$ ]] && (( NEW_PORT >= 1 && NEW_PORT <= 65535 )) || {
    echo "INVALID_NEW_PORT=$NEW_PORT" >&2
    exit 4
}
case "$NEW_PROTO" in
    udp|tcp) ;;
    *)
        echo "INVALID_NEW_PROTO=$NEW_PROTO" >&2
        exit 5
        ;;
esac

escape_sed_replacement() {
    printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'
}

NEW_HOST_ESCAPED="$(escape_sed_replacement "$NEW_HOST")"
BACKUP_SUFFIX=".nicevpn-connection.bak"
changed=0
total=0
declare -a touched=()

rollback() {
    local file backup
    for file in "${touched[@]:-}"; do
        backup="${file}${BACKUP_SUFFIX}"
        if [[ -f "$backup" ]]; then
            mv -f -- "$backup" "$file" || true
        fi
    done
}

cleanup_backups() {
    local file backup
    for file in "${touched[@]:-}"; do
        backup="${file}${BACKUP_SUFFIX}"
        rm -f -- "$backup"
    done
}

trap 'rollback' ERR INT TERM

shopt -s nullglob
files=("$OVPN_DIR"/*.ovpn)

# 先做完整预检，避免改到一半才发现某个历史文件结构异常。
for file in "${files[@]}"; do
    ((total += 1))
    grep -Eq '^[[:space:]]*remote[[:space:]]+[^[:space:]]+' "$file" || {
        echo "MISSING_REMOTE=$file" >&2
        exit 6
    }
    grep -Eq '^[[:space:]]*proto[[:space:]]+[^[:space:]]+' "$file" || {
        echo "MISSING_PROTO=$file" >&2
        exit 7
    }
done

for file in "${files[@]}"; do
    before_remote="$(grep -Em1 '^[[:space:]]*remote[[:space:]]+' "$file" || true)"
    before_proto="$(grep -Em1 '^[[:space:]]*proto[[:space:]]+' "$file" || true)"
    expected_remote="remote $NEW_HOST $NEW_PORT"
    expected_proto="proto $NEW_PROTO"

    if [[ "$before_remote" == "$expected_remote" && "$before_proto" == "$expected_proto" ]]; then
        continue
    fi

    rm -f -- "${file}${BACKUP_SUFFIX}"
    sed -i"$BACKUP_SUFFIX" \
        -e "s|^[[:space:]]*remote[[:space:]].*$|remote ${NEW_HOST_ESCAPED} ${NEW_PORT}|" \
        -e "s|^[[:space:]]*proto[[:space:]].*$|proto ${NEW_PROTO}|" \
        -- "$file"

    touched+=("$file")
    ((changed += 1))
done

trap - ERR INT TERM
cleanup_backups

printf 'OLD=%s:%s/%s\n' "$OLD_HOST" "$OLD_PORT" "$OLD_PROTO"
printf 'NEW=%s:%s/%s\n' "$NEW_HOST" "$NEW_PORT" "$NEW_PROTO"
printf 'TOTAL=%d\n' "$total"
printf 'CHANGED=%d\n' "$changed"
