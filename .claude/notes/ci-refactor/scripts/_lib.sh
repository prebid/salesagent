#!/bin/bash
# Shared verification helpers for verify-pr*.sh scripts.
#
# Usage: source from verify-pr*.sh scripts:
#     source "$(dirname "$0")/_lib.sh"
#
# Provides: fail(), ok(), warn(), section(), check_sha_pinned(),
#   check_persist_credentials_false(), check_workflow_permissions(),
#   check_workflow_concurrency(), check_adr_status(),
#   check_harden_runner_cve_fix(), check_yaml_lints(), check_actionlint().
#
# Sourced (not executed) — shellcheck: disable=SC2034 if unused.
set -euo pipefail

# Output helpers
fail()    { echo "FAIL: $*" >&2; exit 1; }
ok()      { echo "  ok: $*"; }
warn()    { echo "WARN: $*" >&2; }
section() { echo; echo "=== $* ==="; }

# Common assertions
check_sha_pinned() {
    local file="$1"
    local pattern="$2"
    grep -E "${pattern}@[a-f0-9]{40}\s+#\s+(frozen:\s+)?v[0-9]" "$file" \
        || fail "$file does not have SHA-pinned $pattern with version comment"
}

check_persist_credentials_false() {
    local file="$1"
    grep -A1 "uses: actions/checkout" "$file" | grep -q "persist-credentials: false" \
        || fail "$file actions/checkout missing persist-credentials: false"
}

check_workflow_permissions() {
    local file="$1"
    head -50 "$file" | grep -q "^permissions:" \
        || fail "$file missing top-level permissions block"
}

check_workflow_concurrency() {
    local file="$1"
    grep -q "^concurrency:" "$file" \
        || fail "$file missing top-level concurrency block"
}

check_adr_status() {
    local adr="$1"
    local expected="$2"
    grep -q "^- \*\*Status:\*\* ${expected}" "$adr" \
        || fail "$adr Status is not ${expected}"
}

check_harden_runner_cve_fix() {
    local file="$1"
    grep -q "disable-sudo-and-containers: true" "$file" \
        || fail "$file harden-runner missing disable-sudo-and-containers: true (CVE-2025-32955)"
    ! grep -E "disable-sudo:\s*true\s*$" "$file" \
        || fail "$file harden-runner uses bypassable disable-sudo (CVE-2025-32955)"
}

check_yaml_lints() {
    local file="$1"
    yamllint -d relaxed "$file" \
        || fail "$file fails yamllint (relaxed mode)"
}

check_actionlint() {
    local file="$1"
    actionlint "$file" \
        || fail "$file fails actionlint"
}

# vim: ft=sh
