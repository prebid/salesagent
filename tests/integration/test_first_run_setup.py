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
from tests.helpers.envelope_assertions import assert_envelope_shape

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
    # Presented and rejected: AUTH_INVALID (401, terminal), refused by the resolver before
    # the tool runs. Without it the 200 above proves nothing about the token.
    assert refused.status_code == 401, refused.text
    assert_envelope_shape(refused.json(), "AUTH_INVALID", recovery="terminal")


@pytest.fixture
def single_tenant_admin(admin_client, monkeypatch):
    """The Admin UI of a single-tenant deployment (the default), requested from a host naming no tenant."""
    monkeypatch.setattr("src.core.config_loader.is_single_tenant_mode", lambda: True)
    return admin_client


def test_the_single_tenant_admin_entry_reaches_the_tenant_setup_created(bound_factory_session, single_tenant_admin):
    """``/admin/`` names the deployment's one tenant, whatever its id: nothing creates ``default`` (#2260).

    The request's host (the test client's ``localhost``) names no tenant, so this grades the
    fallback to the one active tenant, and that its sign-in page exists rather than 404ing.
    """
    create_tenant(TenantSetup(name="Acme", virtual_host="acme.adcp.test", tenant_id="acme"))

    entry = single_tenant_admin.get("/admin/")
    assert entry.status_code == 302, entry.text
    assert entry.headers["Location"].endswith("/tenant/acme"), entry.headers["Location"]

    dashboard = single_tenant_admin.get("/tenant/acme")
    assert dashboard.status_code == 302, "an unauthenticated visitor is sent to sign in"
    sign_in = single_tenant_admin.get(dashboard.headers["Location"])
    assert sign_in.status_code in (200, 302), f"the tenant's sign-in answered {sign_in.status_code}"


@pytest.mark.parametrize("tenants", [0, 2], ids=["empty-database", "two-tenants-host-names-neither"])
def test_the_single_tenant_admin_entry_without_one_tenant_goes_to_sign_in(
    bound_factory_session, single_tenant_admin, tenants
):
    """No tenant to name: ``/admin/`` sends the visitor to sign in, where a super admin creates one."""
    for i in range(tenants):
        create_tenant(TenantSetup(name=f"Seller {i}", virtual_host=f"seller{i}.adcp.test", tenant_id=f"seller{i}"))

    entry = single_tenant_admin.get("/admin/")
    assert entry.status_code == 302, entry.text
    assert entry.headers["Location"].endswith("/login"), entry.headers["Location"]
    assert single_tenant_admin.get("/login").status_code in (200, 302)
