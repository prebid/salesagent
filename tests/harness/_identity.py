"""Canonical make_identity helper for test files.

Delegates to ``PrincipalFactory.make_identity()`` / ``make_public_identity()`` — the single
source of truth for constructing an identity in tests.
"""

from __future__ import annotations

from src.core.resolved_identity import PublicIdentity, ResolvedIdentity
from src.core.tenant_context import TenantContext
from tests.factories.principal import _UNSET, PrincipalFactory


def make_identity(
    principal_id: str | None = _UNSET,  # type: ignore[assignment]
    tenant_id: str | None = _UNSET,  # type: ignore[assignment]
    tenant: TenantContext | None = _UNSET,  # type: ignore[assignment]
    **kwargs: object,
) -> ResolvedIdentity | PublicIdentity:
    """Build an identity with explicit control over all fields.

    This is the canonical version — import from ``tests.harness`` instead
    of defining a local ``_make_identity`` in each test file.

    Thin wrapper around the factory for backward compatibility with existing callers.
    ``principal_id=None`` is the anonymous caller and builds a ``PublicIdentity`` (the type
    a public tool takes); anything else builds the ``ResolvedIdentity`` a protected tool
    takes. A dict is refused by either.
    """
    resolved_tenant_id = "test_tenant" if tenant_id is _UNSET else tenant_id
    if principal_id is None:
        return PrincipalFactory.make_public_identity(
            principal_id=None,
            tenant_id=resolved_tenant_id,  # type: ignore[arg-type]
            tenant=tenant,
            **kwargs,
        )
    return PrincipalFactory.make_identity(
        principal_id="test_principal" if principal_id is _UNSET else principal_id,
        tenant_id=resolved_tenant_id,  # type: ignore[arg-type]
        tenant=tenant,  # type: ignore[arg-type]
        **kwargs,
    )
