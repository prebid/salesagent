"""Comprehensive integration tests for ALL Admin UI GET routes.

This test suite validates that all 71+ GET routes in the Admin UI:
- Return appropriate status codes (200, 404, 501, etc.)
- Don't crash with template errors
- Work correctly with the integration database
- All links in HTML pages are valid (no 404s)

Routes are organized by blueprint for maintainability.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tests.factories import GAMInventoryFactory, ProductFactory, SyncJobFactory, TenantFactory
from tests.integration.link_validator import LinkValidator, format_broken_links_report

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


class TestCoreRoutes:
    """Test core blueprint routes (/, /health, etc.)."""

    def test_root_index(self, admin_client):
        """Test / redirects or renders."""
        response = admin_client.get("/", follow_redirects=False)
        assert response.status_code in [200, 302]

    def test_super_admin_index_filters_the_tenant_list(self, authenticated_admin_session, factory_session, monkeypatch):
        """The configured filter reaches the tenant listing (TenantLookupRepository.list_active)."""
        from src.core.config import get_settings

        monkeypatch.setattr(get_settings().runtime, "adcp_multi_tenant", True)
        TenantFactory(name="Alpha Wired", ad_server="google_ad_manager")
        TenantFactory(name="Bravo Bare", ad_server=None)

        response = authenticated_admin_session.get("/?configured=not-configured")

        assert response.status_code == 200
        assert b"Bravo Bare" in response.data
        assert b"Alpha Wired" not in response.data

    def test_health_endpoint(self, admin_client):
        """Test /health returns 200."""
        response = admin_client.get("/health")
        assert response.status_code == 200

    def test_health_config_endpoint(self, admin_client):
        """Test /health/config returns data."""
        response = admin_client.get("/health/config")
        assert response.status_code == 200

    def test_metrics_endpoint(self, authenticated_admin_session):
        """Test /metrics endpoint."""
        response = authenticated_admin_session.get("/metrics")
        assert response.status_code in [200, 501]  # May not be implemented

    def test_debug_headers(self, authenticated_admin_session):
        """Test /debug/headers endpoint."""
        response = authenticated_admin_session.get("/debug/headers")
        assert response.status_code == 200

    def test_create_tenant_page(self, authenticated_admin_session):
        """Test /create_tenant page renders."""
        response = authenticated_admin_session.get("/create_tenant")
        assert response.status_code == 200


class TestPublicRoutes:
    """Test public blueprint routes (no auth required)."""

    def test_signup_landing(self, admin_client):
        """Test /signup landing page."""
        response = admin_client.get("/signup")
        assert response.status_code == 200

    def test_signup_start(self, admin_client):
        """Test /signup/start page."""
        response = admin_client.get("/signup/start", follow_redirects=False)
        # May redirect to signup or landing page
        assert response.status_code in [200, 302]

    def test_signup_onboarding(self, admin_client):
        """Test /signup/onboarding page."""
        response = admin_client.get("/signup/onboarding", follow_redirects=True)
        # May redirect if no session
        assert response.status_code in [200, 302]

    def test_signup_complete(self, admin_client):
        """Test /signup/complete page."""
        response = admin_client.get("/signup/complete", follow_redirects=True)
        # May redirect if no session
        assert response.status_code in [200, 302]


class TestAuthRoutes:
    """Test authentication routes."""

    def test_login_page(self, admin_client):
        """Test /login page renders or redirects to OAuth."""
        response = admin_client.get("/login", follow_redirects=False)
        # 200: Login page rendered (test mode or OAuth not configured)
        # 302: Redirect to OAuth provider (OAuth configured)
        assert response.status_code in [200, 302]

    def test_test_login_page(self, admin_client):
        """Test /test/login page (test mode)."""
        response = admin_client.get("/test/login")
        assert response.status_code in [200, 404]  # May be disabled in production

    def test_logout(self, admin_client):
        """Test /logout redirects."""
        response = admin_client.get("/logout", follow_redirects=False)
        assert response.status_code in [200, 302]


class TestTenantRoutes:
    """Test tenant-specific routes."""

    def test_tenant_dashboard(self, authenticated_admin_session, test_tenant_with_data):
        """Test tenant dashboard."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}", follow_redirects=True)
        assert response.status_code == 200

    def test_tenant_settings(self, authenticated_admin_session, test_tenant_with_data):
        """Test tenant settings page."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/settings", follow_redirects=True)
        assert response.status_code == 200

    def test_tenant_settings_sections(self, authenticated_admin_session, test_tenant_with_data):
        """Test tenant settings section pages."""
        tenant_id = test_tenant_with_data["tenant_id"]
        sections = ["account", "adapter", "inventory", "integrations"]
        for section in sections:
            response = authenticated_admin_session.get(f"/tenant/{tenant_id}/settings/{section}", follow_redirects=True)
            assert response.status_code in [200, 404]  # Section may not exist

    def test_tenant_media_buys(self, authenticated_admin_session, test_tenant_with_data):
        """Test tenant media buys list."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/media-buys", follow_redirects=True)
        assert response.status_code == 200

    def test_tenant_setup_checklist(self, authenticated_admin_session, test_tenant_with_data):
        """Test tenant setup checklist."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/setup-checklist", follow_redirects=True)
        assert response.status_code == 200

    def test_tenant_users(self, authenticated_admin_session, test_tenant_with_data):
        """Test tenant users list."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/users", follow_redirects=True)
        assert response.status_code == 200


