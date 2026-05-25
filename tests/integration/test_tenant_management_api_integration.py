#!/usr/bin/env python3
"""Integration tests for the Tenant Management API - tests with actual database."""

import pytest
from flask import Flask
from sqlalchemy import delete

from src.admin.tenant_management_api import tenant_management_api
from src.core.database.models import Tenant

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# temp_db fixture removed - using integration_db from conftest instead


@pytest.fixture
def mock_api_key_auth(integration_db):
    """Mock API key authentication to always pass.

    This fixture bypasses the require_tenant_management_api_key decorator
    by creating a valid API key in the database that all tests can use.

    API key is provisioned via TENANT_MANAGEMENT_API_KEY env var in production.
    """
    from datetime import UTC, datetime

    from src.core.database.database_session import get_db_session
    from src.core.database.models import TenantManagementConfig

    # Create a test API key in the database
    test_api_key = "sk-test-integration-key"

    with get_db_session() as session:
        # Check if key already exists
        from sqlalchemy import select

        stmt = select(TenantManagementConfig).filter_by(config_key="tenant_management_api_key")
        existing = session.scalars(stmt).first()

        if not existing:
            config = TenantManagementConfig(
                config_key="tenant_management_api_key",
                config_value=test_api_key,
                description="Test API key for integration tests",
                updated_at=datetime.now(UTC),
                updated_by="pytest",
            )
            session.add(config)
            session.commit()

    return test_api_key


@pytest.fixture
def app(integration_db, mock_api_key_auth):
    """Create test Flask app with auth configured."""
    # integration_db ensures database is properly initialized
    # mock_api_key_auth ensures API key exists in database
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(tenant_management_api)
    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def test_tenant(integration_db):
    """Create a test tenant."""
    from src.core.database.database_session import get_db_session
    from tests.utils.database_helpers import create_tenant_with_timestamps

    with get_db_session() as session:
        # Create a test tenant
        tenant = create_tenant_with_timestamps(
            tenant_id="test_tenant",
            name="Test Tenant",
            subdomain="test",
            ad_server="mock",
            enable_axe_signals=True,
            auto_approve_format_ids=[],
            human_review_required=False,
            billing_plan="basic",
            is_active=True,
        )
        session.add(tenant)
        session.commit()

    yield tenant

    # Cleanup
    with get_db_session() as session:
        session.execute(delete(Tenant).where(Tenant.tenant_id == "test_tenant"))
        session.commit()


