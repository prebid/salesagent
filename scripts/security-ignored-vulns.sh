#!/usr/bin/env bash
# Shared vulnerability suppressions for security tooling.
# Keep this file as the single source of truth for temporary ignores.
#
# IDs for the same advisory can differ by tool/vendor namespace — both are
# listed here for cross-reference. Format: GHSA/PYSEC == CVE.
#
# Active suppressions: (none — all prior suppressions resolved in PR 2 of #1234)
#
# Previously ignored, now resolved:
#
# - GHSA-cqp8-fcvh-x7r3 == CVE-2026-46678: pydantic-ai deserialization RCE.
#   Resolved: pydantic-ai bumped to >=1.99.0 + fastmcp bumped to >=3.3.1 (PR 2).
#
# - PYSEC-2026-161: starlette 0.50.0 vulnerability (BadHost / CVE-2026-48710).
#   Resolved: fastapi bumped to >=0.133.0 (starlette 1.0+ support) (PR 2).

# uv-secure supports GHSA/PYSEC identifiers.
UV_SECURE_IGNORED_VULNS="PYSEC-2026-89,PYSEC-2025-183"

# pip-audit supports CVE, GHSA, and PYSEC IDs. Keep both CVE and GHSA for
# cross-reference. PYSEC IDs used where no CVE alias is available.
PIP_AUDIT_IGNORED_VULNS=""
