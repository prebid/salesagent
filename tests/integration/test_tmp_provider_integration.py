"""Integration tests for TMP Provider feature.

Four end-to-end scenarios exercised against a real PostgreSQL database:

1. test_discovery_returns_active_providers
   Discovery endpoint (GET /tenant/{id}/tmp-providers/discovery) returns
   active/draining providers and excludes inactive ones.

2. test_sync_packages_posts_to_providers
   sync_packages_for_media_buy fans out to all syncable providers; outbound
   HTTP is stubbed at the httpx.Client level (not at _post_packages_sync) so
   the full sync path — URL construction, auth header, body shape — is graded.

3. test_health_scheduler_tick_persists_status
   TMPHealthScheduler.tick() probes providers (HTTP stubbed) and persists the
   resulting health_status to the DB.

4. test_fire_tmp_sync_dispatched_posts_to_providers
   fire_tmp_sync() spawns a daemon thread; the thread completes and the
   outbound POST is asserted (transport-parameterized: MCP response shape).

beads: salesagent-tmp-sync
"""

from __future__ import annotations

import os
import threading
from unittest.mock import AsyncMock, MagicMock, patch

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


def _make_mock_http_client(status_code: int = 200) -> MagicMock:
    """Return a mock httpx.Client context manager whose .post() returns *status_code*.

    Used by SF-3 and SF-4 tests to stub outbound HTTP at the httpx.Client level
    rather than at _post_packages_sync, so the full sync path (URL construction,
    auth header, body serialisation) is exercised against real production code.
    """
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.raise_for_status = MagicMock(return_value=None)

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_response
    return mock_client


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
    """sync_packages_for_media_buy POSTs to every syncable provider (HTTP stubbed at httpx level)."""

    def test_sync_packages_posts_to_providers(self, integration_db):
        """With two active providers and one package, httpx.Client.post is called twice.

        Stubs httpx.Client (not _post_packages_sync) so the full sync path is
        graded: URL construction via provider_url(), auth header via bearer_headers(),
        and JSON body shape from _build_package_payload().
        """
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

        mock_client = _make_mock_http_client(200)
        with (
            patch("src.services.tmp_provider_sync.httpx.Client", return_value=mock_client),
            patch.dict(os.environ, {"ADCP_AGENT_URL": "https://salesagent.example.com/mcp"}),
        ):
            sync_packages_for_media_buy(tenant_id, media_buy_id)

        # Both providers must have been called
        assert mock_client.post.call_count == 2

        # Assert the URLs hit — provider_url() appends /packages/sync
        called_urls = {call.args[0] for call in mock_client.post.call_args_list}
        assert called_urls == {
            "https://alpha.example.com/tmp/packages/sync",
            "https://beta.example.com/tmp/packages/sync",
        }

        # No auth credentials on either provider → no Authorization header
        for call in mock_client.post.call_args_list:
            headers = call.kwargs.get("headers", call.args[2] if len(call.args) > 2 else {})
            assert "Authorization" not in headers

        # Body must be a list of AvailablePackage dicts with required fields
        for call in mock_client.post.call_args_list:
            body = call.kwargs.get("json", call.args[1] if len(call.args) > 1 else None)
            assert isinstance(body, list)
            assert len(body) == 1
            pkg_payload = body[0]
            assert "package_id" in pkg_payload
            assert "media_buy_id" in pkg_payload
            assert pkg_payload["media_buy_id"] == media_buy_id
            assert "seller_agent" in pkg_payload
            assert pkg_payload["seller_agent"]["agent_url"] == "https://salesagent.example.com/mcp"


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


# ---------------------------------------------------------------------------
# 4. fire_tmp_sync dispatched transport-parameterized test
# ---------------------------------------------------------------------------


class TestFireTmpSyncDispatched:
    """fire_tmp_sync() spawns a daemon thread that POSTs to active providers.

    Graded at the httpx.Client level (not _post_packages_sync) so the full
    dispatch path — thread spawn, URL construction, auth header, body shape —
    is exercised against real production code.

    Transport-parameterised: the MCP transport wraps the domain response in a
    CreateMediaBuyResult envelope; fire_tmp_sync() must unwrap it to extract
    media_buy_id.
    """

    def test_fire_tmp_sync_dispatched_posts_to_providers(self, integration_db):
        """fire_tmp_sync() spawns a thread that POSTs packages to active providers.

        Uses the MCP response shape (CreateMediaBuyResult wrapper with .response
        inner object) to verify fire_tmp_sync() correctly unwraps the envelope.
        """
        with _TMPEnv() as env:
            tenant = TenantFactory(tenant_id="tmp_int_fire_t1")
            mb = MediaBuyFactory(tenant=tenant)
            MediaPackageFactory(
                media_buy=mb,
                package_config={
                    "product_id": "prod-fire-001",
                    "name": "Fire Test Package",
                    "is_active": True,
                },
            )
            TMPProviderFactory(
                tenant=tenant,
                name="Fire Provider",
                endpoint="https://fire.example.com/tmp",
                status="active",
            )
            env._commit_factory_data()
            media_buy_id = mb.media_buy_id
            tenant_id = tenant.tenant_id

        from src.services.tmp_provider_sync import fire_tmp_sync

        # MCP transport shape: CreateMediaBuyResult wraps the domain response.
        # fire_tmp_sync() reads response.response.media_buy_id (inner object).
        inner_response = MagicMock()
        inner_response.media_buy_id = media_buy_id
        mcp_response = MagicMock()
        mcp_response.media_buy_id = None  # not on the wrapper
        mcp_response.response = inner_response

        identity = MagicMock()
        identity.tenant_id = tenant_id

        mock_client = _make_mock_http_client(200)

        # Collect the spawned thread so we can join it before asserting
        spawned_threads: list[threading.Thread] = []
        original_start = threading.Thread.start

        def _track_start(self_thread: threading.Thread) -> None:
            spawned_threads.append(self_thread)
            original_start(self_thread)

        with (
            patch("src.services.tmp_provider_sync.httpx.Client", return_value=mock_client),
            patch.dict(os.environ, {"ADCP_AGENT_URL": "https://salesagent.example.com/mcp"}),
            patch.object(threading.Thread, "start", _track_start),
        ):
            fire_tmp_sync(mcp_response, identity)

        # Join the daemon thread so the assertion runs after the POST completes
        for t in spawned_threads:
            t.join(timeout=10)

        assert mock_client.post.call_count == 1
        call = mock_client.post.call_args_list[0]
        assert call.args[0] == "https://fire.example.com/tmp/packages/sync"

        body = call.kwargs.get("json", call.args[1] if len(call.args) > 1 else None)
        assert isinstance(body, list)
        assert len(body) == 1
        assert body[0]["media_buy_id"] == media_buy_id
        assert body[0]["seller_agent"]["agent_url"] == "https://salesagent.example.com/mcp"
