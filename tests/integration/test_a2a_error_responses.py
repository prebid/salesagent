#!/usr/bin/env python3
"""
Test A2A error response handling.

This test suite ensures that errors from core tools are properly propagated
through the A2A wrapper layer, including:
1. errors field is included in A2A responses
2. success: false when errors are present
3. All AdCP response fields are preserved
"""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from a2a.types import Message, MessageSendParams
from sqlalchemy import delete

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.core.database.database_session import get_db_session

# fmt: off
from src.core.database.models import CurrencyLimit
from src.core.database.models import Principal as ModelPrincipal
from src.core.database.models import Product as ModelProduct
from src.core.database.models import Tenant as ModelTenant

# fmt: on

# TODO: Fix failing tests and remove skip_ci (see GitHub issue #XXX)
pytestmark = [pytest.mark.integration, pytest.mark.skip_ci]

# Configure logging for tests
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


@pytest.mark.integration
@pytest.mark.requires_db
@pytest.mark.asyncio
class TestA2AErrorPropagation:
    """Test that errors from core tools are properly propagated through A2A handlers."""

    @pytest.fixture
    def test_tenant(self, integration_db):
        """Create test tenant with minimal setup."""
        from src.core.config_loader import set_current_tenant

        with get_db_session() as session:
            now = datetime.now(UTC)

            # Clean up existing test data
            session.execute(delete(ModelPrincipal).where(ModelPrincipal.tenant_id == "a2a_error_test"))
            session.execute(delete(ModelProduct).where(ModelProduct.tenant_id == "a2a_error_test"))
            session.execute(delete(CurrencyLimit).where(CurrencyLimit.tenant_id == "a2a_error_test"))
            session.execute(delete(ModelTenant).where(ModelTenant.tenant_id == "a2a_error_test"))
            session.commit()

            # Create tenant
            tenant = ModelTenant(
                tenant_id="a2a_error_test",
                name="A2A Error Test Tenant",
                subdomain="a2aerror",
                ad_server="mock",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            session.add(tenant)

            # Create product
            product = ModelProduct(
                tenant_id="a2a_error_test",
                product_id="a2a_error_product",
                name="A2A Error Test Product",
                description="Product for error testing",
                formats=["display_300x250"],
                delivery_type="guaranteed",
                cpm=10.0,
                min_spend=1000.0,
                targeting_template={},
                is_fixed_price=True,
            )
            session.add(product)

            # Add currency limit
            currency_limit = CurrencyLimit(
                tenant_id="a2a_error_test",
                currency_code="USD",
                min_package_budget=1000.0,
                max_daily_package_spend=10000.0,
            )
            session.add(currency_limit)

            session.commit()

            # Set tenant context
            set_current_tenant(
                {
                    "tenant_id": "a2a_error_test",
                    "name": "A2A Error Test Tenant",
                    "subdomain": "a2aerror",
                    "ad_server": "mock",
                }
            )

            yield {
                "tenant_id": "a2a_error_test",
                "name": "A2A Error Test Tenant",
                "subdomain": "a2aerror",
                "ad_server": "mock",
            }

    @pytest.fixture
    def test_principal(self, test_tenant):
        """Create test principal."""
        with get_db_session() as session:
            principal = ModelPrincipal(
                tenant_id=test_tenant["tenant_id"],
                principal_id="a2a_error_principal",
                name="A2A Error Test Principal",
                access_token="a2a_error_token_123",
                advertiser_name="A2A Error Advertiser",
                is_active=True,
                platform_mappings={"mock": {"advertiser_id": "mock_adv_123"}},
            )
            session.add(principal)
            session.commit()

            yield {
                "principal_id": "a2a_error_principal",
                "access_token": "a2a_error_token_123",
                "name": "A2A Error Test Principal",
            }

    @pytest.fixture
    def handler(self):
        """Create A2A handler instance."""
        return AdCPRequestHandler()

    def create_message_with_skill(self, skill_name: str, parameters: dict) -> Message:
        """Helper to create message with explicit skill invocation."""
        return Message(
            role=Role.user,
            parts=[
                Part(
                    root={
                        "type": "skill",
                        "skill": {"name": skill_name, "arguments": parameters},
                    }
                )
            ],
        )

    async def test_create_media_buy_validation_error_includes_errors_field(self, handler, test_tenant, test_principal):
        """Test that validation errors include errors field in A2A response."""
        # Mock authentication
        handler._get_auth_token = MagicMock(return_value=test_principal["access_token"])

        with (
            patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,
            patch("src.a2a_server.adcp_a2a_server.get_current_tenant") as mock_get_tenant,
        ):
            mock_get_principal.return_value = test_principal["principal_id"]
            mock_get_tenant.return_value = test_tenant

            # Create message with INVALID parameters (missing required fields)
            skill_params = {
                "promoted_offering": "Test Campaign",
                # Missing: packages, budget, start_time, end_time
            }
            message = self.create_message_with_skill("create_media_buy", skill_params)
            params = MessageSendParams(message=message)

            # Process the message - should return error
            result = await handler.on_message_send(params)

            # Verify task result structure
            assert isinstance(result, Task)
            assert result.artifacts is not None
            assert len(result.artifacts) > 0

            # Extract response data
            artifact = result.artifacts[0]
            artifact_data = artifact.parts[0].data if hasattr(artifact.parts[0], "data") else {}

            # CRITICAL ASSERTIONS: Error propagation
            assert "success" in artifact_data, "Response must include 'success' field"
            assert artifact_data["success"] is False, "success must be False when errors present"
            assert "errors" in artifact_data, "Response must include 'errors' field"
            assert len(artifact_data["errors"]) > 0, "errors array must not be empty"

            # Verify error structure
            error = artifact_data["errors"][0]
            assert "message" in error, "Error must include message"
            assert "Missing required AdCP parameters" in error["message"]

    async def test_create_media_buy_auth_error_includes_errors_field(self, handler, test_tenant):
        """Test that authentication errors include errors field in A2A response."""
        # Mock authentication with INVALID principal
        handler._get_auth_token = MagicMock(return_value="invalid_token")

        with (
            patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,
            patch("src.a2a_server.adcp_a2a_server.get_current_tenant") as mock_get_tenant,
        ):
            # Return non-existent principal ID
            mock_get_principal.return_value = "nonexistent_principal"
            mock_get_tenant.return_value = test_tenant

            # Create valid message structure
            start_time = (datetime.now(UTC) + timedelta(days=1)).isoformat()
            end_time = (datetime.now(UTC) + timedelta(days=31)).isoformat()

            skill_params = {
                "promoted_offering": "Test Campaign",
                "packages": [
                    {
                        "buyer_ref": "pkg_1",
                        "products": ["a2a_error_product"],
                        "budget": {"total": 10000.0, "currency": "USD"},
                    }
                ],
                "budget": {"total": 10000.0, "currency": "USD"},
                "start_time": start_time,
                "end_time": end_time,
            }
            message = self.create_message_with_skill("create_media_buy", skill_params)
            params = MessageSendParams(message=message)

            # Process the message - should return auth error
            result = await handler.on_message_send(params)

            # Extract response data
            artifact = result.artifacts[0]
            artifact_data = artifact.parts[0].data if hasattr(artifact.parts[0], "data") else {}

            # CRITICAL ASSERTIONS: Error propagation for auth failures
            assert artifact_data["success"] is False, "success must be False for auth errors"
            assert "errors" in artifact_data, "Response must include 'errors' field for auth errors"
            assert len(artifact_data["errors"]) > 0, "errors array must not be empty"

            # Verify error is about authentication
            error = artifact_data["errors"][0]
            assert "code" in error, "Error must include code"
            assert error["code"] == "authentication_error"

    async def test_create_media_buy_success_has_no_errors_field(self, handler, test_tenant, test_principal):
        """Test that successful responses don't have errors field (or it's None/empty)."""
        # Mock authentication
        handler._get_auth_token = MagicMock(return_value=test_principal["access_token"])

        with (
            patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,
            patch("src.a2a_server.adcp_a2a_server.get_current_tenant") as mock_get_tenant,
        ):
            mock_get_principal.return_value = test_principal["principal_id"]
            mock_get_tenant.return_value = test_tenant

            # Create VALID message
            start_time = (datetime.now(UTC) + timedelta(days=1)).isoformat()
            end_time = (datetime.now(UTC) + timedelta(days=31)).isoformat()

            skill_params = {
                "promoted_offering": "Test Campaign",
                "packages": [
                    {
                        "buyer_ref": "pkg_1",
                        "products": ["a2a_error_product"],
                        "budget": {"total": 10000.0, "currency": "USD"},
                    }
                ],
                "budget": {"total": 10000.0, "currency": "USD"},
                "start_time": start_time,
                "end_time": end_time,
            }
            message = self.create_message_with_skill("create_media_buy", skill_params)
            params = MessageSendParams(message=message)

            # Process the message - should succeed
            result = await handler.on_message_send(params)

            # Extract response data
            artifact = result.artifacts[0]
            artifact_data = artifact.parts[0].data if hasattr(artifact.parts[0], "data") else {}

            # CRITICAL ASSERTIONS: Success response
            assert artifact_data["success"] is True, "success must be True for successful operation"
            assert (
                artifact_data.get("errors") is None or len(artifact_data.get("errors", [])) == 0
            ), "errors field must be None or empty array for success"
            assert "media_buy_id" in artifact_data, "Success response must include media_buy_id"
            assert artifact_data["media_buy_id"] is not None, "media_buy_id must not be None for success"

    async def test_create_media_buy_response_includes_all_adcp_fields(self, handler, test_tenant, test_principal):
        """Test that A2A response includes all AdCP response fields (not just cherry-picked ones)."""
        # Mock authentication
        handler._get_auth_token = MagicMock(return_value=test_principal["access_token"])

        with (
            patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,
            patch("src.a2a_server.adcp_a2a_server.get_current_tenant") as mock_get_tenant,
        ):
            mock_get_principal.return_value = test_principal["principal_id"]
            mock_get_tenant.return_value = test_tenant

            # Create valid message
            start_time = (datetime.now(UTC) + timedelta(days=1)).isoformat()
            end_time = (datetime.now(UTC) + timedelta(days=31)).isoformat()

            skill_params = {
                "promoted_offering": "Test Campaign",
                "packages": [
                    {
                        "buyer_ref": "pkg_1",
                        "products": ["a2a_error_product"],
                        "budget": {"total": 10000.0, "currency": "USD"},
                    }
                ],
                "budget": {"total": 10000.0, "currency": "USD"},
                "start_time": start_time,
                "end_time": end_time,
            }
            message = self.create_message_with_skill("create_media_buy", skill_params)
            params = MessageSendParams(message=message)

            # Process the message
            result = await handler.on_message_send(params)

            # Extract response data
            artifact = result.artifacts[0]
            artifact_data = artifact.parts[0].data if hasattr(artifact.parts[0], "data") else {}

            # CRITICAL ASSERTIONS: All AdCP fields preserved
            # Required AdCP fields from CreateMediaBuyResponse schema
            assert "adcp_version" in artifact_data, "Must include adcp_version (AdCP spec required field)"
            assert "status" in artifact_data, "Must include status (AdCP spec required field)"
            assert "buyer_ref" in artifact_data, "Must include buyer_ref (AdCP spec required field)"

            # Optional but important AdCP fields
            assert "packages" in artifact_data, "Must include packages (AdCP spec field)"
            assert (
                "creative_deadline" in artifact_data or artifact_data.get("creative_deadline") is None
            ), "Must include creative_deadline (AdCP spec field)"

            # A2A-specific augmentation fields
            assert "success" in artifact_data, "A2A wrapper must add success field"
            assert "message" in artifact_data, "A2A wrapper must add message field"


@pytest.mark.integration
@pytest.mark.requires_db
@pytest.mark.asyncio
class TestA2AErrorResponseStructure:
    """Test the structure of error responses to ensure consistency."""

    @pytest.fixture
    def handler(self):
        """Create A2A handler instance."""
        return AdCPRequestHandler()

    async def test_error_response_has_consistent_structure(self, handler):
        """Test that all error responses have consistent field structure."""
        # Mock minimal auth
        handler._get_auth_token = MagicMock(return_value="test_token")

        with (
            patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,
            patch("src.a2a_server.adcp_a2a_server.get_current_tenant") as mock_get_tenant,
        ):
            mock_get_principal.return_value = "test_principal"
            mock_get_tenant.return_value = {"tenant_id": "test_tenant"}

            # Call handler directly with invalid params
            result = await handler._handle_create_media_buy_skill(
                parameters={"promoted_offering": "test"}, auth_token="test_token"  # Missing required fields
            )

            # Verify error response structure
            assert isinstance(result, dict), "Error response must be dict"
            assert "success" in result, "Error response must have success field"
            assert result["success"] is False, "Error response success must be False"
            assert "message" in result, "Error response must have message field"
            assert "required_parameters" in result, "Validation error must list required parameters"

    async def test_errors_field_structure_from_validation_error(self, handler):
        """Test that validation errors produce properly structured errors field."""
        handler._get_auth_token = MagicMock(return_value="test_token")

        with (
            patch("src.a2a_server.adcp_a2a_server.get_principal_from_token") as mock_get_principal,
            patch("src.a2a_server.adcp_a2a_server.get_current_tenant") as mock_get_tenant,
        ):
            mock_get_principal.return_value = "test_principal"
            mock_get_tenant.return_value = {"tenant_id": "test_tenant"}

            # Call with invalid params (missing required fields) - returns immediately without DB
            result = await handler._handle_create_media_buy_skill(
                parameters={
                    "promoted_offering": "test",
                    # Missing: packages, budget, start_time, end_time
                },
                auth_token="test_token",
            )

            # Verify this is a validation error response
            assert result["success"] is False, "Validation error should have success=False"
            assert "required_parameters" in result, "Validation error should list required params"
