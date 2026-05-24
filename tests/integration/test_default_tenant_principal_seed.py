"""Integration tests for ensure_default_tenant_exists's principal-seed behavior.

When ADCP_AUTH_TOKEN is set in the environment, ensure_default_tenant_exists
should seed a default Principal with that token so docker compose + storyboard
CI start with an authenticated tenant out of the box. Without the env var, no
principal is created (production-safe default).

Closes the auth-token half of #1308's storyboard prerequisite. The storyboard
runner sends `x-adcp-auth: $ADCP_AUTH_TOKEN` on every authenticated MCP call;
without a matching Principal row, the request fails with "Authentication
token is invalid for tenant 'default'" before reaching the tool.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from sqlalchemy import select

from src.core.config_loader import (
    _ensure_default_principal,
    _ensure_default_storyboard_fixtures,
    ensure_default_tenant_exists,
)
from src.core.database.database_session import get_db_session
from src.core.database.models import (
    CurrencyLimit,
    PricingOption,
    Principal,
    Product,
    PropertyTag,
    Tenant,
)
from tests.factories import TenantFactory

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture
def clean_tenant_table(integration_db):
    """Empty the tenants/principals tables AND bind factory sessions for this test.

    These tests exercise the bootstrap path (config_loader's ensure_default_tenant_exists),
    not a domain env class — so there's no IntegrationEnv to bind factories. We follow the
    pattern from tests/admin/conftest.py:42-44 — open a session, bind ALL_FACTORIES to it,
    yield, then unbind on teardown.
    """
    from sqlalchemy.orm import Session as SASession

    from src.core.database.database_session import get_engine
    from tests.factories import ALL_FACTORIES

    with get_db_session() as session:
        session.query(PricingOption).delete()
        session.query(Product).delete()
        session.query(PropertyTag).delete()
        session.query(CurrencyLimit).delete()
        session.query(Principal).delete()
        session.query(Tenant).delete()
        session.commit()

    factory_session = SASession(bind=get_engine())
    for f in ALL_FACTORIES:
        f._meta.sqlalchemy_session = factory_session

    try:
        yield
    finally:
        for f in ALL_FACTORIES:
            f._meta.sqlalchemy_session = None
        factory_session.close()
        with get_db_session() as session:
            session.query(PricingOption).delete()
            session.query(Product).delete()
            session.query(PropertyTag).delete()
            session.query(CurrencyLimit).delete()
            session.query(Principal).delete()
            session.query(Tenant).delete()
            session.commit()


class TestEnsureDefaultPrincipalFromEnv:
    """The seed reads ADCP_AUTH_TOKEN and creates a Principal idempotently."""

    def test_no_env_var_no_principal(self, clean_tenant_table):
        """Without ADCP_AUTH_TOKEN, the helper is a no-op — production-safe default."""
        TenantFactory(tenant_id="default", subdomain="default")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ADCP_AUTH_TOKEN", None)
            with get_db_session() as session:
                _ensure_default_principal(session, "default")
                count = session.scalars(select(Principal).filter_by(tenant_id="default")).all()
                assert len(count) == 0, "No principal should be seeded without ADCP_AUTH_TOKEN"

    def test_with_env_var_seeds_principal(self, clean_tenant_table):
        """With ADCP_AUTH_TOKEN set, the helper creates a Principal with that token."""
        TenantFactory(tenant_id="default", subdomain="default")
        with patch.dict(os.environ, {"ADCP_AUTH_TOKEN": "story-test-token-A"}):
            with get_db_session() as session:
                _ensure_default_principal(session, "default")
                principals = session.scalars(select(Principal).filter_by(tenant_id="default")).all()
                assert len(principals) == 1
                assert principals[0].access_token == "story-test-token-A"
                assert principals[0].principal_id == "default_principal"

    def test_idempotent_seed(self, clean_tenant_table):
        """Calling twice does not create duplicates — important because
        ensure_default_tenant_exists may run on every docker compose up."""
        TenantFactory(tenant_id="default", subdomain="default")
        with patch.dict(os.environ, {"ADCP_AUTH_TOKEN": "story-test-token-B"}):
            with get_db_session() as session:
                _ensure_default_principal(session, "default")
                _ensure_default_principal(session, "default")
                _ensure_default_principal(session, "default")
                principals = session.scalars(select(Principal).filter_by(tenant_id="default")).all()
                assert len(principals) == 1, "Repeated calls must not create duplicate principals"


class TestEnsureDefaultTenantWiresSeed:
    """The public ensure_default_tenant_exists wires the principal seed into both
    the create-new-tenant path and the tenant-already-exists path."""

    def test_new_tenant_creation_seeds_principal(self, clean_tenant_table):
        with patch.dict(os.environ, {"ADCP_AUTH_TOKEN": "fresh-token-C"}):
            ensure_default_tenant_exists()
            with get_db_session() as session:
                principal = session.scalars(select(Principal).filter_by(tenant_id="default")).first()
                assert principal is not None
                assert principal.access_token == "fresh-token-C"

    def test_existing_tenant_path_still_seeds_principal(self, clean_tenant_table):
        """If the tenant was created on a prior boot without the env var, a later
        boot with ADCP_AUTH_TOKEN set should still seed the principal."""
        # First boot — no env var, tenant created but no principal.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ADCP_AUTH_TOKEN", None)
            ensure_default_tenant_exists()
            with get_db_session() as session:
                assert session.scalars(select(Principal).filter_by(tenant_id="default")).first() is None

        # Second boot — env var set, principal seeded on the existing tenant.
        with patch.dict(os.environ, {"ADCP_AUTH_TOKEN": "later-token-D"}):
            ensure_default_tenant_exists()
            with get_db_session() as session:
                principal = session.scalars(select(Principal).filter_by(tenant_id="default")).first()
                assert principal is not None
                assert principal.access_token == "later-token-D"


class TestEnsureDefaultStoryboardFixtures:
    """The storyboard-fixtures seed creates the CurrencyLimit/PropertyTag/Product/
    PricingOption chain so storyboard get_products scenarios find at least one
    product to return.

    Without these fixtures, refine_products / inventory_list_targeting /
    inventory_list_no_match all fail the storyboard validator's
    ``field_present: products[0].product_id`` check because get_products returns
    an empty list.
    """

    def test_no_env_var_no_fixtures(self, clean_tenant_table):
        """Without ADCP_AUTH_TOKEN, the helper is a no-op — production-safe.

        Product is the cleanest assertion target: TenantFactory auto-creates a
        USD CurrencyLimit via RelatedFactory (tests/factories/core.py:56-60), so
        we can't assert CurrencyLimit absence here. PropertyTag is similarly
        outside our seed contract. The helper's job is to seed *products*
        (with their dependency chain); the production-safe gate is that no
        products are created without the env var.
        """
        TenantFactory(tenant_id="default", subdomain="default")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ADCP_AUTH_TOKEN", None)
            with get_db_session() as session:
                _ensure_default_storyboard_fixtures(session, "default")
                assert session.scalars(select(Product).filter_by(tenant_id="default")).all() == []
                assert session.scalars(select(PricingOption).filter_by(tenant_id="default")).all() == []

    def test_with_env_var_seeds_full_chain(self, clean_tenant_table):
        """With ADCP_AUTH_TOKEN, the helper creates CurrencyLimit + PropertyTag +
        Product + PricingOption — the full chain a storyboard get_products call needs."""
        TenantFactory(tenant_id="default", subdomain="default")
        with patch.dict(os.environ, {"ADCP_AUTH_TOKEN": "fixtures-token-E"}):
            with get_db_session() as session:
                _ensure_default_storyboard_fixtures(session, "default")

                currencies = session.scalars(select(CurrencyLimit).filter_by(tenant_id="default")).all()
                assert len(currencies) == 1
                assert currencies[0].currency_code == "USD"

                tags = session.scalars(select(PropertyTag).filter_by(tenant_id="default")).all()
                assert len(tags) == 1
                assert tags[0].tag_id == "all_inventory"

                products = session.scalars(select(Product).filter_by(tenant_id="default")).all()
                assert len(products) == 1
                assert products[0].product_id == "default_display"
                # Storyboard validator checks field_present at products[0].product_id —
                # the value must be a non-empty string for the field_present check to pass.
                assert isinstance(products[0].product_id, str) and products[0].product_id

                pricing = session.scalars(
                    select(PricingOption).filter_by(tenant_id="default", product_id="default_display")
                ).all()
                assert len(pricing) == 1
                assert pricing[0].pricing_model == "cpm"
                assert pricing[0].currency == "USD"

    def test_idempotent_seed(self, clean_tenant_table):
        """Repeated calls do not create duplicates — docker compose up runs may
        invoke this multiple times across restarts."""
        TenantFactory(tenant_id="default", subdomain="default")
        with patch.dict(os.environ, {"ADCP_AUTH_TOKEN": "fixtures-token-F"}):
            with get_db_session() as session:
                _ensure_default_storyboard_fixtures(session, "default")
                _ensure_default_storyboard_fixtures(session, "default")
                _ensure_default_storyboard_fixtures(session, "default")

                assert len(session.scalars(select(CurrencyLimit).filter_by(tenant_id="default")).all()) == 1
                assert len(session.scalars(select(PropertyTag).filter_by(tenant_id="default")).all()) == 1
                assert len(session.scalars(select(Product).filter_by(tenant_id="default")).all()) == 1
                assert (
                    len(
                        session.scalars(
                            select(PricingOption).filter_by(tenant_id="default", product_id="default_display")
                        ).all()
                    )
                    == 1
                )

    def test_ensure_default_tenant_wires_fixtures_on_create(self, clean_tenant_table):
        """The public ensure_default_tenant_exists wires the fixture seed into the
        create-new-tenant path so a fresh docker compose up has products available."""
        with patch.dict(os.environ, {"ADCP_AUTH_TOKEN": "wired-token-G"}):
            ensure_default_tenant_exists()
            with get_db_session() as session:
                product = session.scalars(select(Product).filter_by(tenant_id="default")).first()
                assert product is not None
                assert product.product_id == "default_display"

    def test_ensure_default_tenant_wires_fixtures_on_existing(self, clean_tenant_table):
        """The existing-tenant path also seeds fixtures so prior boots without
        env var get backfilled on a later boot with it set."""
        # First boot: tenant created, no env var, no fixtures.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ADCP_AUTH_TOKEN", None)
            ensure_default_tenant_exists()
            with get_db_session() as session:
                assert session.scalars(select(Product).filter_by(tenant_id="default")).first() is None

        # Second boot: env var set, fixtures seeded.
        with patch.dict(os.environ, {"ADCP_AUTH_TOKEN": "backfill-token-H"}):
            ensure_default_tenant_exists()
            with get_db_session() as session:
                product = session.scalars(select(Product).filter_by(tenant_id="default")).first()
                assert product is not None
