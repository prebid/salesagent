"""Harness for the admin PRINCIPALS surface — the routes that issue tenant credentials.

Sibling of :mod:`tests.harness.admin_accounts`, kept separate rather than bolted onto it:
that env is named for accounts and owns account seeding and cleanup, and a principals
method on it would be findable only by someone who already knew to look there.

Integration transport only, deliberately. ``AdminAccountEnv`` carries both
``integration`` and ``e2e`` because ``BR-ADMIN-ACCOUNTS.feature`` declares both and BDD
parametrizes over them. Nothing drives this env from BDD yet, so an e2e mode here would be
a transport with no caller — added when the BDD coverage that needs it is written, not
before.

Why this exists at all rather than the test reading the database itself:
``tests/unit/test_architecture_repository_pattern.py`` forbids ``get_db_session()`` in test
bodies and its allowlist only shrinks. DB access belongs in the harness, which is not
scanned — the same split ``AdminAccountEnv.get_account_from_db`` already uses.
"""

from __future__ import annotations

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import AuditLog, Principal
from tests.harness.admin_client import AdminClient, AdminResponse


class AdminPrincipalEnv:
    """Drive the admin principals routes, and read what they wrote."""

    DEFAULT_TENANT_ID = "admin_principal_tenant"

    def __init__(self, *, tenant_id: str | None = None) -> None:
        """Deliberately NOT a context manager.

        ``tests/harness/test_harness_base.py`` allows ``__enter__``/``__exit__`` in exactly
        two declared homes — ``BaseTestEnv``, which owns the one unwind guard, and
        ``AdminAccountEnv``, a named and separately tested exception — and that list only
        shrinks, so a third home is not the way to add an env. The rule is also right here
        on its own terms: this env acquires nothing that needs releasing. Each request
        opens a short-lived :class:`AdminClient` and closes it before returning, so there
        is no resource for an unwind guard to protect and the lifecycle methods would have
        been ceremony around a plain constructor.
        """
        self._tenant_id = tenant_id or self.DEFAULT_TENANT_ID

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    # ── seeding ────────────────────────────────────────────────────────────

    def seed_principal(self, *, principal_id: str) -> str:
        """Create the tenant and one principal, returning the principal id.

        Factories are bound to the session this method owns. Binding every factory rather
        than the two named here is not defensive: ``PrincipalFactory`` declares
        ``tenant = SubFactory(TenantFactory)`` and reaches siblings, and an unbound one
        raises ``RuntimeError("No session provided.")`` from inside factory_boy rather than
        at the call site. ``ALL_FACTORIES`` is the list ``IntegrationEnv`` itself binds.
        """
        from tests.factories import ALL_FACTORIES, PrincipalFactory, TenantFactory

        with get_db_session() as session:
            for factory in ALL_FACTORIES:
                factory._meta.sqlalchemy_session = session
            try:
                tenant = TenantFactory(tenant_id=self._tenant_id)
                PrincipalFactory(tenant=tenant, principal_id=principal_id)
            finally:
                for factory in ALL_FACTORIES:
                    factory._meta.sqlalchemy_session = None
        return principal_id

    # ── requests ───────────────────────────────────────────────────────────

    def post_rotate_token(
        self,
        principal_id: str,
        *,
        authenticated: bool,
        form: dict[str, str] | None = None,
    ) -> AdminResponse:
        """POST the rotate-token route, with or without an admin session."""
        client = AdminClient()
        try:
            if authenticated:
                client.authenticate(self._tenant_id)
            return client.request(
                "post", f"/tenant/{self._tenant_id}/principal/{principal_id}/rotate-token", data=form or {}
            )
        finally:
            client.close()

    # ── reads ──────────────────────────────────────────────────────────────

    def token_hash(self, principal_id: str) -> str:
        """The STORED form of the principal's token.

        The plaintext is never stored — ``credentials.hash_token`` is what the column
        holds — so the hash is the only way to observe that a rotation happened.
        """
        with get_db_session() as session:
            return session.scalars(select(Principal).filter_by(principal_id=principal_id)).one().token_hash

    def audit_rows(self, operation: str) -> list[AuditLog]:
        """Every audit row this tenant holds for *operation*."""
        with get_db_session() as session:
            return list(session.scalars(select(AuditLog).filter_by(tenant_id=self._tenant_id, operation=operation)))
