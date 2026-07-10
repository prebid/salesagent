"""Integration tests for TMP Provider feature.

Three end-to-end scenarios exercised against a real PostgreSQL database:

1. test_discovery_returns_active_providers
   Discovery endpoint (GET /tenant/{id}/tmp-providers/discovery) returns
   active/draining providers and excludes inactive ones.

2. test_sync_packages_posts_to_providers
   sync_packages_for_media_buy fans out to all syncable providers; outbound
   HTTP is stubbed via unittest.mock.patch so no real network calls are made.

3. test_health_scheduler_tick_persists_status
   TMPHealthScheduler.tick() probes providers (HTTP stubbed) and persists the
   resulting health_status to the DB.

beads: salesagent-tmp-sync
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from tests.factories import MediaBuyFactory, MediaPackageFactory, TenantFactory, TMPProviderFactory
from tests.harness._base import IntegrationEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# Shared integration env — no external patches (we patch inline per test)
# ---------------------------------------------------------------------------


class _TMPEnv(IntegrationEnv):
    """Bare integration env for TMP tests — external patches applied inline."""

    EXTERNAL_PATCHES: dict[str, str] = {}


# ---------------------------------------------------------------------------
# 1. Discovery endpoint returns active providers
# ---------------------------------------------------------------------------


class TestDiscoveryReturnsActiveProviders:
    """GET /tenant/{id}/tmp-providers/discovery returns active+draining, excludes inactive."""

    def test_discovery_returns_active_providers(self, integration_db):
        """Active and draining providers appear in the discovery response; inactive do not."""
        from starlette.testclient import TestClient

        from src.app import app

        with _TMPEnv() as env:
            tenant = TenantFactory(tenant_id="tmp_int_disc_t1")
            TMPProviderFactory(tenant=tenant, name="Active Provider", status="active")
            TMPProviderFactory(tenant=tenant, name="Draining Provider", status="draining")
            TMPProviderFactory(tenant=tenant, name="Inactive Provider", status="inactive")
            env._commit_factory_data()

        with patch.dict(os.environ, {"TMP_DISCOVERY_API_KEYS": "OPEN"}):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/tenant/tmp_int_disc_t1/tmp-providers/discovery")

        assert response.status_code == 200
        data = response.json()
        assert data["tenant_id"] == "tmp_int_disc_t1"
        names = {p["name"] for p in data["providers"]}
        assert "Active Provider" in names
        assert "Draining Provider" in names
        assert "Inactive Provider" not in names


# ---------------------------------------------------------------------------
# 2. sync_packages_for_media_buy fans out to all syncable providers
# ---------------------------------------------------------------------------


class TestSyncPackagesPostsToProviders:
    """sync_packages_for_media_buy POSTs to every syncable provider (HTTP stubbed)."""

    def test_sync_packages_posts_to_providers(self, integration_db):
        """With two active providers and one package, _post_packages_sync is called twice."""
        with _TMPEnv() as env:
            tenant = TenantFactory(tenant_id="tmp_int_sync_t1")
            mb = MediaBuyFactory(tenant=tenant)
            MediaPackageFactory(
                media_buy=mb,
                package_config={
                    "product_id": "prod-001",
                    "name": "Test Package",
                    "is_active": True,
                },
            )
            TMPProviderFactory(
                tenant=tenant,
                name="Provider Alpha",
                endpoint="https://alpha.example.com/tmp",
                status="active",
            )
            TMPProviderFactory(
                tenant=tenant,
                name="Provider Beta",
                endpoint="https://beta.example.com/tmp",
                status="active",
            )
            env._commit_factory_data()
            media_buy_id = mb.media_buy_id
            tenant_id = tenant.tenant_id

        from src.services.tmp_provider_sync import sync_packages_for_media_buy

        with (
            patch("src.services.tmp_provider_sync._post_packages_sync") as mock_post,
            patch(
                "src.services.tmp_provider_sync._resolve_seller_agent_url",
                return_value="http://salesagent:8000/mcp",
            ),
        ):
            sync_packages_for_media_buy(tenant_id, media_buy_id)

        # Both providers must have been called
        assert mock_post.call_count == 2
        called_endpoints = {call.args[0] for call in mock_post.call_args_list}
        assert called_endpoints == {
            "https://alpha.example.com/tmp",
            "https://beta.example.com/tmp",
        }
        # No auth credentials set on either provider
        called_auths = {call.args[2] for call in mock_post.call_args_list}
        assert called_auths == {""}


# ---------------------------------------------------------------------------
# 3. TMPHealthScheduler.tick() persists health_status to DB
# ---------------------------------------------------------------------------


class TestHealthSchedulerTickPersistsStatus:
    """TMPHealthScheduler.tick() writes health_status to the DB after probing."""

    def test_health_scheduler_tick_persists_status(self, integration_db):
        """After tick(), the provider's health_status column is updated in the DB."""
        import asyncio

        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import TMPProvider
        from src.services.tmp_health_scheduler import TMPHealthScheduler

        with _TMPEnv() as env:
            tenant = TenantFactory(tenant_id="tmp_int_health_t1")
            provider = TMPProviderFactory(
                tenant=tenant,
                name="Health Provider",
                endpoint="https://health.example.com/tmp",
                status="active",
            )
            env._commit_factory_data()
            provider_id = provider.provider_id

        # Stub the HTTP probe so no real network call is made
        with patch(
            "src.services.tmp_health_scheduler._check_provider_health",
            new=AsyncMock(return_value="healthy"),
        ):
            scheduler = TMPHealthScheduler()
            asyncio.run(scheduler.tick())

        # Verify the health_status was persisted
        with get_db_session() as session:
            stmt = select(TMPProvider).filter_by(provider_id=provider_id)
            updated = session.scalars(stmt).first()

        assert updated is not None
        assert updated.health_status == "healthy"
        assert updated.last_health_checked_at is not None
