"""First-run setup is a command an operator runs, never something the serving start does (#2260).

Graded end to end against real PostgreSQL and read back from the rows, with the commands
driven through the same entry points the operator and docker compose use:

* the serving start's database step (``scripts/ops/migrate.py``) on an empty database
  creates no tenant, even with a deployment host declared in single-tenant mode, which is
  exactly the configuration in which it used to create ``default``;
* ``setup_tenant`` creates what its docstring lists and nothing else, and refuses a
  second run for a taken key without writing anything;
* ``demo_tenant`` runs only while no tenant exists, is a no-op after that, and the tenant
  it creates answers a buyer presenting the token it printed and ``x-adcp-tenant``.

None of this mirrors argparse. ``setup_tenant`` is driven through :class:`TenantSetup`, whose
fields all default but two, so a new CLI option does not touch these tests (the deleted
``class Args:`` mirrors broke on exactly that). ``demo_tenant`` is driven through ``main``
with the one option docker compose passes it.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import func, select, text
from starlette.testclient import TestClient

from scripts.setup.demo_tenant import DEMO_PRODUCTS, DEMO_TENANT_ID, provision_demo
from scripts.setup.demo_tenant import main as demo_main
from scripts.setup.setup_tenant import (
    DEFAULT_CURRENCIES,
    TenantAlreadyExistsError,
    TenantSetup,
    create_tenant,
)
from src.core.credentials import hash_token
from src.core.database.models import AdapterConfig, CurrencyLimit, PricingOption, Product, Tenant, User
from src.core.database.repositories.principal_lookup import find_principal_by_token_hash
from tests.factories import TenantFactory
from tests.helpers.credentials import credential_headers

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

#: Every table a tenant-creating command may write to, counted before and after a refused run.
_COUNTED = (Tenant, AdapterConfig, CurrencyLimit, User, Product, PricingOption)


def _counts(session) -> dict[str, int]:
    session.expire_all()
    return {model.__name__: session.scalar(select(func.count()).select_from(model)) for model in _COUNTED}


def test_the_serving_start_creates_no_tenant_on_an_empty_database(migration_db, monkeypatch):
    """Migrations on an empty database leave it without a tenant.

    Declares ``ADCP_AGENT_URL`` in single-tenant mode: before #2260 that made the migration
    step create a ``default`` tenant (``ensure_default_tenant_exists``) and ``init_db`` a
    second path to one, so a deployment always had a tenant nobody created.
    """
    from scripts.ops.migrate import run_migrations
    from src.core.database.database_session import reset_engine

    engine, db_url = migration_db
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("ADCP_AGENT_URL", "https://first-run.adcp.test")
    monkeypatch.delenv("ADCP_MULTI_TENANT", raising=False)
    # The settings and the engine are process-wide caches; drop both so this run reads the
    # environment above, and restore them afterwards.
    monkeypatch.setattr("src.core.config._settings", None)
    reset_engine()
    try:
        run_migrations(exit_on_error=False)
    finally:
        reset_engine()

    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM alembic_version")).scalar() == 1, "migrations did not run"
        tenants = conn.execute(text("SELECT tenant_id FROM tenants")).scalars().all()
    assert tenants == [], f"the serving start created tenants {tenants}; it must provision nothing"


def test_setup_tenant_creates_exactly_what_it_says(bound_factory_session):
    setup = TenantSetup(
        name="Acme Publisher",
        virtual_host="acme.adcp.test",
        tenant_id="acme",
        admin_email="Ops@Acme.example",
        authorized_domains=("acme.example",),
    )

    created = create_tenant(setup)

    session = bound_factory_session
    assert _counts(session) == {
        "Tenant": 1,
        "AdapterConfig": 1,
        "CurrencyLimit": len(DEFAULT_CURRENCIES),
        "User": 1,
        "Product": 0,
        "PricingOption": 0,
    }
    tenant = session.get(Tenant, "acme")
    assert (tenant.name, tenant.subdomain, tenant.virtual_host, tenant.ad_server) == (
        "Acme Publisher",
        "acme",
        "acme.adcp.test",
        "mock",
    )
    # Lists, not JSON-encoded strings: the old script passed json.dumps() to JSONType columns.
    assert tenant.authorized_emails == ["ops@acme.example"]
    assert tenant.authorized_domains == ["acme.example"]
    assert session.scalars(select(AdapterConfig.adapter_type).filter_by(tenant_id="acme")).all() == ["mock"]
    assert sorted(session.scalars(select(CurrencyLimit.currency_code).filter_by(tenant_id="acme"))) == sorted(
        DEFAULT_CURRENCIES
    )
    user = session.scalars(select(User).filter_by(tenant_id="acme")).one()
    assert (user.email, user.role) == ("ops@acme.example", "admin")

    principal = find_principal_by_token_hash(session, hash_token(created.token))
    assert principal is not None, "the printed token does not resolve to a principal"
    assert (principal.tenant_id, principal.principal_id) == ("acme", created.principal_id)
    assert principal.platform_mappings == {"mock": {"advertiser_id": "mock_acme"}}


@pytest.mark.parametrize(
    "second",
    [
        TenantSetup(name="Again", virtual_host="other.adcp.test", tenant_id="acme"),
        TenantSetup(name="Again", virtual_host="other.adcp.test", tenant_id="acme2", subdomain="acme"),
        TenantSetup(name="Again", virtual_host="ACME.adcp.test:8443", tenant_id="acme2"),
    ],
    ids=["same-id", "same-subdomain", "same-host"],
)
def test_setup_tenant_refuses_a_taken_key_and_writes_nothing(bound_factory_session, second):
    create_tenant(TenantSetup(name="Acme", virtual_host="acme.adcp.test", tenant_id="acme"))
    before = _counts(bound_factory_session)

    with pytest.raises(TenantAlreadyExistsError):
        create_tenant(second)

    assert _counts(bound_factory_session) == before


def test_the_demo_command_refuses_once_any_tenant_exists(bound_factory_session):
    TenantFactory(tenant_id="already_here", virtual_host="already-here.adcp.test")
    bound_factory_session.commit()
    before = _counts(bound_factory_session)

    assert provision_demo("localhost:8000") is None
    assert _counts(bound_factory_session) == before
    assert bound_factory_session.get(Tenant, DEMO_TENANT_ID) is None


def test_the_demo_command_is_a_no_op_the_second_time(bound_factory_session, capsys):
    assert demo_main(["--virtual-host", "localhost:8000"]) == 0
    first = _counts(bound_factory_session)
    capsys.readouterr()

    assert demo_main(["--virtual-host", "localhost:8000"]) == 0

    assert _counts(bound_factory_session) == first
    assert "tok_" not in capsys.readouterr().out, "a second run must not mint or print a credential"


def test_the_demo_tenant_answers_a_buyer_with_the_printed_token(bound_factory_session, capsys):
    from src.app import app

    assert demo_main(["--virtual-host", "localhost:8000"]) == 0
    printed = re.findall(r"\btok_[A-Za-z0-9_-]+", capsys.readouterr().out)
    assert len(set(printed)) == 1, f"expected exactly one token in the output, found {printed}"
    token = printed[0]

    session = bound_factory_session
    session.expire_all()
    assert session.get(Tenant, DEMO_TENANT_ID).ad_server == "mock"
    assert sorted(session.scalars(select(Product.product_id).filter_by(tenant_id=DEMO_TENANT_ID))) == sorted(
        p["product_id"] for p in DEMO_PRODUCTS
    )

    client = TestClient(app)
    answered = client.post("/api/v1/accounts", json={}, headers=credential_headers(token=token, tenant=DEMO_TENANT_ID))
    assert answered.status_code == 200, answered.text

    refused = client.post(
        "/api/v1/accounts", json={}, headers=credential_headers(token="tok_not-the-demo-token", tenant=DEMO_TENANT_ID)
    )
    assert refused.status_code in (401, 403), (
        f"a wrong token was answered {refused.status_code}; the 200 above proves nothing about the token"
    )
