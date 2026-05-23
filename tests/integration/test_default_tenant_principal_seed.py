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

from src.core.config_loader import _ensure_default_principal, ensure_default_tenant_exists
from src.core.database.database_session import get_db_session
from src.core.database.models import Principal, Tenant

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.fixture
def clean_tenant_table(integration_db):
    """Ensure the tenants/principals tables are empty at the start of each test."""
    with get_db_session() as session:
        session.query(Principal).delete()
        session.query(Tenant).delete()
        session.commit()
    yield
    with get_db_session() as session:
        session.query(Principal).delete()
        session.query(Tenant).delete()
        session.commit()


class TestEnsureDefaultPrincipalFromEnv:
    """The seed reads ADCP_AUTH_TOKEN and creates a Principal idempotently."""

    def test_no_env_var_no_principal(self, clean_tenant_table):
        """Without ADCP_AUTH_TOKEN, the helper is a no-op — production-safe default."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ADCP_AUTH_TOKEN", None)
            with get_db_session() as session:
                # Seed a tenant first so the helper has somewhere to attach to.
                session.add(
                    Tenant(
                        tenant_id="default",
                        name="Default",
                        subdomain="default",
                        ad_server="mock",
                        is_active=True,
                    )
                )
                session.commit()
                _ensure_default_principal(session, "default")
                count = session.scalars(select(Principal).filter_by(tenant_id="default")).all()
                assert len(count) == 0, "No principal should be seeded without ADCP_AUTH_TOKEN"

    def test_with_env_var_seeds_principal(self, clean_tenant_table):
        """With ADCP_AUTH_TOKEN set, the helper creates a Principal with that token."""
        with patch.dict(os.environ, {"ADCP_AUTH_TOKEN": "story-test-token-A"}):
            with get_db_session() as session:
                session.add(
                    Tenant(
                        tenant_id="default",
                        name="Default",
                        subdomain="default",
                        ad_server="mock",
                        is_active=True,
                    )
                )
                session.commit()
                _ensure_default_principal(session, "default")
                principals = session.scalars(select(Principal).filter_by(tenant_id="default")).all()
                assert len(principals) == 1
                assert principals[0].access_token == "story-test-token-A"
                assert principals[0].principal_id == "default_principal"

    def test_idempotent_seed(self, clean_tenant_table):
        """Calling twice does not create duplicates — important because
        ensure_default_tenant_exists may run on every docker compose up."""
        with patch.dict(os.environ, {"ADCP_AUTH_TOKEN": "story-test-token-B"}):
            with get_db_session() as session:
                session.add(
                    Tenant(
                        tenant_id="default",
                        name="Default",
                        subdomain="default",
                        ad_server="mock",
                        is_active=True,
                    )
                )
                session.commit()
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
                assert (
                    session.scalars(select(Principal).filter_by(tenant_id="default")).first() is None
                )

        # Second boot — env var set, principal seeded on the existing tenant.
        with patch.dict(os.environ, {"ADCP_AUTH_TOKEN": "later-token-D"}):
            ensure_default_tenant_exists()
            with get_db_session() as session:
                principal = session.scalars(select(Principal).filter_by(tenant_id="default")).first()
                assert principal is not None
                assert principal.access_token == "later-token-D"