class TestTenantManagementAPIIntegration:
    """Integration tests for Tenant Management API."""

    def test_init_api_key_endpoint_removed(self, client):
        """Verify the unauthenticated init-api-key endpoint no longer exists."""
        response = client.post("/api/v1/tenant-management/init-api-key")
        assert response.status_code == 404  # Route removed entirely

    def test_health_check(self, client, mock_api_key_auth):
        """Test health check endpoint."""
        response = client.get(
            "/api/v1/tenant-management/health", headers={"X-Tenant-Management-API-Key": mock_api_key_auth}
        )

        assert response.status_code == 200
        assert response.json["status"] == "healthy"

    def test_list_adapters_returns_supported_catalog(self, client, mock_api_key_auth):
        """Discovery endpoint surfaces the full adapter catalog so embedders
        can dynamically render the picker. Verifies every shipped adapter
        appears with its capabilities + connection JSON Schema."""
        response = client.get(
            "/api/v1/tenant-management/adapters",
            headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
        )

        assert response.status_code == 200
        body = response.json
        assert body["count"] == len(body["adapters"])

        types = {entry["type"] for entry in body["adapters"]}
        assert types == {"google_ad_manager", "mock", "freewheel", "broadstreet", "springserve"}
        # Triton is parked — must not appear in the discovery catalog.
        assert "triton" not in types

        # FreeWheel entry exercises every interesting field path
        fw = next(entry for entry in body["adapters"] if entry["type"] == "freewheel")
        assert fw["name"] == "FreeWheel"
        assert "Video and CTV" in fw["description"]
        assert fw["tier"] == "live"
        assert "olv" in fw["default_channels"]
        assert "ctv" in fw["default_channels"]
        assert fw["contract_version"] == "2026-05-01"
        assert fw["capabilities_url"] == "/api/v1/tenant-management/adapters/freewheel/capabilities"
        assert fw["openapi_url"] == "/api/v1/tenant-management/adapters/freewheel/openapi.json"
        assert fw["capabilities"]["supports_inventory_sync"] is True
        assert fw["capabilities"]["supports_reporting_sync"] is True
        assert "cpm" in fw["capabilities"]["supported_pricing_models"]

        # JSON Schema must carry the discriminator literal so embedders can
        # validate locally before they POST
        schema = fw["connection_schema"]
        type_field = schema["properties"]["type"]
        # Pydantic v2 emits literals via either ``const`` or ``enum`` —
        # accept either as long as the value is "freewheel".
        assert type_field.get("const") == "freewheel" or type_field.get("enum") == ["freewheel"]

    def test_list_adapters_tier_filter_excludes_mock_from_live(self, client, mock_api_key_auth):
        """?tier=live filters out simulated/dev-only adapters (Mock) so
        production storefronts can render the picker without offering
        a fake option."""
        response = client.get(
            "/api/v1/tenant-management/adapters?tier=live",
            headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
        )
        assert response.status_code == 200
        types = {entry["type"] for entry in response.json["adapters"]}
        assert "mock" not in types
        assert types == {"google_ad_manager", "freewheel", "broadstreet", "springserve"}
        # And every returned entry is tier=live
        assert all(entry["tier"] == "live" for entry in response.json["adapters"])

    def test_list_adapters_tier_filter_test_returns_only_mock(self, client, mock_api_key_auth):
        """?tier=test returns just the simulated adapters — useful for dev
        consoles that want to show the test surface explicitly."""
        response = client.get(
            "/api/v1/tenant-management/adapters?tier=test",
            headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
        )
        assert response.status_code == 200
        types = {entry["type"] for entry in response.json["adapters"]}
        assert types == {"mock"}

    def test_list_adapters_tier_filter_rejects_unknown_value(self, client, mock_api_key_auth):
        """Unknown tier values must be rejected — silently ignoring them
        would mask client bugs."""
        response = client.get(
            "/api/v1/tenant-management/adapters?tier=beta",
            headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
        )
        assert response.status_code == 400

    def test_list_adapters_requires_api_key(self, client):
        """Discovery endpoint is gated by the tenant-management API key."""
        response = client.get("/api/v1/tenant-management/adapters")
        assert response.status_code in (401, 403)

    def test_get_adapter_capabilities_returns_contract_details(self, client, mock_api_key_auth):
        """Per-adapter capabilities expose the contract metadata Storefront needs."""
        response = client.get(
            "/api/v1/tenant-management/adapters/freewheel/capabilities",
            headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
        )

        assert response.status_code == 200
        body = response.json
        assert body["type"] == "freewheel"
        assert body["contract_version"] == "2026-05-01"
        assert body["openapi_url"] == "/api/v1/tenant-management/adapters/freewheel/openapi.json"
        assert body["supports_inventory_sync"] is True
        assert body["supports_reporting_sync"] is True
        assert body["supports_reporting"] is True
        assert body["sync_streams"] == ["inventory", "reporting"]
        assert "placement" in body["supported_object_types"]
        assert "audience_segment" in body["supported_signal_types"]
        pricing_gap = next(
            feature for feature in body["unsupported_features"] if feature["feature"] == "pricing_recommendations"
        )
        assert "pricing recommendations" in pricing_gap["reason"]
        assert pricing_gap["remediation"]

    def test_get_adapter_capabilities_accepts_gam_alias(self, client, mock_api_key_auth):
        """The public contract endpoint accepts the legacy GAM alias but returns the canonical type."""
        response = client.get(
            "/api/v1/tenant-management/adapters/gam/capabilities",
            headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
        )

        assert response.status_code == 200
        assert response.json["type"] == "google_ad_manager"
        assert response.json["supports_forecasting"] is True
        assert response.json["supports_custom_targeting"] is True
        assert response.json["sync_streams"] == ["inventory", "custom_targeting", "advertisers"]
        assert "flat_rate" in response.json["supported_pricing_models"]

    def test_get_adapter_openapi_returns_component_contract(self, client, mock_api_key_auth):
        """Adapter OpenAPI docs are component-focused and avoid advertising unimplemented paths as live."""
        response = client.get(
            "/api/v1/tenant-management/adapters/google_ad_manager/openapi.json",
            headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
        )

        assert response.status_code == 200
        document = response.json
        assert document["openapi"] == "3.1.0"
        assert document["info"]["version"] == "2026-05-01"
        assert document["x-salesagent-adapter"] == "google_ad_manager"
        assert "GAM 1x1" in " ".join(document["x-salesagent-normalization-notes"])
        assert document["x-salesagent-common-tenant-endpoints"]["live"]
        assert document["security"] == [{"TenantManagementApiKey": []}]
        assert "TenantManagementApiKey" in document["components"]["securitySchemes"]
        assert (
            "GET /tenants/{tenant_id}/inventory-configuration-candidates"
            in document["x-salesagent-common-tenant-endpoints"]["contract_components_only"]
        )

        schemas = document["components"]["schemas"]
        for schema_name in (
            "AdapterConfig",
            "AdapterCapabilities",
            "ApiError",
            "TestConnectionResponse",
            "PreviewAdapterResponse",
            "TenantStatusResponse",
            "SetupTaskItem",
            "StatusSyncsBlock",
            "AdServerObjectSearchRequest",
            "AdServerObjectSearchResponse",
            "AdServerObject",
            "CreativeFormatClassification",
            "InventoryConfigurationCandidate",
            "InventoryConfigurationCandidateListResponse",
            "InventoryConfigurationWrite",
            "InventoryConfigurationResponse",
            "InventoryConfigurationPreviewRequest",
            "InventoryConfigurationPreviewResponse",
            "SignalCandidate",
            "SignalMappingWrite",
            "SignalMappingResponse",
            "SignalMappingPreviewRequest",
            "SignalMappingPreviewResponse",
            "ReportingForecastSummary",
            "PricingRecommendation",
            "PricingRecommendationResponse",
            "WebhookEvent",
            "UnsupportedFeature",
        ):
            assert schema_name in schemas

        operations = document["x-salesagent-common-tenant-operations"]
        assert (
            operations["live"]["POST /tenants/{tenant_id}/adapter-config/test-connection"]["response"]
            == "#/components/schemas/TestConnectionResponse"
        )
        assert schemas["PreviewAdapterRequest"]["properties"]["adapter"] == {
            "$ref": "#/components/schemas/AdapterConfig"
        }
        assert schemas["ProvisionTenantRequest"]["properties"]["adapter"] == {
            "$ref": "#/components/schemas/AdapterConfig"
        }
        assert (
            operations["contract_components_only"]["GET /tenants/{tenant_id}/ad-server-objects/search"]["response"]
            == "#/components/schemas/AdServerObjectSearchResponse"
        )
        for planned_endpoint in document["x-salesagent-common-tenant-endpoints"]["contract_components_only"]:
            assert planned_endpoint in operations["contract_components_only"]
        assert "/tenants/{tenant_id}/inventory-configuration-candidates" not in document["paths"]

    def test_adapter_contract_signal_capabilities_are_consistent(self, client, mock_api_key_auth):
        """Signal schemas and capabilities use the same adapter support decision."""
        for adapter_type in ("mock", "broadstreet"):
            capabilities_response = client.get(
                f"/api/v1/tenant-management/adapters/{adapter_type}/capabilities",
                headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
            )
            openapi_response = client.get(
                f"/api/v1/tenant-management/adapters/{adapter_type}/openapi.json",
                headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
            )

            assert capabilities_response.status_code == 200
            assert openapi_response.status_code == 200
            capabilities_body = capabilities_response.json
            signal_type_schema = openapi_response.json["components"]["schemas"]["SignalCandidate"]["properties"]["type"]

            assert capabilities_body["supported_signal_types"] == []
            assert capabilities_body["supports_audiences"] is False
            assert any(
                feature["feature"] == "custom_targeting" for feature in capabilities_body["unsupported_features"]
            )
            assert "enum" not in signal_type_schema

    def test_get_adapter_contract_unknown_type_returns_404(self, client, mock_api_key_auth):
        """Unknown and parked adapters are not published as contract surfaces."""
        for adapter_type in ("unknown", "triton"):
            response = client.get(
                f"/api/v1/tenant-management/adapters/{adapter_type}/capabilities",
                headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
            )
            assert response.status_code == 404

    def test_adapter_contract_endpoints_require_api_key(self, client):
        """Contract endpoints are gated like the rest of tenant-management."""
        capabilities_response = client.get("/api/v1/tenant-management/adapters/freewheel/capabilities")
        openapi_response = client.get("/api/v1/tenant-management/adapters/freewheel/openapi.json")

        assert capabilities_response.status_code in (401, 403)
        assert openapi_response.status_code in (401, 403)

    def test_create_minimal_gam_tenant(self, client, mock_api_key_auth):
        """Test creating a minimal GAM tenant with just refresh token."""
        tenant_data = {
            "name": "Test Sports Publisher",
            "subdomain": "test-sports",
            "ad_server": "google_ad_manager",
            "gam_refresh_token": "1//test-refresh-token",
            "creator_email": "test@sports.com",  # Required for access control
        }

        response = client.post(
            "/api/v1/tenant-management/tenants",
            headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
            json=tenant_data,
        )

        assert response.status_code == 201
        data = response.json

        # Verify response
        assert "tenant_id" in data
        assert data["name"] == "Test Sports Publisher"
        assert data["subdomain"] == "test-sports"
        assert "admin_token" in data
        assert "admin_ui_url" in data
        assert "default_principal_token" in data

        assert data["tenant_id"], "tenant_id should be a non-empty string"

    def test_create_full_gam_tenant(self, client, mock_api_key_auth):
        """Test creating a GAM tenant with all fields."""
        tenant_data = {
            "name": "Test News Publisher",
            "subdomain": "test-news",
            "ad_server": "google_ad_manager",
            "gam_refresh_token": "1//test-refresh-token-full",
            "gam_network_code": "123456789",
            "gam_trafficker_id": "trafficker_456",
            "authorized_emails": ["admin@testnews.com"],
            "authorized_domains": ["testnews.com"],
            "billing_plan": "premium",
        }
        # NOTE: gam_company_id removed - advertiser_id is per-principal in platform_mappings

        response = client.post(
            "/api/v1/tenant-management/tenants",
            headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
            json=tenant_data,
        )

        assert response.status_code == 201
        data = response.json

        # Verify response
        assert data["name"] == "Test News Publisher"
        assert data["subdomain"] == "test-news"

    def test_list_tenants(self, client, mock_api_key_auth, test_tenant):
        """Test listing all tenants."""
        response = client.get(
            "/api/v1/tenant-management/tenants", headers={"X-Tenant-Management-API-Key": mock_api_key_auth}
        )

        assert response.status_code == 200
        data = response.json

        assert "tenants" in data
        assert "count" in data
        assert isinstance(data["tenants"], list)

        # Should have at least the default tenant plus any we created
        assert data["count"] >= 1

    def test_get_tenant_details(self, client, mock_api_key_auth):
        """Test getting specific tenant details."""
        # First create a tenant
        create_response = client.post(
            "/api/v1/tenant-management/tenants",
            headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
            json={
                "name": "Test Detail Publisher",
                "subdomain": "test-detail",
                "ad_server": "google_ad_manager",
                "gam_refresh_token": "1//test-detail-token",
                "creator_email": "test@detail.com",
            },
        )

        tenant_id = create_response.json["tenant_id"]

        # Now get the details
        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant_id}", headers={"X-Tenant-Management-API-Key": mock_api_key_auth}
        )

        assert response.status_code == 200
        data = response.json

        # The response shape is now ``TenantDetail`` (sprint-1 of managed-tenant-mode).
        # ``settings`` and ``adapter_config`` blocks moved out of this endpoint —
        # adapter config now has its own resource at /tenants/{id}/adapter-config.
        assert data["tenant_id"] == tenant_id
        assert data["name"] == "Test Detail Publisher"
        assert data["subdomain"] == "test-detail"
        assert data["ad_server"] == "google_ad_manager"
        assert data["adapter_configured"] is True
        # ``managed_externally`` is the deprecated alias of ``is_embedded`` — both must match.
        assert data["managed_externally"] is False
        assert data["is_embedded"] is False

        # Adapter config now lives at its own endpoint.
        adapter_resp = client.get(
            f"/api/v1/tenant-management/tenants/{tenant_id}/adapter-config",
            headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
        )
        assert adapter_resp.status_code == 200
        adapter_data = adapter_resp.json
        assert adapter_data["type"] == "google_ad_manager"
        assert adapter_data["refresh_token"] == "<redacted>"

    def test_update_tenant(self, client, mock_api_key_auth, test_tenant):
        """Test updating a tenant."""
        # First create a tenant
        create_response = client.post(
            "/api/v1/tenant-management/tenants",
            headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
            json={
                "name": "Test Update Publisher",
                "subdomain": "test-update",
                "ad_server": "google_ad_manager",
                "gam_refresh_token": "1//test-update-token",
                "creator_email": "test@update.com",
            },
        )

        tenant_id = create_response.json["tenant_id"]

        # Update the tenant
        update_data = {
            "billing_plan": "enterprise",
            "adapter_config": {"gam_network_code": "987654321", "gam_trafficker_id": "trafficker_999"},
        }
        # NOTE: gam_company_id removed - advertiser_id is per-principal in platform_mappings
        # NOTE: max_daily_budget removed - moved to currency_limits table

        response = client.put(
            f"/api/v1/tenant-management/tenants/{tenant_id}",
            headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
            json=update_data,
        )

        assert response.status_code == 200

        # Verify the update
        get_response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant_id}", headers={"X-Tenant-Management-API-Key": mock_api_key_auth}
        )

        updated_data = get_response.json
        assert updated_data["billing_plan"] == "enterprise"
        # Adapter config moved to its own resource. Verify the legacy PUT update wrote through.
        adapter_resp = client.get(
            f"/api/v1/tenant-management/tenants/{tenant_id}/adapter-config",
            headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
        )
        adapter_data = adapter_resp.json
        assert adapter_data["network_code"] == "987654321"

    def test_soft_delete_tenant(self, client, mock_api_key_auth, test_tenant):
        """Test soft deleting a tenant."""
        # First create a tenant
        create_response = client.post(
            "/api/v1/tenant-management/tenants",
            headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
            json={
                "name": "Test Delete Publisher",
                "subdomain": "test-delete",
                "ad_server": "mock",
                "creator_email": "test@delete.com",
            },
        )

        tenant_id = create_response.json["tenant_id"]

        # Soft delete
        response = client.delete(
            f"/api/v1/tenant-management/tenants/{tenant_id}", headers={"X-Tenant-Management-API-Key": mock_api_key_auth}
        )

        # Sprint-1 contract: DELETE returns the soft-deleted TenantDetail body.
        assert response.status_code == 200
        assert response.json["is_active"] is False
        assert response.json["tenant_id"] == tenant_id

        # Verify tenant still exists but is inactive
        get_response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant_id}", headers={"X-Tenant-Management-API-Key": mock_api_key_auth}
        )

        assert get_response.status_code == 200
        assert get_response.json["is_active"] is False


