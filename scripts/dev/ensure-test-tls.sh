#!/usr/bin/env bash
#
# Single entry point for the test stacks' TLS material (salesagent-tgzb).
#
# Every path that brings up an e2e stack calls THIS, not the generator directly,
# so the "who generates the certificate" question has one answer:
#   scripts/test-stack.sh cmd_up       (host stack -> published TLS port)
#   run_all_tests.sh                   (in-network stack + per-worker sidecars)
#   tests/e2e/conftest.py              (standalone branch: builds its own stack)
#
# The only thing this adds over `python scripts/dev/gen_test_tls.py` is finding
# an interpreter that HAS `cryptography` (a direct project dependency, so the
# project venv always does — but a bare `python3` on a CI runner may not, and the
# in-network runner deliberately has no `uv` on PATH). Callers that already know
# a good interpreter pass it in $PYTHON.
set -euo pipefail

cd "$( dirname "${BASH_SOURCE[0]}" )/../.."

GENERATOR="scripts/dev/gen_test_tls.py"

try_python() {
    command -v "$1" >/dev/null 2>&1 || return 1
    "$@" -c "import cryptography" >/dev/null 2>&1 || return 1
    exec "$@" "$GENERATOR"
}

[ -n "${PYTHON:-}" ] && try_python "$PYTHON" || true
try_python uv run python || true
try_python python3 || true
try_python python || true

echo "ERROR: no Python with the 'cryptography' package found — cannot generate the test TLS material." >&2
echo "       Set PYTHON=<interpreter>, or install the project's dev dependencies (uv sync)." >&2
exit 1
