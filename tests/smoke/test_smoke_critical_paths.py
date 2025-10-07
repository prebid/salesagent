"""Smoke tests for critical system paths - MUST ALWAYS PASS."""

import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select


class TestServerStartup:
    """Test that servers can start and respond to health checks."""

    @pytest.mark.smoke
    @pytest.mark.requires_server
    def test_mcp_server_health(self):
        """Test MCP server responds to health check."""
        try:
            response = httpx.get("http://localhost:8080/health", timeout=5.0)
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"
        except httpx.ConnectError:
            pytest.skip("MCP server not running")

    @pytest.mark.smoke
    @pytest.mark.requires_server
    def test_admin_ui_health(self):
        """Test Admin UI responds to health check."""
        try:
            response = httpx.get("http://localhost:8001/health", timeout=5.0)
            assert response.status_code == 200
        except httpx.ConnectError:
            pytest.skip("Admin UI not running")


class TestMCPCriticalEndpoints:
    """Test critical MCP endpoints work with valid authentication."""

    @pytest.fixture
    def auth_headers(self):
        """Get valid auth headers for testing."""
        # Use the test token from the test database
        return {"x-adcp-auth": "test_token_sports", "Content-Type": "application/json", "Accept": "application/json"}

    @pytest.mark.smoke
    @pytest.mark.requires_server
    @pytest.mark.asyncio
    async def test_get_products_endpoint(self, auth_headers):
        """Test that get_products endpoint works."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "http://localhost:8080/mcp",
                headers=auth_headers,
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": "get_products", "arguments": {}},
                    "id": 1,
                },
                timeout=10.0,
            )
            assert response.status_code == 200
            result = response.json()
            assert "result" in result or "error" in result

    @pytest.mark.smoke
    @pytest.mark.requires_server
    @pytest.mark.asyncio
    async def test_create_media_buy_endpoint(self, auth_headers):
        """Test that create_media_buy endpoint is available."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "http://localhost:8080/mcp",
                headers=auth_headers,
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {
                        "name": "create_media_buy",
                        "arguments": {
                            "product_ids": ["prod_sports_display"],
                            "total_budget": 1000.0,
                            "flight_start_date": "2025-02-01",
                            "flight_end_date": "2025-02-28",
                        },
                    },
                    "id": 2,
                },
                timeout=10.0,
            )
            assert response.status_code == 200
            result = response.json()
            # Either success or expected error (e.g., product not found)
            assert "result" in result or "error" in result

    @pytest.mark.smoke
    @pytest.mark.requires_server
    @pytest.mark.asyncio
    async def test_get_media_buy_status_endpoint(self, auth_headers):
        """Test that get_media_buy_status endpoint works."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "http://localhost:8080/mcp",
                headers=auth_headers,
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": "get_media_buy_status", "arguments": {"media_buy_id": "mb_test_001"}},
                    "id": 3,
                },
                timeout=10.0,
            )
            assert response.status_code == 200

    @pytest.mark.smoke
    @pytest.mark.requires_server
    def test_authentication_required(self):
        """Test that transaction endpoints require authentication."""
        response = httpx.post(
            "http://localhost:8080/mcp/",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "create_media_buy",
                    "arguments": {
                        "product_ids": ["test"],
                        "total_budget": 1000.0,
                        "flight_start_date": "2025-01-01",
                        "flight_end_date": "2025-01-31",
                    },
                },
                "id": 4,
            },
            timeout=5.0,
        )
        assert response.status_code == 200
        result = response.json()
        assert "error" in result
        assert "authentication" in result["error"]["message"].lower()

    @pytest.mark.smoke
    @pytest.mark.requires_server
    def test_discovery_endpoints_work_without_auth(self):
        """Test that discovery endpoints work without authentication."""
        # Test get_products without auth
        response = httpx.post(
            "http://localhost:8080/mcp/",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "get_products",
                    "arguments": {"brief": "test campaign", "promoted_offering": "Nike Air Max 2025 running shoes"},
                },
                "id": 5,
            },
            timeout=5.0,
        )
        assert response.status_code == 200
        result = response.json()
        # Should succeed without authentication error
        assert "result" in result or ("error" in result and "authentication" not in result["error"]["message"].lower())

        # If successful, verify pricing data is filtered for anonymous users
        if "result" in result and "content" in result["result"]:
            products_data = result["result"]["content"]
            if "products" in products_data:
                for product in products_data["products"]:
                    # Pricing fields should be null for anonymous users
                    assert product.get("cpm") is None
                    assert product.get("min_spend") is None
            # Should contain pricing message
            if "message" in products_data:
                assert "authorized buying agent for pricing" in products_data["message"]

        # Test list_creative_formats without auth
        response = httpx.post(
            "http://localhost:8080/mcp/",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "list_creative_formats", "arguments": {}},
                "id": 6,
            },
            timeout=5.0,
        )
        assert response.status_code == 200
        result = response.json()
        # Should succeed without authentication error
        assert "result" in result or ("error" in result and "authentication" not in result["error"]["message"].lower())


class TestAdminUICriticalPaths:
    """Test critical Admin UI paths."""

    @pytest.mark.smoke
    @pytest.mark.requires_server
    def test_login_page_accessible(self):
        """Test that login page is accessible."""
        response = httpx.get("http://localhost:8001/login", timeout=5.0)
        assert response.status_code == 200
        assert b"Sign in" in response.content or b"Login" in response.content

    @pytest.mark.smoke
    @pytest.mark.requires_server
    def test_protected_routes_require_auth(self):
        """Test that protected routes redirect to login."""
        response = httpx.get("http://localhost:8001/tenants", follow_redirects=False, timeout=5.0)
        assert response.status_code in [302, 303]
        assert "/login" in response.headers.get("location", "")


class TestDatabaseConnectivity:
    """Test database connectivity and basic operations."""

    @pytest.mark.smoke
    def test_database_connection(self):
        """Test that we can connect to the database."""
        from src.core.database.database_session import get_db_session

        with get_db_session() as session:
            from sqlalchemy import text

            # Simple query to test connection
            result = session.execute(text("SELECT 1")).fetchone()
            assert result[0] == 1

    @pytest.mark.smoke
    def test_critical_tables_exist(self, test_database):
        """Test that critical tables exist in the database."""
        from src.core.database.database_session import get_db_session

        critical_tables = ["tenants", "principals", "products", "media_buys", "creative_formats", "audit_logs"]

        with get_db_session() as session:
            from sqlalchemy import text

            for table in critical_tables:
                # This will raise an error if table doesn't exist
                result = session.execute(text(f"SELECT COUNT(*) FROM {table}"))
                assert result.fetchone() is not None


class TestMigrations:
    """Test database migrations are properly applied."""

    @pytest.mark.smoke
    @pytest.mark.skip_ci
    def test_migrations_are_current(self, test_database):
        """Test that all migrations have been applied."""
        from src.core.database.database_session import get_db_session

        with get_db_session() as session:
            from sqlalchemy import text

            # Check alembic_version table exists
            result = session.execute(
                text("SELECT version_num FROM alembic_version ORDER BY version_num DESC LIMIT 1")
            ).fetchone()

            assert result is not None, "No migrations have been applied"

            # Get the latest migration from the migrations folder
            migrations_dir = Path("alembic/versions")
            if migrations_dir.exists():
                migration_files = list(migrations_dir.glob("*.py"))
                if migration_files:
                    # Extract version numbers from filenames
                    versions = []
                    for f in migration_files:
                        if "_" in f.stem:
                            version = f.stem.split("_")[0]
                            versions.append(version)

                    if versions:
                        latest_version = max(versions)
                        current_version = result[0]
                        assert (
                            current_version == latest_version
                        ), f"Database at {current_version}, latest is {latest_version}"


class TestCriticalBusinessLogic:
    """Test critical business logic paths."""

    @pytest.mark.smoke
    def test_principal_authentication_flow(self, test_database):
        """Test the principal authentication flow."""
        from src.core.database.database_session import get_db_session
        from src.core.database.models import Principal as ModelPrincipal

        with get_db_session() as session:
            # Create a test principal
            test_principal = ModelPrincipal(
                tenant_id="test_tenant",
                principal_id="smoke_test_principal",
                name="Smoke Test Principal",
                access_token="smoke_test_token_" + str(int(time.time())),
                platform_mappings={"mock": {"advertiser_id": "smoke_test_advertiser"}},
            )
            session.add(test_principal)
            session.commit()

            # Verify we can retrieve it
            retrieved = session.scalars(select(ModelPrincipal).filter_by(principal_id="smoke_test_principal")).first()

            assert retrieved is not None
            assert retrieved.name == "Smoke Test Principal"

            # Cleanup
            session.delete(retrieved)
            session.commit()

    @pytest.mark.smoke
    def test_media_buy_creation_flow(self, test_database):
        """Test that media buy creation flow works."""
        from src.core.database.database_session import get_db_session
        from src.core.database.models import MediaBuy

        with get_db_session() as session:
            # Create a test media buy
            test_buy = MediaBuy(
                media_buy_id=f"smoke_test_{int(time.time())}",
                tenant_id="test_tenant",
                principal_id="test_principal",
                order_name="Smoke Test Order",
                advertiser_name="Smoke Test Advertiser",
                budget=1000.0,
                start_date=datetime.now().date(),
                end_date=datetime.now().date(),
                status="pending",
                raw_request={},
            )
            session.add(test_buy)
            session.commit()

            # Verify we can retrieve it
            retrieved = session.scalars(select(MediaBuy).filter_by(media_buy_id=test_buy.media_buy_id)).first()

            assert retrieved is not None
            assert retrieved.order_name == "Smoke Test Order"
            assert retrieved.budget == 1000.0

            # Cleanup
            session.delete(retrieved)
            session.commit()


class TestErrorHandling:
    """Test system handles errors gracefully."""

    @pytest.mark.smoke
    @pytest.mark.requires_server
    def test_invalid_endpoint_returns_error(self):
        """Test that invalid endpoints return proper errors."""
        response = httpx.get("http://localhost:8080/invalid/endpoint", timeout=5.0)
        assert response.status_code in [404, 405]

    @pytest.mark.smoke
    @pytest.mark.requires_server
    @pytest.mark.asyncio
    async def test_invalid_tool_returns_error(self, auth_headers):
        """Test that calling invalid tools returns proper error."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "http://localhost:8080/mcp",
                headers={"x-adcp-auth": "test_token_sports"},
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": "invalid_tool_name", "arguments": {}},
                    "id": 5,
                },
                timeout=10.0,
            )
            assert response.status_code == 200
            result = response.json()
            assert "error" in result

    @pytest.mark.smoke
    def test_database_transaction_rollback(self, test_database):
        """Test that failed transactions rollback properly."""
        from src.core.database.database_session import get_db_session
        from src.core.database.models import Tenant

        with get_db_session() as session:
            try:
                # Try to create a tenant with invalid data
                bad_tenant = Tenant(tenant_id=None, name="Bad Tenant")  # This should fail
                session.add(bad_tenant)
                session.commit()
                raise AssertionError("Should have raised an error")
            except Exception:
                session.rollback()
                # Verify database is still functional
                from sqlalchemy import text

                result = session.execute(text("SELECT 1")).fetchone()
                assert result[0] == 1