class TestProductsRoutes:
    """Test products blueprint routes."""

    def test_list_products(self, authenticated_admin_session, test_tenant_with_data):
        """Test products list page."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/products/", follow_redirects=True)
        assert response.status_code == 200

    def test_add_product_page(self, authenticated_admin_session, test_tenant_with_data):
        """Test add product page."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/products/add", follow_redirects=True)
        assert response.status_code == 200


class TestPrincipalsRoutes:
    """Test principals (advertisers) blueprint routes."""

    def test_list_principals(self, authenticated_admin_session, test_tenant_with_data):
        """Test principals list page."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/principals", follow_redirects=True)
        assert response.status_code == 200

    def test_create_principal_page(self, authenticated_admin_session, test_tenant_with_data):
        """Test create principal page."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/principals/create", follow_redirects=True)
        assert response.status_code == 200


class TestAuthorizedPropertiesRoutes:
    """Test authorized properties blueprint routes."""

    def test_authorized_properties_list(self, authenticated_admin_session, test_tenant_with_data):
        """Test authorized properties list."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/authorized-properties", follow_redirects=True)
        assert response.status_code == 200

    def test_property_tags_list(self, authenticated_admin_session, test_tenant_with_data):
        """Test property tags list."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/property-tags", follow_redirects=True)
        assert response.status_code == 200


