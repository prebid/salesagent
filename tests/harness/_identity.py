"""Canonical make_identity helper for test files.

Provides a single source of truth for constructing ResolvedIdentity
in tests that don't use BaseTestEnv (which has identity_for()).
"""

from __future__ import annotations

from src.core.resolved_identity import ResolvedIdentity
from src.core.testing_hooks import AdCPTestContext


def make_identity(
    principal_id: str | None = None,
    tenant_id: str | None = None,
    tenant: dict | None = None,
    protocol: str = "mcp",
    dry_run: bool = False,
    **kwargs,
) -> ResolvedIdentity:
    """Build a ResolvedIdentity with explicit control over all fields.

    This is the canonical version — import from ``tests.harness`` instead
    of defining a local ``_make_identity`` in each test file.
    """
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id or "test_tenant",
        tenant=tenant,
        protocol=protocol,
        testing_context=AdCPTestContext(
            dry_run=dry_run,
            mock_time=None,
            jump_to_event=None,
            test_session_id=None,
        ),
        **kwargs,
    )
