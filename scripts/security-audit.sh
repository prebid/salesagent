#!/usr/bin/env bash
# Security audit — SINGLE SOURCE OF TRUTH for the ignored-vulnerabilities list.
# Both ./run_all_tests.sh and .github/workflows/test.yml call this script, so
# the list cannot drift between local and CI.
#
# Ignored advisories (each documented with rationale):
#
# - PYSEC-2026-89 / CVE-2025-69534 / GHSA-5wmx-573v-2qwq: Python-Markdown
#     unhandled AssertionError on malformed HTML-like sequences. The
#     advisory body says "fixed in version 3.8.1"; the OSV record's
#     affected-range events array is malformed (missing the
#     ``{"fixed": "3.8.1"}`` event), so uv-secure flags every version. We
#     are on Markdown >= 3.8.1 (currently 3.10.2) — past the fix.
#
# - PYSEC-2025-183 / CVE-2025-45768: PyJWT alleged weak signing key —
#     explicitly DISPUTED by the supplier per the advisory body ("the key
#     length is chosen by the application that uses the library"). No fix
#     version exists because the maintainer rejects the framing. OSV record
#     has the same malformed empty-fixed-event pattern as PYSEC-2026-89.
#     Inapplicable here regardless: this codebase only invokes PyJWT via
#     ``jwt.decode(id_token, options={"verify_signature": False})`` to parse
#     Google-issued OIDC ID tokens (src/admin/auth_utils.py), so no PyJWT
#     signing keys are configured by this application at all.
#     uv-secure's advisory database has shown it flip between flagged and
#     unflagged across runs as the upstream record gets re-curated, hence
#     ``--allow-unused-ignores`` below.
#
# Previously ignored, now resolved by real dep bumps (kept here as a record):
# - GHSA-7gcm-g887-7qv7 (protobuf DoS) — resolved by bumping protobuf to 6.33.6.
# - GHSA-5239-wwwm-4pmq (Pygments AdlLexer ReDoS) — resolved by bumping
#   Pygments to 2.20.0.
# - GHSA-cqp8-fcvh-x7r3 / CVE-2026-46678: pydantic-ai RCE via unsafe deserialization.
#   Resolved: pydantic-ai bumped to >=1.99.0 + fastmcp to >=3.3.1 (PR 2 of #1234).
# - PYSEC-2026-161: starlette 0.50.0 BadHost / CVE-2026-48710.
#   Resolved: fastapi bumped to >=0.133.0 (starlette 1.0+ support) (PR 2 of #1234).
#
# ``--allow-unused-ignores`` keeps the build resilient: an ignored advisory
# that the upstream database temporarily un-flags must not flip the suite
# red on the next run.
#
# Extra arguments are forwarded to uv-secure (e.g. ``--no-check-uv-tool``).
# Ignore IDs are sourced from scripts/security-ignored-vulns.sh.
set -euo pipefail

source "$(dirname "$0")/security-ignored-vulns.sh"

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec uvx uv-secure --ignore-vulns "$UV_SECURE_IGNORED_VULNS" --allow-unused-ignores "$@" "$PROJECT_ROOT/uv.lock"
