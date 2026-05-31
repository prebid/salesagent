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
from src.core.exceptions import AdCPAuthenticationError, AdCPBudgetTooLowError, AdCPValidationError
from src.core.tools import create_media_buy_raw, list_creatives_raw, sync_creatives_raw
from tests.factories import PrincipalFactory
from tests.helpers.adcp_factories import create_test_package_request_dict
from tests.integration.conftest import add_required_setup_data, create_test_product_with_pricing

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
        """Test that missing principal raises AdCPAuthenticationError.

        After the error-emission architecture migration, _impl functions raise
        typed AdCPError subclasses; the boundary translator builds the spec-compliant envelope.
        """
        identity = PrincipalFactory.make_identity(
            tenant_id="error_test_tenant",
            principal_id="nonexistent_principal",  # Principal doesn't exist
            protocol="a2a",
        )

        future_start = datetime.now(UTC) + timedelta(days=1)
        future_end = future_start + timedelta(days=7)

        # _impl raises typed exception; boundary translator builds envelope.
        with pytest.raises(AdCPAuthenticationError, match="Principal.*not found"):
            await create_media_buy_raw(
                po_number="error_test_po",
                brand={"domain": "testbrand.com"},
                context={"trace_id": "auth-missing-principal"},
                packages=[
                    create_test_package_request_dict(
                        product_id="error_test_product",
                        pricing_option_id="cpm_usd_fixed",
                        budget=5000.0,
                    )
                ],
                start_time=future_start.isoformat(),
                end_time=future_end.isoformat(),
                identity=identity,
            )

    async def test_start_time_in_past_returns_validation_error(self, test_tenant_with_principal):
        """Test that start_time in past raises AdCPValidationError."""
        identity = PrincipalFactory.make_identity(
            tenant_id="error_test_tenant",
            principal_id="error_test_principal",
            protocol="a2a",
        )

        past_start = datetime.now(UTC) - timedelta(days=1)  # In the past!
        past_end = past_start + timedelta(days=7)

        # Typed AdCPValidationError now propagates past the boundary catch.
        with pytest.raises(AdCPValidationError) as excinfo:
            await create_media_buy_raw(
                po_number="error_test_po",
                brand={"domain": "testbrand.com"},
                context={"trace_id": "past-start"},
                packages=[
                    create_test_package_request_dict(
                        product_id="error_test_product",
                        pricing_option_id="cpm_usd_fixed",
                        budget=5000.0,
                    )
                ],
                start_time=past_start.isoformat(),
                end_time=past_end.isoformat(),
                identity=identity,
            )

        # Typed AdCPValidationError raised directly from _impl with
        # error_code="INVALID_REQUEST".
        exc = excinfo.value
        assert exc.error_code == "INVALID_REQUEST"
        assert "past" in exc.message.lower() or "start" in exc.message.lower()

    async def test_end_time_before_start_returns_validation_error(self, test_tenant_with_principal):
        """Test that end_time before start_time raises AdCPValidationError."""
        identity = PrincipalFactory.make_identity(
            tenant_id="error_test_tenant",
            principal_id="error_test_principal",
            protocol="a2a",
        )

        start = datetime.now(UTC) + timedelta(days=7)
        end = start - timedelta(days=1)  # Before start!

        # Typed AdCPValidationError now propagates past the boundary catch.
        with pytest.raises(AdCPValidationError) as excinfo:
            await create_media_buy_raw(
                po_number="error_test_po",
                brand={"domain": "testbrand.com"},
                packages=[
                    create_test_package_request_dict(
                        product_id="error_test_product",
                        pricing_option_id="cpm_usd_fixed",
                        budget=5000.0,
                    )
                ],
                start_time=start.isoformat(),
                end_time=end.isoformat(),
                identity=identity,
            )

        exc = excinfo.value
        assert exc.error_code == "INVALID_REQUEST"
        assert "end" in exc.message.lower() or "after" in exc.message.lower()

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
                packages=[
                    create_test_package_request_dict(
                        product_id="error_test_product",
                        pricing_option_id="cpm_usd_fixed",
                        budget=-1000.0,  # Negative budget (will fail validation)
                    )
                ],
                start_time=future_start.isoformat(),
                end_time=future_end.isoformat(),
                identity=identity,
            )

        error_message = str(exc_info.value)
        assert "budget" in error_message.lower()
        assert "greater than or equal to 0" in error_message.lower()

    async def test_missing_packages_returns_validation_error(self, test_tenant_with_principal):
        """Test that empty packages raises AdCPValidationError.

        Empty packages → budget=0 path → validation error.
        """
        identity = PrincipalFactory.make_identity(
            tenant_id="error_test_tenant",
            principal_id="error_test_principal",
            protocol="a2a",
        )

        future_start = datetime.now(UTC) + timedelta(days=1)
        future_end = future_start + timedelta(days=7)

        # Typed AdCPBudgetTooLowError now propagates past the boundary catch.
        # Empty packages -> budget=0.0 -> "Budget must be positive" validator at
        # media_buy_create.py:1758 raises AdCPBudgetTooLowError (typed subclass,
        # typed AdCPValidationError raised directly).
        with pytest.raises(AdCPBudgetTooLowError) as excinfo:
            await create_media_buy_raw(
                po_number="error_test_po",
                brand={"domain": "testbrand.com"},
                packages=[],  # Empty packages!
                start_time=future_start.isoformat(),
                end_time=future_end.isoformat(),
                identity=identity,
            )

        exc = excinfo.value
        assert exc.error_code == "BUDGET_TOO_LOW"
        assert "budget" in exc.message.lower() or "package" in exc.message.lower()


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

        # The contract under test: sync_creatives_raw must surface a typed,
        # buyer-visible validation error for a malformed creative. Either it
        # returns a response carrying failed-creative entries, or it raises
        # a typed AdCPError / Pydantic ValidationError / ValueError —
        # anything else (e.g. RuntimeError, KeyError, NameError) is a bug,
        # not an "ok" outcome the original ``except Exception: pass`` was
        # silently accepting.
        from pydantic import ValidationError

        from src.core.exceptions import AdCPError

        try:
            response = sync_creatives_raw(
                creatives=invalid_creatives,
                identity=identity,
            )
        except (AdCPError, ValidationError, ValueError):
            return  # typed validation surface — the contract is honored

        # sync_creatives_raw returned a response: it must report the failure
        # explicitly via a per-creative ``action == failed`` entry, not silently
        # accept the malformed creative as a success.
        from src.core.schemas import CreativeAction

        assert response is not None, "sync_creatives_raw must not return None for invalid input"
        failed = [c for c in response.creatives if c.action == CreativeAction.failed]
        succeeded = [c for c in response.creatives if c.action in (CreativeAction.created, CreativeAction.updated)]
        assert len(failed) >= 1, (
            f"Invalid creative should land in creatives[] with action=failed, "
            f"got {[c.action for c in response.creatives]}"
        )
        assert not succeeded, f"Invalid creative must not be reported as created/updated, got {succeeded!r}"


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

    The REST boundary now serializes the AdCP spec 3.0.0 two-layer envelope:
    recovery lives inside ``adcp_error.recovery`` and ``errors[0].recovery``,
    not at the top level. These tests confirm the full chain: AdCPError raised
    -> exception handler -> envelope JSON body.
    """

    @pytest.mark.parametrize(
        ("error_factory_path", "exc_message", "expected_status", "expected_code", "expected_recovery"),
        [
            (
                "src.core.exceptions.AdCPValidationError",
                "bad input",
                400,
                "VALIDATION_ERROR",
                "correctable",
            ),
            (
                "src.core.exceptions.AdCPAdapterError",
                "GAM unavailable",
                502,
                "SERVICE_UNAVAILABLE",
                "transient",
            ),
            (
                "src.core.exceptions.AdCPBudgetExceededError",
                "budget exceeds ceiling",
                422,
                "BUDGET_EXCEEDED",
                "correctable",
            ),
            (
                "src.core.exceptions.AdCPCreativeRejectedError",
                "creative failed policy review",
                422,
                "CREATIVE_REJECTED",
                "correctable",
            ),
            (
                "src.core.exceptions.AdCPProductUnavailableError",
                "product is offline",
                422,
                "PRODUCT_UNAVAILABLE",
                "correctable",
            ),
        ],
        ids=[
            "validation_error_correctable",
            "adapter_error_transient",
            "budget_exceeded_correctable",
            "creative_rejected_correctable",
            "product_unavailable_correctable",
        ],
    )
    def test_rest_recovery_field_propagates_from_typed_error(
        self, error_factory_path, exc_message, expected_status, expected_code, expected_recovery
    ):
        """REST exception handler propagates recovery hint from typed AdCPError to both envelope layers."""
        import importlib
        from unittest.mock import patch

        from starlette.testclient import TestClient

        from src.app import app
        from tests.helpers import assert_envelope_shape

        module_path, _, class_name = error_factory_path.rpartition(".")
        exc_class = getattr(importlib.import_module(module_path), class_name)

        with patch(
            "src.core.tools.capabilities.get_adcp_capabilities_raw",
            side_effect=exc_class(exc_message),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/capabilities")
            assert response.status_code == expected_status
            assert_envelope_shape(response.json(), expected_code, recovery=expected_recovery)

    def test_rest_custom_recovery_override_preserved(self):
        """Custom recovery= override is preserved through REST boundary (both layers)."""
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
            assert body["adcp_error"]["recovery"] == "transient", (
                "Custom recovery='transient' must be preserved at envelope level, not default 'terminal'"
            )
            assert body["errors"][0]["recovery"] == "transient"

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


class TestRestBoundaryAuditObservability:
    """A REST boundary error leaves an audit row when identity is resolvable.

    The MCP and A2A boundaries already write a tenant-scoped audit row on
    error; REST previously emitted only a WARNING log line because identity
    is not resolved on ``request.state`` at the exception-handler boundary.
    ``_envelope_response`` now resolves identity best-effort from the request's
    ``x-adcp-auth`` token and forwards it to ``record_boundary_error`` so all
    three transports leave the same persistent trail.
    """

    def test_rest_error_with_valid_token_writes_audit_row(self, sample_principal):
        """REST 4xx with a valid token writes an audit row scoped to the resolved principal."""
        from unittest.mock import patch

        from sqlalchemy import select
        from starlette.testclient import TestClient

        from src.app import app
        from src.core.database.database_session import get_db_session
        from src.core.database.models import AuditLog
        from src.core.exceptions import AdCPMediaBuyNotFoundError

        with patch(
            "src.core.tools.capabilities.get_adcp_capabilities_raw",
            side_effect=AdCPMediaBuyNotFoundError("buy_x missing"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get(
                "/api/v1/capabilities",
                headers={"x-adcp-auth": sample_principal["access_token"]},
            )

        assert response.status_code == 404

        with get_db_session() as session:
            stmt = select(AuditLog).filter_by(tenant_id="test_tenant", adapter_id="rest_boundary")
            audit_log = session.scalars(stmt).first()

        assert audit_log is not None, "REST boundary error must write an audit row when identity resolves"
        assert audit_log.success is False
        assert audit_log.principal_id == "test_principal"
        assert "buy_x missing" in (audit_log.error_message or "")
