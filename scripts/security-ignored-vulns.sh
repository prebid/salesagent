#!/usr/bin/env bash
# Shared vulnerability suppressions for security tooling.
# Keep this file as the single source of truth for temporary ignores.
#
# IDs for the same advisory can differ by tool/vendor namespace — both are
# listed here for cross-reference. Format: GHSA/PYSEC == CVE.
#
# Active suppressions:
#
# - MAL-2026-4750: fastapi 0.136.3 — Amazon Inspector flagged an undocumented
#   'fastar' dep in the [standard] optional group. The OSV advisory was
#   WITHDRAWN on 2026-05-26 (same day it was published); 'fastar' was an
#   internal tooling artefact, not a supply-chain injection. Both uv-secure
#   and pip-audit lag the OSV withdrawal by hours/days — suppress until
#   their databases catch up.
#
# Previously ignored, now resolved:
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
UV_SECURE_IGNORED_VULNS="PYSEC-2026-89,PYSEC-2025-183,MAL-2026-4750"

# pip-audit supports CVE, GHSA, PYSEC, and MAL IDs.
PIP_AUDIT_IGNORED_VULNS="MAL-2026-4750"