class TestInventoryRoutes:
    """Test inventory management routes."""

    def test_inventory_browser(self, authenticated_admin_session, test_tenant_with_data):
        """Test inventory browser page."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/inventory", follow_redirects=True)
        assert response.status_code == 200

    def test_inventory_orders(self, authenticated_admin_session, test_tenant_with_data):
        """Test inventory orders page."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/orders", follow_redirects=True)
        assert response.status_code in [200, 501]  # May not be implemented

    def test_inventory_targeting(self, authenticated_admin_session, test_tenant_with_data):
        """Test inventory targeting page."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/targeting", follow_redirects=True)
        assert response.status_code in [200, 501]

    def test_check_inventory_sync(self, authenticated_admin_session, test_tenant_with_data):
        """Test check inventory sync endpoint."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/check-inventory-sync", follow_redirects=True)
        assert response.status_code in [200, 404]

    def test_check_inventory_sync_reports_the_latest_sync(self, authenticated_admin_session, factory_session):
        """last_sync is the latest sync of any item, as on every other inventory view, not the newest
        item's creation time: a re-sync moves it without creating anything."""
        tenant = TenantFactory()
        latest = datetime.now(UTC).replace(microsecond=0)
        GAMInventoryFactory(tenant=tenant, created_at=latest - timedelta(days=10), last_synced=latest)
        GAMInventoryFactory(
            tenant=tenant, created_at=latest - timedelta(days=5), last_synced=latest - timedelta(days=5)
        )
        GAMInventoryFactory(created_at=latest, last_synced=latest + timedelta(days=1))  # another tenant's

        response = authenticated_admin_session.get(f"/tenant/{tenant.tenant_id}/check-inventory-sync")

        assert response.status_code == 200
        body = response.get_json()
        assert (body["has_inventory"], body["inventory_count"]) == (True, 2)
        assert datetime.fromisoformat(body["last_sync"]) == latest

    def test_sync_status_counts_every_status_and_reads_the_latest_sync_per_type(
        self, authenticated_admin_session, factory_session
    ):
        """Per type: the total, the count per status (STALE included), the type's latest sync,
        and the tenant's latest sync job on every type."""
        tenant = TenantFactory()
        latest = datetime.now(UTC).replace(microsecond=0)
        for status, synced in [("ACTIVE", latest - timedelta(days=2)), ("ACTIVE", latest), ("STALE", latest)]:
            GAMInventoryFactory(tenant=tenant, inventory_type="placement", status=status, last_synced=synced)
        GAMInventoryFactory(tenant=tenant, inventory_type="ad_unit", last_synced=latest + timedelta(days=1))
        GAMInventoryFactory(inventory_type="placement", last_synced=latest + timedelta(days=2))  # another tenant's
        job = SyncJobFactory(tenant=tenant, status="completed")

        response = authenticated_admin_session.get(f"/api/tenant/{tenant.tenant_id}/sync-status")

        assert response.status_code == 200
        sync_status = response.get_json()["sync_status"]
        placement = sync_status["placement"]
        assert (placement["total_count"], placement["status_counts"]) == (3, {"ACTIVE": 2, "STALE": 1})
        assert datetime.fromisoformat(placement["last_synced"]) == latest
        label = sync_status["label"]
        assert datetime.fromisoformat(label.pop("last_job_time")) == job.started_at
        assert label == {
            "total_count": 0,
            "status_counts": {},
            "last_synced": None,
            "last_job_status": "completed",
            "last_job_error": None,
        }

    def test_targeting_data_reports_the_latest_sync_of_any_type(self, authenticated_admin_session, factory_session):
        tenant = TenantFactory()
        latest = datetime.now(UTC).replace(microsecond=0)
        GAMInventoryFactory(tenant=tenant, inventory_type="ad_unit", last_synced=latest)
        GAMInventoryFactory(tenant=tenant, inventory_type="label", last_synced=latest - timedelta(days=1))
        GAMInventoryFactory(inventory_type="label", last_synced=latest + timedelta(days=1))  # another tenant's

        response = authenticated_admin_session.get(f"/api/tenant/{tenant.tenant_id}/targeting/all")

        assert response.status_code == 200
        assert datetime.fromisoformat(response.get_json()["last_sync"]) == latest

    def test_analyze_ad_server(self, authenticated_admin_session, test_tenant_with_data):
        """Test analyze ad server endpoint."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/analyze-ad-server", follow_redirects=True)
        assert response.status_code in [200, 404, 501]


class TestOperationsRoutes:
    """Test operations management routes."""

    def test_operations_orders(self, authenticated_admin_session, test_tenant_with_data):
        """Test operations orders page."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/orders", follow_redirects=True)
        assert response.status_code in [200, 501]

    def test_operations_reporting(self, authenticated_admin_session, test_tenant_with_data):
        """Test operations reporting page."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/reporting", follow_redirects=True)
        # May require GAM adapter
        assert response.status_code in [200, 400, 404]

    def test_operations_targeting(self, authenticated_admin_session, test_tenant_with_data):
        """Test operations targeting page."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/targeting", follow_redirects=True)
        assert response.status_code in [200, 501]

    def test_operations_webhooks(self, authenticated_admin_session, test_tenant_with_data):
        """Test operations webhooks page."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/webhooks", follow_redirects=True)
        assert response.status_code in [200, 501]

    def test_operations_workflows(self, authenticated_admin_session, test_tenant_with_data):
        """Test operations workflows page."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/workflows", follow_redirects=True)
        assert response.status_code == 200


