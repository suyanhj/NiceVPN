#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../common/atomic-files.sh
source "${SCRIPT_DIR}/../common/atomic-files.sh"
# shellcheck source=openvpn-install-common.sh
source "${SCRIPT_DIR}/openvpn-install-common.sh"

easyrsa_migrate_legacy_install
