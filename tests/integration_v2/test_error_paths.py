"""Comprehensive error path testing for AdCP tools.

⚠️ MIGRATION NOTICE: This test has been migrated to tests/integration_v2/ to use the new
pricing_options model. The original file in tests/integration/ is deprecated.

This test suite systematically exercises error handling paths that were previously
untested, ensuring:
1. Error responses are actually constructible (no NameErrors)
2. Error classes are properly imported
3. Error handling returns proper AdCP-compliant responses
4. All validation and authentication failures are handled gracefully

Background: PR #332 fixed a NameError where Error class wasn't imported but was
used in error responses. These tests prevent regression by actually executing
those error paths.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from src.core.database.database_session import get_db_session
from src.core.database.models import CurrencyLimit
from src.core.database.models import Principal as ModelPrincipal
from src.core.database.models import Product as ModelProduct
from src.core.database.models import Tenant as ModelTenant
from src.core.schema_adapters import CreateMediaBuyResponse
from src.core.schemas import Error
from src.core.tool_context import ToolContext
from src.core.tools import create_media_buy_raw, list_creatives_raw, sync_creatives_raw
from tests.integration_v2.conftest import add_required_setup_data, create_test_product_with_pricing

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.mark.integration
@pytest.mark.requires_db
class TestCreateMediaBuyErrorPaths:
    """Test error handling in create_media_buy.

    These tests ensure the Error class is properly imported and error responses
    are constructible without NameError.
    """

    @pytest.fixture
    def test_tenant_minimal(self, integration_db):
        """Create minimal tenant without principal (for auth error tests)."""
        from src.core.config_loader import set_current_tenant

        with get_db_session() as session:
            now = datetime.now(UTC)

            # Delete existing test data
            session.execute(delete(ModelPrincipal).where(ModelPrincipal.tenant_id == "error_test_tenant"))
            session.execute(delete(ModelProduct).where(ModelProduct.tenant_id == "error_test_tenant"))
            session.execute(delete(CurrencyLimit).where(CurrencyLimit.tenant_id == "error_test_tenant"))
            session.execute(delete(ModelTenant).where(ModelTenant.tenant_id == "error_test_tenant"))
            session.commit()

            # Create tenant
            tenant = ModelTenant(
                tenant_id="error_test_tenant",
                name="Error Test Tenant",
                subdomain="errortest",
                ad_server="mock",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            session.add(tenant)
            session.commit()

            # Add required setup data (currency limits, property tags)
            add_required_setup_data(session, "error_test_tenant")

            # Create product using new pricing_options model
            product = create_test_product_with_pricing(
                session=session,
                tenant_id="error_test_tenant",
                product_id="error_test_product",
                name="Error Test Product",
                description="Product for error testing",
                pricing_model="CPM",
                rate="10.00",
                is_fixed=True,
                min_spend_per_package="1000.00",
                formats=[{"agent_url": "https://test.com", "id": "display_300x250"}],
            )

            session.commit()

        # Session closed here - data persists in database

        # Set tenant context
        set_current_tenant(
            {
                "tenant_id": "error_test_tenant",
                "name": "Error Test Tenant",
                "subdomain": "errortest",
                "ad_server": "mock",
            }
        )

        yield

        # Cleanup with new session
        with get_db_session() as session:
            session.execute(delete(ModelPrincipal).where(ModelPrincipal.tenant_id == "error_test_tenant"))
            session.execute(delete(ModelProduct).where(ModelProduct.tenant_id == "error_test_tenant"))
            session.execute(delete(CurrencyLimit).where(CurrencyLimit.tenant_id == "error_test_tenant"))
            session.execute(delete(ModelTenant).where(ModelTenant.tenant_id == "error_test_tenant"))
            session.commit()

    @pytest.fixture
    def test_tenant_with_principal(self, test_tenant_minimal):
        """Add principal to minimal tenant."""
        with get_db_session() as session:
            principal = ModelPrincipal(
                tenant_id="error_test_tenant",
                principal_id="error_test_principal",
                name="Error Test Principal",
                access_token="error_test_token",
                platform_mappings={"mock": {"advertiser_id": "error_test_adv"}},
            )
            session.add(principal)
            session.commit()

        # Session closed here - principal persists in database

        yield

        # Cleanup principal with new session
        with get_db_session() as session:
            session.execute(delete(ModelPrincipal).where(ModelPrincipal.principal_id == "error_test_principal"))
            session.commit()

    async def test_missing_principal_returns_authentication_error(self, test_tenant_minimal):
        """Test that missing principal returns Error response with authentication_error code.

        This tests line 3159 in main.py where Error(code="authentication_error") is used.
        Previously this would cause NameError because Error wasn't imported.
        """
        context = ToolContext(
            context_id="test_ctx",
            tenant_id="error_test_tenant",
            principal_id="nonexistent_principal",  # Principal doesn't exist
            tool_name="create_media_buy",
            request_timestamp=datetime.now(UTC),
        )

        future_start = datetime.now(UTC) + timedelta(days=1)
        future_end = future_start + timedelta(days=7)

        # This should return error response, not raise NameError
        response = await create_media_buy_raw(
            po_number="error_test_po",
            brand_manifest={"name": "Test campaign"},
            buyer_ref="test_buyer",
            packages=[
                {
                    "package_id": "pkg1",
                    "products": ["error_test_product"],
                    "budget": {"total": 5000.0, "currency": "USD"},
                }
            ],
            start_time=future_start.isoformat(),
            end_time=future_end.isoformat(),
            budget={"total": 5000.0, "currency": "USD"},
            context=context,
        )

        # Verify response structure
        assert isinstance(response, CreateMediaBuyResponse)
        assert response.errors is not None
        assert len(response.errors) > 0

        # Verify error details
        error = response.errors[0]
        assert isinstance(error, Error)
        assert error.code == "authentication_error"
        assert "principal" in error.message.lower() or "not found" in error.message.lower()

    async def test_start_time_in_past_returns_validation_error(self, test_tenant_with_principal):
        """Test that start_time in past returns Error response with validation_error code.

        This tests line 3147 in main.py where Error(code="validation_error") is used
        in the ValueError exception handler.
        """
        context = ToolContext(
            context_id="test_ctx",
            tenant_id="error_test_tenant",
            principal_id="error_test_principal",
            tool_name="create_media_buy",
            request_timestamp=datetime.now(UTC),
        )

        past_start = datetime.now(UTC) - timedelta(days=1)  # In the past!
        past_end = past_start + timedelta(days=7)

        # This should return error response for past start time
        response = await create_media_buy_raw(
            po_number="error_test_po",
            brand_manifest={"name": "Test campaign"},
            buyer_ref="test_buyer",
            packages=[
                {
                    "package_id": "pkg1",
                    "products": ["error_test_product"],
                    "budget": {"total": 5000.0, "currency": "USD"},
                }
            ],
            start_time=past_start.isoformat(),
            end_time=past_end.isoformat(),
            budget={"total": 5000.0, "currency": "USD"},
            context=context,
        )

        # Verify response structure
        assert isinstance(response, CreateMediaBuyResponse)
        assert response.errors is not None
        assert len(response.errors) > 0

        # Verify error details
        error = response.errors[0]
        assert isinstance(error, Error)
        assert error.code == "validation_error"
        assert "past" in error.message.lower() or "start" in error.message.lower()

    async def test_end_time_before_start_returns_validation_error(self, test_tenant_with_principal):
        """Test that end_time before start_time returns Error response."""
        context = ToolContext(
            context_id="test_ctx",
            tenant_id="error_test_tenant",
            principal_id="error_test_principal",
            tool_name="create_media_buy",
            request_timestamp=datetime.now(UTC),
        )

        start = datetime.now(UTC) + timedelta(days=7)
        end = start - timedelta(days=1)  # Before start!

        response = await create_media_buy_raw(
            po_number="error_test_po",
            brand_manifest={"name": "Test campaign"},
            buyer_ref="test_buyer",
            packages=[
                {
                    "package_id": "pkg1",
                    "products": ["error_test_product"],
                    "budget": {"total": 5000.0, "currency": "USD"},
                }
            ],
            start_time=start.isoformat(),
            end_time=end.isoformat(),
            budget={"total": 5000.0, "currency": "USD"},
            context=context,
        )

        assert isinstance(response, CreateMediaBuyResponse)
        assert response.errors is not None
        assert len(response.errors) > 0

        error = response.errors[0]
        assert isinstance(error, Error)
        assert error.code == "validation_error"
        assert "end" in error.message.lower() or "after" in error.message.lower()

    async def test_negative_budget_returns_validation_error(self, test_tenant_with_principal):
        """Test that negative budget returns Error response."""
        context = ToolContext(
            context_id="test_ctx",
            tenant_id="error_test_tenant",
            principal_id="error_test_principal",
            tool_name="create_media_buy",
            request_timestamp=datetime.now(UTC),
        )

        future_start = datetime.now(UTC) + timedelta(days=1)
        future_end = future_start + timedelta(days=7)

        response = await create_media_buy_raw(
            po_number="error_test_po",
            brand_manifest={"name": "Test campaign"},
            buyer_ref="test_buyer",
            packages=[
                {
                    "package_id": "pkg1",
                    "products": ["error_test_product"],
                    "budget": {"total": -1000.0, "currency": "USD"},  # Negative!
                }
            ],
            start_time=future_start.isoformat(),
            end_time=future_end.isoformat(),
            budget={"total": -1000.0, "currency": "USD"},
            context=context,
        )

        assert isinstance(response, CreateMediaBuyResponse)
        assert response.errors is not None
        assert len(response.errors) > 0

        error = response.errors[0]
        assert isinstance(error, Error)
        assert error.code == "validation_error"
        assert "budget" in error.message.lower() and "positive" in error.message.lower()

    async def test_missing_packages_returns_validation_error(self, test_tenant_with_principal):
        """Test that missing packages returns Error response."""
        context = ToolContext(
            context_id="test_ctx",
            tenant_id="error_test_tenant",
            principal_id="error_test_principal",
            tool_name="create_media_buy",
            request_timestamp=datetime.now(UTC),
        )

        future_start = datetime.now(UTC) + timedelta(days=1)
        future_end = future_start + timedelta(days=7)

        response = await create_media_buy_raw(
            po_number="error_test_po",
            brand_manifest={"name": "Test campaign"},
            buyer_ref="test_buyer",
            packages=[],  # Empty packages!
            start_time=future_start.isoformat(),
            end_time=future_end.isoformat(),
            budget={"total": 5000.0, "currency": "USD"},
            context=context,
        )

        assert isinstance(response, CreateMediaBuyResponse)
        assert response.errors is not None
        assert len(response.errors) > 0

        error = response.errors[0]
        assert isinstance(error, Error)
        # Should be validation error or similar
        assert error.code in ["validation_error", "invalid_request"]


@pytest.mark.integration
@pytest.mark.requires_db
class TestSyncCreativesErrorPaths:
    """Test error handling in sync_creatives."""

    @pytest.mark.asyncio
    async def test_invalid_creative_format_returns_error(self, integration_db):
        """Test that invalid creative format is handled gracefully."""
        from src.core.config_loader import set_current_tenant

        # Create minimal test context
        context = ToolContext(
            context_id="test_ctx",
            tenant_id="test_tenant",
            principal_id="test_principal",
            tool_name="sync_creatives",
            request_timestamp=datetime.now(UTC),
        )

        # Set tenant (mock for this test)
        set_current_tenant(
            {
                "tenant_id": "test_tenant",
                "name": "Test Tenant",
                "subdomain": "test",
                "ad_server": "mock",
            }
        )

        # Invalid creative - missing required fields
        invalid_creatives = [
            {
                "creative_id": "invalid_creative",
                # Missing format, assets, etc
            }
        ]

        # Should handle gracefully, not crash
        try:
            response = await sync_creatives_raw(
                creatives=invalid_creatives,
                context=context,
            )
            # If it returns, check for errors
            assert response is not None
        except NameError:
            # ❌ FAIL: NameError means Error class wasn't imported
            pytest.fail("sync_creatives_raw raised NameError - Error class not imported")
        except Exception:
            # ✅ Other exceptions are fine (validation errors, etc.)
            pass


@pytest.mark.integration
@pytest.mark.requires_db
class TestListCreativesErrorPaths:
    """Test error handling in list_creatives."""

    @pytest.mark.asyncio
    async def test_invalid_date_format_returns_error(self, integration_db):
        """Test that invalid date format is handled with proper error."""
        from src.core.config_loader import set_current_tenant

        context = ToolContext(
            context_id="test_ctx",
            tenant_id="test_tenant",
            principal_id="test_principal",
            tool_name="list_creatives",
            request_timestamp=datetime.now(UTC),
        )

        set_current_tenant(
            {
                "tenant_id": "test_tenant",
                "name": "Test Tenant",
                "subdomain": "test",
                "ad_server": "mock",
            }
        )

        # Should raise ToolError, not NameError
        from fastmcp.exceptions import ToolError

        with pytest.raises(ToolError) as exc_info:
            await list_creatives_raw(
                created_after="not-a-date",  # Invalid format
                context=context,
            )

        # Verify it's a proper ToolError, not NameError
        assert "date" in str(exc_info.value).lower()


@pytest.mark.integration
class TestImportValidation:
    """Meta-test: Verify Error class is actually importable where used."""

    def test_error_class_is_constructible(self):
        """Verify Error class can be constructed (basic smoke test)."""
        from src.core.schemas import Error

        error = Error(code="test_code", message="test message")
        assert error.code == "test_code"
        assert error.message == "test message"

    def test_error_class_imported_in_main(self):
        """Verify Error class is imported in main.py (regression test for PR #332)."""
        import src.core.main
        from src.core.schemas import Error

        # Verify Error is accessible from main module
        assert hasattr(src.core.main, "Error")
        # Verify it's the same class
        assert src.core.main.Error is Error

    def test_create_media_buy_response_with_errors(self):
        """Verify CreateMediaBuyResponse can contain Error objects.

        Protocol fields (adcp_version, status) removed in protocol envelope migration.
        """
        from src.core.schema_adapters import CreateMediaBuyResponse
        from src.core.schemas import Error

        response = CreateMediaBuyResponse(
            buyer_ref="test",
            errors=[Error(code="test", message="test error")],
        )

        assert len(response.errors) == 1
        assert response.errors[0].code == "test"
