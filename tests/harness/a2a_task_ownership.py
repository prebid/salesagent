"""A2ATaskOwnershipEnv — multi-principal env for the A2A in-memory task gate.

``tasks/get`` / ``tasks/cancel`` authorize an in-memory hit against the
``_TaskOwner`` recorded at create (#1702). Grading that gate needs three real,
DB-backed callers — the owner, a sibling principal in the SAME tenant, and a
principal in ANOTHER tenant — because the denial must be identical for a
cross-principal caller, a cross-tenant caller, and an unknown task id.

Real tokens (not injected identities) so each dispatch runs the production auth
chain: header → token → DB lookup → ResolvedIdentity. Nothing is patched; the
handler, its task store, and the ownership gate are all production code.

Usage::

    with A2ATaskOwnershipEnv() as env:
        env.setup_principals()
        env.seed_a2a_task("task-1", identity=env.identity_for_role("owner"))
        denied = env.run_a2a_task_method(
            "tasks/get", "task-1", identity=env.identity_for_role("sibling")
        )
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.resolved_identity import ResolvedIdentity
from tests.a2a_helpers import (
    OWNED_TASK_OTHER_PRINCIPAL,
    OWNED_TASK_OTHER_TENANT,
    OWNED_TASK_OWNER,
    OWNED_TASK_SIBLING,
    OWNED_TASK_TENANT,
)
from tests.harness._base import IntegrationEnv

if TYPE_CHECKING:
    from src.core.database.models import Principal, Tenant

OWNER_ROLE = "owner"
SIBLING_ROLE = "sibling"
OTHER_TENANT_ROLE = "other_tenant"

# Role → (tenant_id, principal_id) built once from the shared OWNED_TASK_* vocabulary.
_ROLE_TARGETS: dict[str, tuple[str, str]] = {
    OWNER_ROLE: (OWNED_TASK_TENANT, OWNED_TASK_OWNER),
    SIBLING_ROLE: (OWNED_TASK_TENANT, OWNED_TASK_SIBLING),
    OTHER_TENANT_ROLE: (OWNED_TASK_OTHER_TENANT, OWNED_TASK_OTHER_PRINCIPAL),
}


class A2ATaskOwnershipEnv(IntegrationEnv):
    """Integration env exposing owner / same-tenant sibling / other-tenant callers."""

    EXTERNAL_PATCHES: dict[str, str] = {}

    def __init__(self, **kwargs: object) -> None:
        kwargs.setdefault("tenant_id", OWNED_TASK_TENANT)
        kwargs.setdefault("principal_id", OWNED_TASK_OWNER)
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._role_identities: dict[str, ResolvedIdentity] = {}

    def setup_principals(self) -> tuple[Tenant, Principal]:
        """Seed the owner tenant + all three principals, each with a real access token.

        Returns the (tenant, owner principal) pair for the owning tenant.
        """
        from tests.factories import PrincipalFactory, TenantFactory

        tenant, owner = self.setup_default_data()
        PrincipalFactory(tenant=tenant, principal_id=OWNED_TASK_SIBLING)
        other_tenant = TenantFactory(tenant_id=OWNED_TASK_OTHER_TENANT)
        PrincipalFactory(tenant=other_tenant, principal_id=OWNED_TASK_OTHER_PRINCIPAL)
        self._commit_factory_data()
        return tenant, owner

    def identity_for_role(self, role: str) -> ResolvedIdentity:
        """A2A ``ResolvedIdentity`` carrying the real token of *role*'s principal.

        Roles: ``owner``, ``sibling`` (same tenant), ``other_tenant``. Built by
        re-pointing the env at that principal so the token comes from the seeded
        row, then restoring the owner as the env default — callers always name
        the role they mean rather than depending on env state.
        """
        from tests.harness.transport import Transport

        if role not in self._role_identities:
            tenant_id, principal_id = _ROLE_TARGETS[role]
            try:
                self.switch_tenant(tenant_id)
                self.switch_principal(principal_id)
                identity = self.identity_for(Transport.A2A)
                assert identity.auth_token, (
                    f"No access token resolved for role {role!r} ({tenant_id}/{principal_id}) — "
                    "call setup_principals() before building role identities."
                )
                self._role_identities[role] = identity
            finally:
                self.switch_tenant(OWNED_TASK_TENANT)
                self.switch_principal(OWNED_TASK_OWNER)
        return self._role_identities[role]
