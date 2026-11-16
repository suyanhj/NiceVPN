#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "${ROOT_DIR}/deploy/shell/python-runtime.sh"

TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/nicevpn-python-runtime.XXXXXX")"
trap 'rm -rf "${TEST_ROOT}"' EXIT

FAKE_BIN="${TEST_ROOT}/bin"
PROJECT="${TEST_ROOT}/project"
mkdir -p "${FAKE_BIN}" "${PROJECT}"

make_fake_python() {
    local path="$1"
    local version="$2"
    local major="${version%%.*}"
    local minor="${version#*.}"
    minor="${minor%%.*}"

    cat > "${path}" <<EOF
#!/usr/bin/env bash
case "\$*" in
    *"sys.version_info >= (3, 10)"*)
        if (( ${major} > 3 || (${major} == 3 && ${minor} >= 10) )); then
            exit 0
        fi
        exit 1
        ;;
    *"sys.version_info[:3]"*)
        printf '%s\n' '${version}'
        ;;
    *"os.path.abspath(sys.executable)"*)
        printf '%s\n' "\$0"
        ;;
    *)
        exit 0
        ;;
esac
EOF
    chmod +x "${path}"
}

# Shadow newer host interpreters so the test is deterministic on any CI runner.
for version in 3.15 3.14 3.13 3.12 3.10; do
    make_fake_python "${FAKE_BIN}/python${version}" "3.6.8"
done
make_fake_python "${FAKE_BIN}/python3" "3.6.8"
make_fake_python "${FAKE_BIN}/python3.11" "3.11.9"
PATH="${FAKE_BIN}:/usr/bin:/bin"
export PATH
unset VIRTUAL_ENV CONDA_PREFIX || true

# A generic python3 pointing to 3.6 must not hide the installed python3.11.
PYTHON_BIN=""
nicevpn_select_python "${PROJECT}"
[[ "${PYTHON_BIN}" == "${FAKE_BIN}/python3.11" ]] || {
    echo "FAIL: expected python3.11, got ${PYTHON_BIN}" >&2
    exit 1
}

# An old project venv is skipped; discovery continues to the compatible system runtime.
mkdir -p "${PROJECT}/.venv/bin"
make_fake_python "${PROJECT}/.venv/bin/python" "3.6.15"
PYTHON_BIN=""
nicevpn_select_python "${PROJECT}"
[[ "${PYTHON_BIN}" == "${FAKE_BIN}/python3.11" ]] || {
    echo "FAIL: old project venv blocked system Python: ${PYTHON_BIN}" >&2
    exit 1
}

# An explicitly requested incompatible interpreter must fail instead of silently switching.
PYTHON_BIN="${FAKE_BIN}/python3"
if nicevpn_select_python "${PROJECT}" >/dev/null 2>&1; then
    echo "FAIL: explicit Python 3.6 should have been rejected" >&2
    exit 1
fi

# An explicitly requested compatible interpreter is respected.
PYTHON_BIN="python3.11"
nicevpn_select_python "${PROJECT}"
[[ "${PYTHON_BIN}" == "${FAKE_BIN}/python3.11" ]] || {
    echo "FAIL: explicit Python 3.11 was not selected: ${PYTHON_BIN}" >&2
    exit 1
}

echo "PASS: Python runtime discovery selects the newest compatible interpreter."
