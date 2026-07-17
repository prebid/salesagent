#!/usr/bin/env bash
# Shared vulnerability suppressions for security tooling.
# Keep this file as the single source of truth for temporary ignores.
#
# IDs for the same advisory can differ by tool/vendor namespace — both are
# listed here for cross-reference. Format: GHSA/PYSEC == CVE.
#
# Active suppressions: (none)
#
# Previously ignored, now resolved:
#
# - PYSEC-2026-89 / CVE-2025-69534 / GHSA-5wmx-573v-2qwq: Python-Markdown
#   OSV affected-range malformed; we are on Markdown >= 3.8.1. Removed 2026-07
#   when uv-secure stopped flagging without ignore (#1557).
#
# - PYSEC-2025-183 / CVE-2025-45768: PyJWT disputed weak-signing-key advisory;
#   inapplicable (OIDC decode only, no app signing keys). Removed 2026-07 when
#   uv-secure stopped flagging without ignore (#1558).
#
# - MAL-2026-4750: fastapi 'fastar' dep — OSV advisory withdrawn 2026-05-26.
#   Removed 2026-07 when uv-secure and pip-audit stopped flagging (#1559).
#
# - GHSA-cqp8-fcvh-x7r3 == CVE-2026-46678: pydantic-ai deserialization RCE.
#   Resolved: pydantic-ai bumped to >=1.99.0 (PR 2). fastmcp was intentionally
#   kept at >=3.2.0,<3.3.0 because 3.3.0 split fastmcp-slim from the monolith,
#   breaking fastmcp.server.context imports. The CVE was in pydantic-ai, not
#   fastmcp, so the constraint change is sufficient.
#
# - PYSEC-2026-161: starlette 0.50.0 vulnerability (BadHost / CVE-2026-48710).
#   Resolved: fastapi bumped to >=0.133.0 (starlette 1.0+ support) (PR 2).

# uv-secure supports GHSA/PYSEC/MAL identifiers.
UV_SECURE_IGNORED_VULNS=""

# pip-audit supports CVE, GHSA, PYSEC, and MAL IDs.
PIP_AUDIT_IGNORED_VULNS=""
