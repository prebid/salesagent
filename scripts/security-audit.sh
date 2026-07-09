#!/usr/bin/env bash
# Security audit — SINGLE SOURCE OF TRUTH for the ignored-vulnerabilities list.
# Both ./run_all_tests.sh and .github/workflows/ci.yml call this script, so
# the list cannot drift between local and CI.
#
# Active suppressions: none (see scripts/security-ignored-vulns.sh).
#
# Previously ignored, now resolved (kept here as a record):
#
# - PYSEC-2026-89 / CVE-2025-69534 / GHSA-5wmx-573v-2qwq: Python-Markdown
#     unhandled AssertionError on malformed HTML-like sequences. The
#     advisory body says "fixed in version 3.8.1"; the OSV record's
#     affected-range events array was malformed (missing the
#     ``{"fixed": "3.8.1"}`` event), so uv-secure flagged every version. We
#     were on Markdown >= 3.8.1 — past the fix. Suppression removed 2026-07
#     (#1557) when uv-secure clean without ignore.
#
# - PYSEC-2025-183 / CVE-2025-45768: PyJWT alleged weak signing key —
#     explicitly DISPUTED by the supplier per the advisory body ("the key
#     length is chosen by the application that uses the library"). No fix
#     version exists because the maintainer rejects the framing. Inapplicable
#     here regardless: this codebase only invokes PyJWT via
#     ``jwt.decode(id_token, options={"verify_signature": False})`` to parse
#     Google-issued OIDC ID tokens (src/admin/auth_utils.py), so no PyJWT
#     signing keys are configured by this application at all. Suppression
#     removed 2026-07 (#1558) when uv-secure clean without ignore.
#
# - MAL-2026-4750: fastapi 'fastar' dep — OSV advisory withdrawn 2026-05-26
#     (internal tooling artefact). Suppression removed 2026-07 (#1559) when
#     uv-secure and pip-audit clean without ignore.
#
# - GHSA-7gcm-g887-7qv7 (protobuf DoS) — resolved by bumping protobuf to 6.33.6.
# - GHSA-5239-wwwm-4pmq (Pygments AdlLexer ReDoS) — resolved by bumping
#   Pygments to 2.20.0.
# - GHSA-cqp8-fcvh-x7r3 / CVE-2026-46678: pydantic-ai RCE via unsafe deserialization.
#   Resolved: pydantic-ai-slim bumped to >=1.99.0; fastmcp kept at <3.3.0 (PR 2 of #1234).
# - PYSEC-2026-161: starlette 0.50.0 BadHost / CVE-2026-48710.
#   Resolved: fastapi bumped to >=0.133.0 (starlette 1.0+ support) (PR 2 of #1234).
#
# When active suppressions exist, ``--allow-unused-ignores`` keeps the build
# resilient: an ignored advisory that the upstream database temporarily
# un-flags must not flip the suite red on the next run.
#
# Extra arguments are forwarded to uv-secure (e.g. ``--no-check-uv-tool``).
# Ignore IDs are sourced from scripts/security-ignored-vulns.sh.
set -euo pipefail

source "$(dirname "$0")/security-ignored-vulns.sh"

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UV_SECURE_ARGS=()
if [[ -n "$UV_SECURE_IGNORED_VULNS" ]]; then
  UV_SECURE_ARGS+=(--ignore-vulns "$UV_SECURE_IGNORED_VULNS" --allow-unused-ignores)
fi

exec uvx uv-secure "${UV_SECURE_ARGS[@]+"${UV_SECURE_ARGS[@]}"}" "$@" "$PROJECT_ROOT/uv.lock"