class TestTenantManagementEnvVarAuth:
    """Test env var auth codepath for tenant management API."""

    def test_env_var_auth_succeeds(self, integration_db, monkeypatch):
        """TENANT_MANAGEMENT_API_KEY env var → auth succeeds."""
        monkeypatch.setenv("TENANT_MANAGEMENT_API_KEY", "env-test-key")

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(tenant_management_api)

        with app.test_client() as client:
            resp = client.get(
                "/api/v1/tenant-management/health",
                headers={"X-Tenant-Management-API-Key": "env-test-key"},
            )
            assert resp.status_code == 200

    def test_env_var_takes_priority_over_db(self, integration_db, mock_api_key_auth, monkeypatch):
        """When both env var and DB have keys, env var wins."""
        monkeypatch.setenv("TENANT_MANAGEMENT_API_KEY", "env-priority-key")

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(tenant_management_api)

        with app.test_client() as client:
            # DB key should be rejected when env var is set
            resp = client.get(
                "/api/v1/tenant-management/health",
                headers={"X-Tenant-Management-API-Key": mock_api_key_auth},
            )
            assert resp.status_code == 401

            # Env var key should succeed
            resp = client.get(
                "/api/v1/tenant-management/health",
                headers={"X-Tenant-Management-API-Key": "env-priority-key"},
            )
            assert resp.status_code == 200


