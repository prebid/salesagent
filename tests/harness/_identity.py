"""Canonical make_identity helper for test files.

Delegates to PrincipalFactory.make_identity() — the single source of truth
for constructing ResolvedIdentity in tests.
"""

from __future__ import annotations

from src.core.resolved_identity import ResolvedIdentity
from tests.factories.principal import _UNSET, PrincipalFactory


def make_identity(
    principal_id: str | None = _UNSET,  # type: ignore[assignment]
    tenant_id: str | None = _UNSET,  # type: ignore[assignment]
    tenant: dict | None = _UNSET,  # type: ignore[assignment]
    protocol: str = "mcp",
    dry_run: bool = False,
    **kwargs: object,
) -> ResolvedIdentity:
    """Build a ResolvedIdentity with explicit control over all fields.

    This is the canonical version — import from ``tests.harness`` instead
    of defining a local ``_make_identity`` in each test file.

    Thin wrapper around PrincipalFactory.make_identity() for backward
    compatibility with existing callers. Preserves None when explicitly
    passed (e.g. principal_id=None for auth-error tests).
    """
    return PrincipalFactory.make_identity(
        principal_id="test_principal" if principal_id is _UNSET else principal_id,
        tenant_id="test_tenant" if tenant_id is _UNSET else tenant_id,
        tenant=tenant,
        protocol=protocol,
        dry_run=dry_run,
        **kwargs,
    )
