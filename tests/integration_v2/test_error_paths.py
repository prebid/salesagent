"""Comprehensive error path testing for AdCP tools.

⚠️ MIGRATION NOTICE: This test has been migrated to tests/integration_v2/ to use the new
pricing_options model. The original file in tests/integration/ is deprecated.

📊 BUDGET FORMAT: AdCP v2.2.0 Migration (2025-10-27)
All tests in this file use float budget format per AdCP v2.2.0 spec:
- Package.budget: float (e.g., 1000.0) - NOT Budget object
- Currency is determined by PricingOption, not Package
- Validation happens at Pydantic schema level (raises ToolError for constraint violations)

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
from fastmcp.exceptions import ToolError
from sqlalchemy import delete

from src.core.database.database_session import get_db_session
from src.core.database.models import CurrencyLimit
from src.core.database.models import Principal as ModelPrincipal
from src.core.database.models import Product as ModelProduct
from src.core.database.models import Tenant as ModelTenant
from src.core.exceptions import AdCPValidationError
from src.core.schemas import CreateMediaBuyError, Error
from src.core.tools import create_media_buy_raw, list_creatives_raw, sync_creatives_raw
from tests.factories import PrincipalFactory
from tests.helpers.adcp_factories import create_test_package_request_dict
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
                format_ids=[{"agent_url": "https://test.com", "id": "display_300x250"}],
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
        identity = PrincipalFactory.make_identity(
            tenant_id="error_test_tenant",
            principal_id="nonexistent_principal",  # Principal doesn't exist
            protocol="a2a",
        )

        future_start = datetime.now(UTC) + timedelta(days=1)
        future_end = future_start + timedelta(days=7)

        # This should return error response, not raise NameError
        response_dict = await create_media_buy_raw(
            po_number="error_test_po",
            brand={"domain": "testbrand.com"},
            buyer_ref="test_buyer",
            context={"trace_id": "auth-missing-principal"},
            packages=[
                create_test_package_request_dict(
                    buyer_ref="pkg1",
                    product_id="error_test_product",
                    pricing_option_id="cpm_usd_fixed",
                    budget=5000.0,
                )
            ],
            start_time=future_start.isoformat(),
            end_time=future_end.isoformat(),
            budget={"total": 5000.0, "currency": "USD"},
            identity=identity,
        )

        # CreateMediaBuyResult supports tuple unpacking: (response, status)
        response, status = response_dict

        # Verify response structure - error cases return CreateMediaBuyError
        assert isinstance(response, CreateMediaBuyError)
        assert response.errors is not None
        assert len(response.errors) > 0

        # Verify error details
        error = response.errors[0]
        assert isinstance(error, Error)
        assert error.code == "authentication_error"
        assert "principal" in error.message.lower() or "not found" in error.message.lower()
        # Context echoed back (adcp 2.12.0+: context is ContextObject, not dict)
        assert response.context.trace_id == "auth-missing-principal"

    async def test_start_time_in_past_returns_validation_error(self, test_tenant_with_principal):
        """Test that start_time in past returns Error response with validation_error code.

        This tests line 3147 in main.py where Error(code="validation_error") is used
        in the ValueError exception handler.
        """
        identity = PrincipalFactory.make_identity(
            tenant_id="error_test_tenant",
            principal_id="error_test_principal",
            protocol="a2a",
        )

        past_start = datetime.now(UTC) - timedelta(days=1)  # In the past!
        past_end = past_start + timedelta(days=7)

        # This should return error response for past start time
        response_dict = await create_media_buy_raw(
            po_number="error_test_po",
            brand={"domain": "testbrand.com"},
            buyer_ref="test_buyer",
            context={"trace_id": "past-start"},
            packages=[
                create_test_package_request_dict(
                    buyer_ref="pkg1",
                    product_id="error_test_product",
                    pricing_option_id="cpm_usd_fixed",
                    budget=5000.0,
                )
            ],
            start_time=past_start.isoformat(),
            end_time=past_end.isoformat(),
            budget={"total": 5000.0, "currency": "USD"},
            identity=identity,
        )

        # CreateMediaBuyResult supports tuple unpacking: (response, status)
        response, status = response_dict

        # Verify response structure - error cases return CreateMediaBuyError
        assert isinstance(response, CreateMediaBuyError)
        assert response.errors is not None
        assert len(response.errors) > 0

        # Verify error details
        error = response.errors[0]
        assert isinstance(error, Error)
        assert error.code == "validation_error"
        # Context echoed back (adcp 2.12.0+: context is ContextObject, not dict)
        assert response.context.trace_id == "past-start"
        assert "past" in error.message.lower() or "start" in error.message.lower()

    async def test_end_time_before_start_returns_validation_error(self, test_tenant_with_principal):
        """Test that end_time before start_time returns Error response."""
        identity = PrincipalFactory.make_identity(
            tenant_id="error_test_tenant",
            principal_id="error_test_principal",
            protocol="a2a",
        )

        start = datetime.now(UTC) + timedelta(days=7)
        end = start - timedelta(days=1)  # Before start!

        response_dict = await create_media_buy_raw(
            po_number="error_test_po",
            brand={"domain": "testbrand.com"},
            buyer_ref="test_buyer",
            packages=[
                create_test_package_request_dict(
                    buyer_ref="pkg1",
                    product_id="error_test_product",
                    pricing_option_id="cpm_usd_fixed",
                    budget=5000.0,
                )
            ],
            start_time=start.isoformat(),
            end_time=end.isoformat(),
            budget={"total": 5000.0, "currency": "USD"},
            identity=identity,
        )

        # CreateMediaBuyResult supports tuple unpacking: (response, status)
        response, status = response_dict

        # Verify response structure - error cases return CreateMediaBuyError
        assert isinstance(response, CreateMediaBuyError)
        assert response.errors is not None
        assert len(response.errors) > 0

        error = response.errors[0]
        assert isinstance(error, Error)
        assert error.code == "validation_error"
        assert "end" in error.message.lower() or "after" in error.message.lower()

    async def test_negative_budget_raises_tool_error(self, test_tenant_with_principal):
        """Test that negative budget raises a validation error during Pydantic validation.

        Note: This is caught at the Pydantic schema level (ge=0 constraint) before
        business logic runs, so it raises ToolError or AdCPValidationError rather
        than returning an Error response.
        """
        identity = PrincipalFactory.make_identity(
            tenant_id="error_test_tenant",
            principal_id="error_test_principal",
            protocol="a2a",
        )

        future_start = datetime.now(UTC) + timedelta(days=1)
        future_end = future_start + timedelta(days=7)

        # Negative budget should fail Pydantic validation (ge=0 constraint)
        with pytest.raises((ToolError, AdCPValidationError)) as exc_info:
            await create_media_buy_raw(
                po_number="error_test_po",
                brand={"domain": "testbrand.com"},
                buyer_ref="test_buyer",
                packages=[
                    create_test_package_request_dict(
                        buyer_ref="pkg1",
                        product_id="error_test_product",
                        pricing_option_id="cpm_usd_fixed",
                        budget=-1000.0,  # Negative budget (will fail validation)
                    )
                ],
                start_time=future_start.isoformat(),
                end_time=future_end.isoformat(),
                budget={"total": -1000.0, "currency": "USD"},
                identity=identity,
            )

        error_message = str(exc_info.value)
        assert "budget" in error_message.lower()
        assert "greater than or equal to 0" in error_message.lower()

    async def test_missing_packages_returns_validation_error(self, test_tenant_with_principal):
        """Test that missing packages returns Error response."""
        identity = PrincipalFactory.make_identity(
            tenant_id="error_test_tenant",
            principal_id="error_test_principal",
            protocol="a2a",
        )

        future_start = datetime.now(UTC) + timedelta(days=1)
        future_end = future_start + timedelta(days=7)

        response_dict = await create_media_buy_raw(
            po_number="error_test_po",
            brand={"domain": "testbrand.com"},
            buyer_ref="test_buyer",
            packages=[],  # Empty packages!
            start_time=future_start.isoformat(),
            end_time=future_end.isoformat(),
            budget={"total": 5000.0, "currency": "USD"},
            identity=identity,
        )

        # CreateMediaBuyResult supports tuple unpacking: (response, status)
        response, status = response_dict

        # Verify response structure - error cases return CreateMediaBuyError
        assert isinstance(response, CreateMediaBuyError)
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

        identity = PrincipalFactory.make_identity(
            tenant_id="test_tenant",
            principal_id="test_principal",
            protocol="a2a",
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
                identity=identity,
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

        identity = PrincipalFactory.make_identity(
            tenant_id="test_tenant",
            principal_id="test_principal",
            protocol="a2a",
        )

        set_current_tenant(
            {
                "tenant_id": "test_tenant",
                "name": "Test Tenant",
                "subdomain": "test",
                "ad_server": "mock",
            }
        )

        # Should raise ToolError or AdCPValidationError, not NameError
        with pytest.raises((ToolError, AdCPValidationError)) as exc_info:
            await list_creatives_raw(
                created_after="not-a-date",  # Invalid format
                identity=identity,
            )

        # Verify it's a proper error, not NameError
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
        """Verify CreateMediaBuyError can contain Error objects.

        Protocol fields (adcp_version, status) removed in protocol envelope migration.
        Note: CreateMediaBuyResponse is a union type (CreateMediaBuySuccess | CreateMediaBuyError),
        so for error responses we use CreateMediaBuyError directly.
        """
        from src.core.schemas import CreateMediaBuyError, Error

        response = CreateMediaBuyError(
            errors=[Error(code="test", message="test error")],
        )

        assert len(response.errors) == 1
        assert response.errors[0].code == "test"


@pytest.mark.integration
class TestRecoveryFieldInErrorResponses:
    """Verify recovery field appears in REST error responses via the exception handler.

    The REST boundary uses AdCPError.to_dict() which includes recovery.
    These tests confirm the full chain: AdCPError raised -> exception handler -> JSON body.
    """

    def test_rest_validation_error_has_correctable_recovery(self):
        """REST 400 from AdCPValidationError includes recovery='correctable'."""
        from unittest.mock import patch

        from starlette.testclient import TestClient

        from src.app import app
        from src.core.exceptions import AdCPValidationError

        with patch(
            "src.core.tools.capabilities.get_adcp_capabilities_raw",
            side_effect=AdCPValidationError("bad input"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/capabilities")
            assert response.status_code == 400
            body = response.json()
            assert "recovery" in body, "REST error response must include 'recovery' field"
            assert body["recovery"] == "correctable"

    def test_rest_adapter_error_has_transient_recovery(self):
        """REST 502 from AdCPAdapterError includes recovery='transient'."""
        from unittest.mock import patch

        from starlette.testclient import TestClient

        from src.app import app
        from src.core.exceptions import AdCPAdapterError

        with patch(
            "src.core.tools.capabilities.get_adcp_capabilities_raw",
            side_effect=AdCPAdapterError("GAM unavailable"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/capabilities")
            assert response.status_code == 502
            body = response.json()
            assert "recovery" in body, "REST error response must include 'recovery' field"
            assert body["recovery"] == "transient"

    def test_rest_custom_recovery_override_preserved(self):
        """Custom recovery= override is preserved through REST boundary."""
        from unittest.mock import patch

        from starlette.testclient import TestClient

        from src.app import app
        from src.core.exceptions import AdCPNotFoundError

        with patch(
            "src.core.tools.capabilities.get_adcp_capabilities_raw",
            side_effect=AdCPNotFoundError("temporarily gone", recovery="transient"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/capabilities")
            assert response.status_code == 404
            body = response.json()
            assert body["recovery"] == "transient", (
                "Custom recovery='transient' must be preserved, not default 'terminal'"
            )

    def test_to_dict_serialization_roundtrip(self):
        """AdCPError.to_dict() -> JSON -> verify recovery is present and correct."""
        import json

        from src.core.exceptions import (
            AdCPBudgetExhaustedError,
            AdCPRateLimitError,
            AdCPValidationError,
        )

        cases = [
            (AdCPValidationError("bad"), "correctable"),
            (AdCPRateLimitError("slow"), "transient"),
            (AdCPBudgetExhaustedError("broke"), "correctable"),
        ]

        for exc, expected_recovery in cases:
            d = exc.to_dict()
            # Simulate JSON roundtrip (what happens in real HTTP response)
            json_str = json.dumps(d)
            deserialized = json.loads(json_str)
            assert deserialized["recovery"] == expected_recovery, (
                f"{type(exc).__name__}: recovery lost in JSON roundtrip"
            )