class TestTenantManagementAuthRejection:
    """Test auth rejection cases for tenant management API."""

    def test_missing_header_returns_401(self, app, client):
        """Request without auth header returns 401."""
        resp = client.get("/api/v1/tenant-management/health")
        assert resp.status_code == 401
        assert "Missing API key" in resp.json["error"]

    def test_wrong_key_returns_401(self, app, client, mock_api_key_auth):
        """Request with incorrect key returns 401."""
        resp = client.get(
            "/api/v1/tenant-management/health",
            headers={"X-Tenant-Management-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401
        assert "Invalid API key" in resp.json["error"]

    def test_unconfigured_returns_503(self, integration_db):
        """When no key is configured anywhere, returns 503."""
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(tenant_management_api)

        with app.test_client() as client:
            resp = client.get(
                "/api/v1/tenant-management/health",
                headers={"X-Tenant-Management-API-Key": "any-key"},
            )
            assert resp.status_code == 503
            assert "TENANT_MANAGEMENT_API_KEY" in resp.json["error"]


class TestSyncApiAuth:
    """Test auth for sync API using the shared helper."""

    @pytest.fixture
    def sync_app(self, integration_db):
        """Create test Flask app with sync API blueprint."""
        from src.admin.sync_api import sync_api

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(sync_api)
        return app

    def test_env_var_auth_succeeds(self, sync_app, monkeypatch):
        """SYNC_API_KEY env var → auth succeeds (hits real route)."""
        monkeypatch.setenv("SYNC_API_KEY", "sync-test-key")

        with sync_app.test_client() as client:
            resp = client.get(
                "/api/v1/sync/stats",
                headers={"X-API-Key": "sync-test-key"},
            )
            # Auth passed — we get past the decorator. Route may fail on DB
            # but must NOT return 401 (missing/invalid key) or 503 (unconfigured).
            assert resp.status_code not in (
                401,
                403,
                503,
            ), f"Auth should have succeeded but got {resp.status_code}: {resp.json}"

    def test_missing_header_returns_401(self, sync_app, monkeypatch):
        """Request without X-API-Key header returns 401."""
        monkeypatch.setenv("SYNC_API_KEY", "sync-test-key")

        with sync_app.test_client() as client:
            resp = client.post("/api/v1/sync/trigger/test-tenant")
            assert resp.status_code == 401

    def test_wrong_key_returns_401(self, sync_app, monkeypatch):
        """Request with incorrect key returns 401."""
        monkeypatch.setenv("SYNC_API_KEY", "correct-key")

        with sync_app.test_client() as client:
            resp = client.post(
                "/api/v1/sync/trigger/test-tenant",
                headers={"X-API-Key": "wrong-key"},
            )
            assert resp.status_code == 401

    def test_unconfigured_returns_503(self, sync_app):
        """When no key is configured anywhere, returns 503."""
        with sync_app.test_client() as client:
            resp = client.post(
                "/api/v1/sync/trigger/test-tenant",
                headers={"X-API-Key": "any-key"},
            )
            assert resp.status_code == 503
            assert "SYNC_API_KEY" in resp.json["error"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
