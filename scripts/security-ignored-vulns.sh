#!/usr/bin/env bash
# Shared vulnerability suppressions for security tooling.
# Keep this file as the single source of truth for temporary ignores.
#
# IDs for the same advisory can differ by tool/vendor namespace — both are
# listed here for cross-reference. Format: GHSA/PYSEC == CVE.
#
# Active suppressions:
#
# - GHSA-cqp8-fcvh-x7r3 == CVE-2026-46678: pydantic-ai deserialization RCE.
#   Fixed in pydantic-ai >= 1.99.0. Upgrade blocked by fastmcp 3.3.1 removing
#   the top-level FastMCP import our codebase uses. Coordinated migration
#   tracked in issue #1234 (PR 2 scope).
#   TODO(#1234-pr2): remove once pydantic-ai + fastmcp upgrade lands.
#
# - PYSEC-2026-161: starlette 0.50.0 vulnerability. Fixed in starlette 1.0.1.
#   Upgrade blocked by fastapi 0.128.0 which constrains starlette < 1.0.0.
#   Requires coordinated fastapi + starlette upgrade (PR 2 scope).
#   TODO(#1234-pr2): remove once fastapi + starlette upgrade lands.
#
# Previously ignored, now resolved (kept as a record):
# (none yet)

# uv-secure supports GHSA/PYSEC identifiers.
UV_SECURE_IGNORED_VULNS="PYSEC-2026-89,PYSEC-2025-183,GHSA-cqp8-fcvh-x7r3,PYSEC-2026-161"

# pip-audit supports CVE, GHSA, and PYSEC IDs. Keep both CVE and GHSA for
# cross-reference. PYSEC IDs used where no CVE alias is available.
PIP_AUDIT_IGNORED_VULNS="CVE-2026-46678,GHSA-cqp8-fcvh-x7r3,PYSEC-2026-161"
