"""Admin harness for the two listings of every tenant a deployment holds.

Drives the all-tenants index a super admin lands on (``GET /admin/``, with the filters its form
sends) and the tenant management API's list (``GET /api/v1/tenant-management/tenants``), on the
two admin transports ``AdminAccountEnv`` provides, and seeds the tenants they list.

Both list every tenant the deployment holds, so this env holds no tenant of its own:
``AdminAccountEnv`` stores one when it is entered, which every listing would show beside the
scenario's. The tenants a scenario seeds are then the deployment's only ones, on both
transports: the in-process database is the scenario's own (``integration_db``), and the e2e
stack's is emptied before each scenario (``_reset_e2e_db`` in tests/bdd/conftest.py).

The contract is stated once, in ``tests/bdd/features/BR-ADMIN-TENANT-LISTINGS.feature``, and
runs on both transports through the ``admin-tenant-listings`` row of ``ENV_ROUTES`` in
tests/bdd/conftest.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, NamedTuple

from tests.factories import (
    AccountFactory,
    AdapterConfigFactory,
    AgentAccountAccessFactory,
    MediaBuyFactory,
    PrincipalFactory,
    TenantFactory,
)
from tests.factories.account import DEFAULT_TEST_ACCOUNT_ID
from tests.factories.mint import mint_id
from tests.harness.admin_accounts import AdminAccountEnv
from tests.utils.database_helpers import bound_factory_session


class ListedTenant(NamedTuple):
    """A tenant a scenario seeded: the columns the tenant management API lists, as stored."""

    tenant_id: str
    name: str
    subdomain: str
    is_active: bool
    billing_plan: str
    ad_server: str | None
    #: As the scenario wrote it, which is how the API writes a UTC time: ``isoformat()``.
    created_at: str
    has_adapter_config: bool


class AdminTenantListingEnv(AdminAccountEnv):
    """The admin transports of ``AdminAccountEnv``, in a deployment that holds only the tenants a scenario seeds."""

    def __init__(self, *, mode: str = "integration", base_url: str | None = None) -> None:
        super().__init__(mode=mode, base_url=base_url)
        self._listed: dict[str, ListedTenant] = {}

    def _setup(self) -> None:
        """Store no tenant: each listing shows every tenant the deployment holds."""

    def seed_listed_tenant(
        self,
        name: str,
        *,
        ad_server: str | None,
        holds_media_buy: bool,
        is_active: bool,
        has_adapter_config: bool,
        created_at: str,
    ) -> None:
        """A tenant named *name*, created at *created_at* (ISO 8601), as the scenario describes it.

        *ad_server* is the ad server the tenant names, ``None`` for none. An adapter
        configuration, when it has one, is for that ad server.
        """
        with bound_factory_session():
            tenant = TenantFactory(
                tenant_id=mint_id("listed"),
                name=name,
                ad_server=ad_server,
                is_active=is_active,
                created_at=datetime.fromisoformat(created_at),
            )
            if holds_media_buy:
                # The buy's account and its principal's grant are stored first, each given the
                # tenant itself, so MediaBuyFactory finds both rather than storing them. Given
                # only the tenant id, as MediaBuyFactory and seed_account_with_access give it,
                # the account factories store a tenant of their own too, which every listing shows.
                principal = PrincipalFactory(tenant=tenant, principal_id=mint_id("principal"))
                account = AccountFactory(tenant=tenant, account_id=DEFAULT_TEST_ACCOUNT_ID)
                AgentAccountAccessFactory(tenant=tenant, principal=principal, account=account)
                MediaBuyFactory(tenant=tenant, principal=principal, media_buy_id=mint_id("mb"))
            if has_adapter_config:
                AdapterConfigFactory(tenant=tenant, adapter_type=ad_server)
            self._listed[name] = ListedTenant(
                tenant.tenant_id,
                name,
                tenant.subdomain,
                is_active,
                tenant.billing_plan,
                ad_server,
                created_at,
                has_adapter_config,
            )

    def expected_tenant_list(self, names: list[str]) -> dict[str, Any]:
        """Today's body of the tenant management API's list, naming the seeded tenants *names* in that order."""
        tenants = [self._listed[name] for name in names]
        return {
            "tenants": [
                {
                    "tenant_id": tenant.tenant_id,
                    "name": tenant.name,
                    "subdomain": tenant.subdomain,
                    "is_active": tenant.is_active,
                    "billing_plan": tenant.billing_plan,
                    "ad_server": tenant.ad_server,
                    "created_at": tenant.created_at,
                    "adapter_configured": tenant.has_adapter_config,
                }
                for tenant in tenants
            ],
            "count": len(tenants),
        }
