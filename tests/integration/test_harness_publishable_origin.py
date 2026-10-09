"""``_write_publishable_origin`` gives a tenant with no dotted host a publishable one.

The branch runs only for a tenant whose ``virtual_host`` is a single label; every factory
tenant is dotted, so no scenario reaches it. The host it writes is built from the tenant id,
which may hold underscores, so it goes through ``dns_label``: ``validate_virtual_host``
refuses an underscore at assignment.

Each test reads the row back from the database rather than trusting the returned value, so
a write that is never committed fails here.
"""

from __future__ import annotations

from src.core.database.repositories.tenant_lookup import TenantLookupRepository
from tests.harness._base import BareIntegrationEnv
from tests.harness._mixins import _write_publishable_origin


def _stored_tenant_id_at(env: BareIntegrationEnv, host: str) -> str | None:
    env._session.expire_all()
    tenant = TenantLookupRepository(env._session).find_by_virtual_host(host)
    return tenant.tenant_id if tenant is not None else None


def test_a_single_label_host_is_replaced_with_a_dotted_one(integration_db) -> None:
    with BareIntegrationEnv(tenant_id="publishable_origin_t") as env:
        env.setup_default_data(virtual_host="single-label")

        assert _write_publishable_origin(env) == "publishable-origin-t.example.com"
        assert _stored_tenant_id_at(env, "publishable-origin-t.example.com") == "publishable_origin_t"


def test_a_dotted_host_is_kept(integration_db) -> None:
    with BareIntegrationEnv(tenant_id="publishable_origin_kept") as env:
        env.setup_default_data(virtual_host="kept.example.com")

        assert _write_publishable_origin(env) == "kept.example.com"
        assert _stored_tenant_id_at(env, "kept.example.com") == "publishable_origin_kept"
