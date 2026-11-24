"""Saving AI settings no longer stores a tenant Logfire token.

A tenant's ``logfire_token`` used to configure Logfire for the whole process. Per-tenant
Logfire is no longer supported, so the AI settings form neither accepts a new token nor
carries a stored one forward.
"""

from __future__ import annotations

import pytest

from src.core.database.models import Tenant, UserRole
from tests.helpers.admin_session import tenant_user_session

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "logfire_drop_tenant"
ADMIN = "admin@logfire-drop.example"


def test_saving_ai_settings_drops_the_tenant_logfire_token(integration_db, test_admin_app):
    from tests.factories import TenantFactory, UserFactory
    from tests.harness._base import IntegrationEnv

    with IntegrationEnv(tenant_id=TENANT_ID) as env:
        tenant = TenantFactory(
            tenant_id=TENANT_ID,
            subdomain="logfire-drop",
            ai_config={"provider": "anthropic", "model": "claude-x", "logfire_token": "pylf_v1_us_stored"},
        )
        UserFactory(tenant=tenant, user_id="logfire_drop_admin", email=ADMIN, role=UserRole.ADMIN)

        client = test_admin_app.test_client()
        tenant_user_session(client, ADMIN)
        response = client.post(
            f"/tenant/{TENANT_ID}/settings/ai",
            data={"ai_provider": "anthropic", "ai_model": "claude-x", "logfire_token": "pylf_v1_us_submitted"},
        )
        assert response.status_code == 302

        env.get_session().expire_all()
        ai_config = env.get_one(Tenant, tenant_id=TENANT_ID).ai_config
        assert (ai_config["provider"], "logfire_token" in ai_config) == ("anthropic", False)