class TestWorkflowsRoutes:
    """Test workflows blueprint routes."""

    def test_workflows_list(self, authenticated_admin_session, test_tenant_with_data):
        """Test workflows list page."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/workflows", follow_redirects=True)
        assert response.status_code == 200


class TestPolicyRoutes:
    """Test policy management routes."""

    def test_policy_index(self, authenticated_admin_session, test_tenant_with_data):
        """Test policy index page."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/policy", follow_redirects=True)
        assert response.status_code == 200

    def test_policy_rules(self, authenticated_admin_session, test_tenant_with_data):
        """Test policy rules page."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/policy/rules", follow_redirects=True)
        assert response.status_code == 200


class TestSchemaRoutes:
    """Test JSON schema validation routes."""

    def test_schemas_root(self, admin_client):
        """Test schemas root endpoint."""
        response = admin_client.get("/schemas/")
        assert response.status_code == 200

    def test_schemas_health(self, admin_client):
        """Test schemas health endpoint."""
        response = admin_client.get("/schemas/health")
        assert response.status_code == 200

    def test_schemas_adcp_root(self, admin_client):
        """Test AdCP schemas root."""
        response = admin_client.get("/schemas/adcp/")
        assert response.status_code == 200

    def test_schemas_adcp_version(self, admin_client):
        """Test AdCP schemas version endpoint."""
        response = admin_client.get("/schemas/adcp/v2.4/")
        assert response.status_code == 200

    def test_schemas_adcp_index(self, admin_client):
        """Test AdCP schemas index."""
        response = admin_client.get("/schemas/adcp/v2.4/index.json")
        assert response.status_code == 200


class TestAPIRoutes:
    """Test API endpoints that return JSON."""

    def test_api_health(self, admin_client):
        """Test /api/health endpoint."""
        response = admin_client.get("/api/health")
        assert response.status_code == 200

    def test_oauth_status(self, authenticated_admin_session):
        """Test OAuth status endpoint."""
        response = authenticated_admin_session.get("/api/oauth/status")
        assert response.status_code == 200

    def test_revenue_chart(self, authenticated_admin_session, test_tenant_with_data):
        """Test revenue chart data endpoint."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/api/tenant/{tenant_id}/revenue-chart")
        assert response.status_code == 200

    def test_product_suggestions(self, authenticated_admin_session, test_tenant_with_data):
        """Test product suggestions API."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/api/tenant/{tenant_id}/products/suggestions")
        assert response.status_code == 200

    def test_product_suggestions_mark_the_products_the_tenant_already_has(
        self, authenticated_admin_session, factory_session
    ):
        """already_exists compares whole product ids; the read used to keep the first character of each."""
        tenant = TenantFactory()
        ProductFactory(tenant=tenant, product_id="run_of_site_display")

        response = authenticated_admin_session.get(f"/api/tenant/{tenant.tenant_id}/products/suggestions")

        assert response.status_code == 200
        marked = {s["product_id"]: s["already_exists"] for s in response.get_json()["suggestions"]}
        assert marked["run_of_site_display"] is True
        assert marked["video_preroll"] is False


class TestActivityStreamRoutes:
    """Test activity stream (SSE) routes."""

    def test_activity_stream(self, authenticated_admin_session, test_tenant_with_data):
        """Test activity stream endpoint."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/activity", follow_redirects=True)
        # SSE endpoint may return different status
        assert response.status_code in [200, 404]

    def test_activity_events(self, authenticated_admin_session, test_tenant_with_data):
        """Test activity events endpoint."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/events", follow_redirects=True)
        # SSE endpoint
        assert response.status_code in [200, 404]

    def test_activity_list(self, authenticated_admin_session, test_tenant_with_data):
        """Test activity list endpoint."""
        tenant_id = test_tenant_with_data["tenant_id"]
        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/activities", follow_redirects=True)
        assert response.status_code in [200, 404]


class TestSettingsRoutes:
    """Test tenant management settings routes."""

    def test_settings_index(self, authenticated_admin_session):
        """Test settings index page."""
        response = authenticated_admin_session.get("/settings", follow_redirects=True)
        # May redirect to tenant-specific settings
        assert response.status_code in [200, 302, 404]


class TestAllLinksValid:
    """Test that all links in major pages are valid.

    This catches broken links like the creative review 404 (PR #421) before
    they reach production. It validates every <a href>, <img src>, <link href>
    on key pages to ensure blueprints are registered and routes exist.
    """

    def test_all_dashboard_links_valid(self, authenticated_admin_session, test_tenant_with_data):
        """Test all links on tenant dashboard are valid."""
        tenant_id = test_tenant_with_data["tenant_id"]
        validator = LinkValidator(authenticated_admin_session)

        response = authenticated_admin_session.get(f"/tenant/{tenant_id}", follow_redirects=True)
        assert response.status_code == 200

        broken_links = validator.validate_response(response, current_page=f"/tenant/{tenant_id}")
        assert not broken_links, format_broken_links_report(broken_links, f"/tenant/{tenant_id}")

    def test_all_settings_links_valid(self, authenticated_admin_session, test_tenant_with_data):
        """Test all links on settings pages are valid."""
        tenant_id = test_tenant_with_data["tenant_id"]
        validator = LinkValidator(authenticated_admin_session)

        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/settings", follow_redirects=True)
        assert response.status_code == 200

        broken_links = validator.validate_response(response, current_page=f"/tenant/{tenant_id}/settings")
        assert not broken_links, format_broken_links_report(broken_links, f"/tenant/{tenant_id}/settings")

    def test_all_products_page_links_valid(self, authenticated_admin_session, test_tenant_with_data):
        """Test all links on products page are valid."""
        tenant_id = test_tenant_with_data["tenant_id"]
        validator = LinkValidator(authenticated_admin_session)

        response = authenticated_admin_session.get(f"/tenant/{tenant_id}/products/", follow_redirects=True)
        assert response.status_code == 200

        broken_links = validator.validate_response(response, current_page=f"/tenant/{tenant_id}/products/")
        assert not broken_links, format_broken_links_report(broken_links, f"/tenant/{tenant_id}/products/")


class TestNotFoundRoutes:
    """Test that non-existent routes return 404."""

    def test_nonexistent_route_404(self, admin_client):
        """Test that non-existent routes return 404."""
        response = admin_client.get("/this-route-does-not-exist-xyz123")
        assert response.status_code == 404

    def test_nonexistent_tenant_404(self, authenticated_admin_session):
        """Test that non-existent tenant returns appropriate error."""
        response = authenticated_admin_session.get("/tenant/nonexistent_xyz/products")
        assert response.status_code in [302, 308, 404, 500]