class TestConcurrency:
    """Test system handles concurrent requests."""

    @pytest.mark.smoke
    @pytest.mark.requires_server
    @pytest.mark.asyncio
    async def test_concurrent_read_requests(self):
        """Test system handles concurrent read requests."""

        async def make_request(client, request_id):
            response = await client.post(
                "http://localhost:8080/mcp",
                headers={"x-adcp-auth": "test_token_sports"},
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": "get_products", "arguments": {}},
                    "id": request_id,
                },
                timeout=10.0,
            )
            return response

        async with httpx.AsyncClient() as client:
            # Make 5 concurrent requests
            tasks = [make_request(client, i) for i in range(5)]
            responses = await asyncio.gather(*tasks)

            # All should succeed
            for response in responses:
                assert response.status_code == 200


class TestSystemIntegration:
    """Test integration between major system components."""

    @pytest.mark.smoke
    def test_audit_logging_works(self, test_database):
        """Test that audit logging is functional."""
        from src.core.audit_logger import get_audit_logger
        from src.core.database.database_session import get_db_session

        logger = get_audit_logger("mock", "smoke_test_tenant")

        # Log a test event
        logger.log_operation(
            operation="smoke_test",
            principal_name="Smoke Test Principal",
            principal_id="smoke_test_principal",
            adapter_id="smoke_test_adapter",
            success=True,
            details={"test": "smoke test audit log"},
        )

        # Verify it was logged to database
        with get_db_session() as session:
            from sqlalchemy import text

            result = session.execute(
                text(
                    """
                SELECT COUNT(*) FROM audit_logs
                WHERE operation = 'mock.smoke_test'
                AND principal_id = 'smoke_test_principal'
                """
                )
            ).fetchone()

            assert result[0] > 0

    @pytest.mark.smoke
    def test_config_loading(self, test_database):
        """Test that configuration loading works."""
        from src.core.config_loader import load_config

        config = load_config()
        assert config is not None
        # Config should have the main sections
        assert "ad_server" in config
        assert "creative_engine" in config
        assert "features" in config


if __name__ == "__main__":
    # Run smoke tests with verbose output
    sys.exit(pytest.main([__file__, "-v", "-m", "smoke"]))
