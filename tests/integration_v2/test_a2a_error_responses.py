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
from unittest.mock import MagicMock, patch

import pytest
from a2a.types import Message, MessageSendParams, Task
from sqlalchemy import delete

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.core.database.database_session import get_db_session

# fmt: off
from src.core.database.models import CurrencyLimit, PricingOption
from src.core.database.models import Principal as ModelPrincipal
from src.core.database.models import Product as ModelProduct
from src.core.database.models import Tenant as ModelTenant

# fmt: on
from tests.integration_v2.conftest import (
    add_required_setup_data,
    create_test_product_with_pricing,
)
from tests.utils.a2a_helpers import create_a2a_message_with_skill

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

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
            session.execute(delete(PricingOption).where(PricingOption.tenant_id == "a2a_error_test"))
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
            session.flush()  # Ensure tenant exists in database before add_required_setup_data queries it

            # Add required setup data before creating product
            add_required_setup_data(session, "a2a_error_test")

            # Create product using new pricing model
            # NOTE: formats must be structured FormatId objects with agent_url, not strings
            product = create_test_product_with_pricing(
                session=session,
                tenant_id="a2a_error_test",
                product_id="a2a_error_product",
                name="A2A Error Test Product",
                description="Product for error testing",
                pricing_model="CPM",
                rate="10.0",
                is_fixed=True,
                min_spend_per_package="1000.0",
                formats=[{"id": "display_300x250", "agent_url": "https://test.example.com"}],
                delivery_type="guaranteed",
                targeting_template={},
            )

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
    def test_principal(self, integration_db, test_tenant):
        """Create test principal."""
        with get_db_session() as session:
            principal = ModelPrincipal(
                tenant_id=test_tenant["tenant_id"],
                principal_id="a2a_error_principal",
                name="A2A Error Test Principal",
                access_token="a2a_error_token_123",
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
        return create_a2a_message_with_skill(skill_name, parameters)

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
                "brand_manifest": {"name": "Test Campaign"},
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
            artifact_data = (
                artifact.parts[0].root.data
                if hasattr(artifact.parts[0], "root") and hasattr(artifact.parts[0].root, "data")
                else {}
            )

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
                "brand_manifest": {"name": "Test Campaign"},
                "packages": [
                    {
                        "buyer_ref": "pkg_1",
                        "product_id": "a2a_error_product",  # Use product_id (singular) not products (plural)
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
            artifact_data = (
                artifact.parts[0].root.data
                if hasattr(artifact.parts[0], "root") and hasattr(artifact.parts[0].root, "data")
                else {}
            )

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
                "brand_manifest": {"name": "Test Campaign"},
                "packages": [
                    {
                        "buyer_ref": "pkg_1",
                        "product_id": "a2a_error_product",  # Use product_id (singular) not products (plural)
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
            artifact_data = (
                artifact.parts[0].root.data
                if hasattr(artifact.parts[0], "root") and hasattr(artifact.parts[0].root, "data")
                else {}
            )

            # CRITICAL ASSERTIONS: Success response
            assert artifact_data["success"] is True, "success must be True for successful operation"
            assert (
                artifact_data.get("errors") is None or len(artifact_data.get("errors", [])) == 0
            ), "errors field must be None or empty array for success"
            assert "media_buy_id" in artifact_data, "Success response must include media_buy_id"
            assert artifact_data["media_buy_id"] is not None, "media_buy_id must not be None for success"

    async def test_create_media_buy_response_includes_all_adcp_fields(self, handler, test_tenant, test_principal):
        """Test that A2A response includes all AdCP domain fields (not just cherry-picked ones).

        Per AdCP v2.4 spec and PR #113:
        - Domain responses contain ONLY domain fields (buyer_ref, media_buy_id, packages, errors)
        - Protocol fields (status, message, task_id, context_id) are added by ProtocolEnvelope wrapper
        - adcp_version is NOT included in individual responses (indicated by schema URL path)

        This test verifies that all domain fields from CreateMediaBuyResponse schema are preserved
        when wrapped by the A2A handler.
        """
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
                "brand_manifest": {"name": "Test Campaign"},
                "packages": [
                    {
                        "buyer_ref": "pkg_1",
                        "product_id": "a2a_error_product",  # Use product_id (singular) not products (plural)
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
            artifact_data = (
                artifact.parts[0].root.data
                if hasattr(artifact.parts[0], "root") and hasattr(artifact.parts[0].root, "data")
                else {}
            )

            # CRITICAL ASSERTIONS: All AdCP domain fields from CreateMediaBuyResponse schema
            # Required AdCP domain field
            assert "buyer_ref" in artifact_data, "Must include buyer_ref (AdCP spec required domain field)"

            # Optional AdCP domain fields
            assert "media_buy_id" in artifact_data, "Must include media_buy_id (AdCP spec domain field)"
            assert "packages" in artifact_data, "Must include packages (AdCP spec domain field)"
            assert "creative_deadline" in artifact_data, "Must include creative_deadline (AdCP spec domain field)"

            # errors field should be present (None or empty for success, populated for errors)
            assert "errors" in artifact_data, "Must include errors field (AdCP spec domain field)"

            # A2A-specific augmentation fields (added by wrapper layer)
            assert "success" in artifact_data, "A2A wrapper must add success field"
            assert "message" in artifact_data, "A2A wrapper must add message field"

            # Verify success case
            assert artifact_data["success"] is True, "Success should be True for successful operation"
            assert artifact_data["media_buy_id"] is not None, "media_buy_id must not be None for success"


@pytest.mark.integration
@pytest.mark.requires_db
@pytest.mark.asyncio
class TestA2AErrorResponseStructure:
    """Test the structure of error responses to ensure consistency."""

    @pytest.fixture
    def handler(self):
        """Create A2A handler instance."""
        return AdCPRequestHandler()

    async def test_error_response_has_consistent_structure(self, integration_db, handler):
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
                parameters={"brand_manifest": {"name": "test"}},
                auth_token="test_token",  # Missing required fields
            )

            # Verify error response structure
            assert isinstance(result, dict), "Error response must be dict"
            assert "success" in result, "Error response must have success field"
            assert result["success"] is False, "Error response success must be False"
            assert "message" in result, "Error response must have message field"
            assert "required_parameters" in result, "Validation error must list required parameters"

    async def test_errors_field_structure_from_validation_error(self, integration_db, handler):
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
                    "brand_manifest": {"name": "test"},
                    # Missing: packages, budget, start_time, end_time
                },
                auth_token="test_token",
            )

            # Verify this is a validation error response
            assert result["success"] is False, "Validation error should have success=False"
            assert "required_parameters" in result, "Validation error should list required params"
