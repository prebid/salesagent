#!/usr/bin/env bash
# Shared vulnerability suppressions for security tooling.
# Keep this file as the single source of truth for temporary ignores.
#
# IDs for the same advisory can differ by tool/vendor namespace:
# - GHSA-cqp8-fcvh-x7r3 == CVE-2026-46678 (pydantic-ai deserialization RCE)
#   TODO(#1234-pr2): remove once coordinated pydantic-ai + fastmcp upgrade lands.

# uv-secure supports GHSA/PYSEC identifiers.
UV_SECURE_IGNORED_VULNS="PYSEC-2026-89,PYSEC-2025-183,GHSA-cqp8-fcvh-x7r3"

# pip-audit supports CVE and GHSA IDs. Keep both for explicit cross-reference.
PIP_AUDIT_IGNORED_VULNS="CVE-2026-46678,GHSA-cqp8-fcvh-x7r3"
