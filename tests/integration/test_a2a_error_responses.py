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
from unittest.mock import MagicMock

import pytest
from a2a.server.routes.common import ServerCallContext
from a2a.types import Message, SendMessageRequest, Task
from sqlalchemy import delete

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.core.database.database_session import get_db_session

# fmt: off
from src.core.database.models import CurrencyLimit, PricingOption
from src.core.database.models import Principal as ModelPrincipal
from src.core.database.models import Product as ModelProduct
from src.core.database.models import Tenant as ModelTenant
from src.core.resolved_identity import ResolvedIdentity

# fmt: on
from tests.helpers.adcp_factories import create_test_package_request_dict
from tests.integration.conftest import (
    add_required_setup_data,
    create_test_product_with_pricing,
)
from tests.utils.a2a_helpers import create_a2a_message_with_skill, extract_data_from_artifact

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
            # Note: human_review_required=False ensures media buy runs immediately
            # rather than going to approval workflow (needed for response field tests)
            tenant = ModelTenant(
                tenant_id="a2a_error_test",
                name="A2A Error Test Tenant",
                subdomain="a2aerror",
                ad_server="mock",
                is_active=True,
                human_review_required=False,
                created_at=now,
                updated_at=now,
            )
            session.add(tenant)
            session.flush()  # Ensure tenant exists in database before add_required_setup_data queries it

            # Add required setup data before creating product
            add_required_setup_data(session, "a2a_error_test")

            # Create product using new pricing model
            # NOTE: format_ids must be structured FormatId objects with agent_url, not strings
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
                format_ids=[{"id": "display_300x250", "agent_url": "https://test.example.com"}],
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
                    "human_review_required": False,
                }
            )

            yield {
                "tenant_id": "a2a_error_test",
                "name": "A2A Error Test Tenant",
                "subdomain": "a2aerror",
                "ad_server": "mock",
                "human_review_required": False,
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

    def extract_data_from_artifact(self, artifact) -> dict:
        """Extract DataPart data from A2A artifact.

        A2A artifacts may have multiple parts: optional TextPart followed by DataPart.
        In a2a-sdk 1.0, Part.data is a protobuf Value, not a plain dict.
        """
        from tests.utils.a2a_helpers import extract_data_from_artifact

        return extract_data_from_artifact(artifact)

    async def test_create_media_buy_validation_error_includes_errors_field(self, handler, test_tenant, test_principal):
        """Test that validation errors include errors field in A2A response."""
        identity = ResolvedIdentity(
            principal_id=test_principal["principal_id"],
            tenant_id=test_tenant["tenant_id"],
            tenant=test_tenant,
            auth_token=test_principal["access_token"],
            protocol="a2a",
        )
        handler._get_auth_token = MagicMock(return_value=test_principal["access_token"])
        handler._resolve_a2a_identity = MagicMock(return_value=identity)

        from src.core.config_loader import set_current_tenant

        set_current_tenant(test_tenant)

        # Create message with INVALID parameters (missing required fields)
        skill_params = {
            "brand": {"domain": "testbrand.com"},
            # Missing: packages, budget, start_time, end_time
        }
        message = self.create_message_with_skill("create_media_buy", skill_params)
        params = SendMessageRequest(message=message)

        # Process the message - should return error
        result = await handler.on_message_send(params, ServerCallContext())

        # Verify task result structure
        assert isinstance(result, Task)
        assert result.artifacts is not None
        assert len(result.artifacts) > 0

        # Extract response data from artifact (handles TextPart + DataPart structure)
        artifact = result.artifacts[0]
        artifact_data = self.extract_data_from_artifact(artifact)

        # CRITICAL ASSERTIONS: Error propagation via the spec two-layer envelope.
        # Skill handlers now raise typed AdCPError; the dispatcher surfaces the
        # ``error_envelope`` (built by ``build_two_layer_error_envelope``) as the
        # DataPart. The wire shape is the spec envelope (adcp_error + errors),
        # not the previous {success: False, errors: [...]} ad-hoc dict.
        assert "adcp_error" in artifact_data, "Response must carry the envelope-level adcp_error key"
        assert (
            artifact_data["adcp_error"]["code"] == "VALIDATION_ERROR"
        ), f"Wire code must be VALIDATION_ERROR, got {artifact_data['adcp_error'].get('code')}"
        assert "errors" in artifact_data, "Response must include 'errors' field"
        assert len(artifact_data["errors"]) > 0, "errors array must not be empty"

        # Verify error structure
        error = artifact_data["errors"][0]
        assert "message" in error, "Error must include message"
        assert "Missing required AdCP parameters" in error["message"]
        assert error["code"] == "VALIDATION_ERROR"

    async def test_create_media_buy_auth_error_includes_errors_field(self, handler, test_tenant):
        """Test that authentication errors include errors field in A2A response."""
        # Mock identity with non-existent principal — simulates resolved but invalid principal
        identity = ResolvedIdentity(
            principal_id="nonexistent_principal",
            tenant_id=test_tenant["tenant_id"],
            tenant=test_tenant,
            auth_token="invalid_token",
            protocol="a2a",
        )
        handler._get_auth_token = MagicMock(return_value="invalid_token")
        handler._resolve_a2a_identity = MagicMock(return_value=identity)

        from src.core.config_loader import set_current_tenant

        set_current_tenant(test_tenant)

        # Create valid message structure
        start_time = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        end_time = (datetime.now(UTC) + timedelta(days=31)).isoformat()

        skill_params = {
            "brand": {"domain": "testbrand.com"},
            "packages": [
                create_test_package_request_dict(
                    product_id="a2a_error_product",
                    pricing_option_id="cpm_usd_fixed",
                    budget=10000.0,
                )
            ],
            "start_time": start_time,
            "end_time": end_time,
        }
        message = self.create_message_with_skill("create_media_buy", skill_params)
        params = SendMessageRequest(message=message)

        # Process the message - should return auth error
        result = await handler.on_message_send(params, ServerCallContext())

        # Extract response data from artifact (handles TextPart + DataPart structure)
        artifact = result.artifacts[0]
        artifact_data = self.extract_data_from_artifact(artifact)

        # "Principal not found" is an established Pattern A site — the
        # impl returns a CreateMediaBuyError variant carrying the
        # Error(code=AUTH_REQUIRED) inside its ``errors`` list, NOT a raised
        # AdCPAuthorizationError producing the envelope. The advisory pattern
        # is documented in test_media_buy.py::test_principal_not_found_returns_error_response
        # and is allowlist-permanent per the error-emission design decisions.
        assert "errors" in artifact_data, "Response must include 'errors' field for auth errors"
        assert len(artifact_data["errors"]) > 0, "errors array must not be empty"

        # Verify error is about authentication
        error = artifact_data["errors"][0]
        assert "code" in error, "Error must include code"
        assert error["code"] == "AUTH_REQUIRED"

    async def test_create_media_buy_success_has_no_errors_field(self, handler, test_tenant, test_principal):
        """Test that successful responses don't have errors field (or it's None/empty)."""
        identity = ResolvedIdentity(
            principal_id=test_principal["principal_id"],
            tenant_id=test_tenant["tenant_id"],
            tenant=test_tenant,
            auth_token=test_principal["access_token"],
            protocol="a2a",
        )
        handler._get_auth_token = MagicMock(return_value=test_principal["access_token"])
        handler._resolve_a2a_identity = MagicMock(return_value=identity)

        from src.core.config_loader import set_current_tenant

        set_current_tenant(test_tenant)

        # Create VALID message
        start_time = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        end_time = (datetime.now(UTC) + timedelta(days=31)).isoformat()

        skill_params = {
            "brand": {"domain": "testbrand.com"},
            "packages": [
                create_test_package_request_dict(
                    product_id="a2a_error_product",
                    pricing_option_id="cpm_usd_fixed",
                    budget=10000.0,
                )
            ],
            "start_time": start_time,
            "end_time": end_time,
        }
        message = self.create_message_with_skill("create_media_buy", skill_params)
        params = SendMessageRequest(message=message)

        # Process the message - should succeed
        result = await handler.on_message_send(params, ServerCallContext())

        # Extract response data from artifact (handles TextPart + DataPart structure)
        artifact = result.artifacts[0]
        artifact_data = self.extract_data_from_artifact(artifact)

        # CRITICAL ASSERTIONS: Success response
        assert artifact_data["success"] is True, "success must be True for successful operation"
        assert (
            artifact_data.get("errors") is None or len(artifact_data.get("errors", [])) == 0
        ), "errors field must be None or empty array for success"
        assert "media_buy_id" in artifact_data, "Success response must include media_buy_id"
        assert artifact_data["media_buy_id"] is not None, "media_buy_id must not be None for success"

    async def test_sync_creatives_missing_creatives_param_wire_envelope(self, handler, test_tenant, test_principal):
        """sync_creatives missing 'creatives' param surfaces two-layer envelope on the A2A wire.

        Mirrors the gold-standard test_create_media_buy_validation_error_includes_errors_field
        pattern: real on_message_send → real handler raises AdCPValidationError →
        dispatcher routes through _build_failed_skill_result → wire envelope in DataPart.

        Konstantine review (PR #1306, 2026-05-24): mock-only tests do not prove
        wiring; this exercises the full A2A transport pipeline end-to-end.
        """
        identity = ResolvedIdentity(
            principal_id=test_principal["principal_id"],
            tenant_id=test_tenant["tenant_id"],
            tenant=test_tenant,
            auth_token=test_principal["access_token"],
            protocol="a2a",
        )
        handler._get_auth_token = MagicMock(return_value=test_principal["access_token"])
        handler._resolve_a2a_identity = MagicMock(return_value=identity)

        from src.core.config_loader import set_current_tenant

        set_current_tenant(test_tenant)

        # INVALID parameters — no 'creatives' key, which the handler explicitly
        # validates against (src/a2a_server/adcp_a2a_server.py:1677-1681).
        skill_params = {"dry_run": True}
        message = self.create_message_with_skill("sync_creatives", skill_params)
        params = SendMessageRequest(message=message)

        result = await handler.on_message_send(params, ServerCallContext())

        assert isinstance(result, Task)
        assert result.artifacts is not None
        assert len(result.artifacts) > 0

        artifact = result.artifacts[0]
        artifact_data = self.extract_data_from_artifact(artifact)

        # CRITICAL: full two-layer envelope on the wire.
        assert "adcp_error" in artifact_data, "Wire envelope must carry top-level adcp_error key"
        assert (
            artifact_data["adcp_error"]["code"] == "VALIDATION_ERROR"
        ), f"Wire code must be VALIDATION_ERROR, got {artifact_data['adcp_error'].get('code')}"
        assert "errors" in artifact_data, "Wire envelope must include errors array"
        assert len(artifact_data["errors"]) > 0, "errors array must not be empty"

        error = artifact_data["errors"][0]
        assert error["code"] == "VALIDATION_ERROR", f"Per-error code must match envelope code, got {error.get('code')}"
        assert "creatives" in error["message"], f"Error message must name the missing field, got: {error['message']}"

    async def test_create_media_buy_response_includes_all_adcp_fields(self, handler, test_tenant, test_principal):
        """Test that A2A response includes all AdCP domain fields (not just cherry-picked ones).

        Per AdCP v2.4 spec and PR #113:
        - Domain responses contain ONLY domain fields (media_buy_id, packages, errors)
        - Protocol fields (status, message, task_id, context_id) are added by ProtocolEnvelope wrapper
        - adcp_version is NOT included in individual responses (indicated by schema URL path)

        This test verifies that all domain fields from CreateMediaBuyResponse schema are preserved
        when wrapped by the A2A handler.
        """
        identity = ResolvedIdentity(
            principal_id=test_principal["principal_id"],
            tenant_id=test_tenant["tenant_id"],
            tenant=test_tenant,
            auth_token=test_principal["access_token"],
            protocol="a2a",
        )
        handler._get_auth_token = MagicMock(return_value=test_principal["access_token"])
        handler._resolve_a2a_identity = MagicMock(return_value=identity)

        from src.core.config_loader import set_current_tenant

        set_current_tenant(test_tenant)

        # Create valid message
        start_time = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        end_time = (datetime.now(UTC) + timedelta(days=31)).isoformat()

        skill_params = {
            "brand": {"domain": "testbrand.com"},
            "packages": [
                create_test_package_request_dict(
                    product_id="a2a_error_product",
                    pricing_option_id="cpm_usd_fixed",
                    budget=10000.0,
                )
            ],
            "start_time": start_time,
            "end_time": end_time,
        }
        message = self.create_message_with_skill("create_media_buy", skill_params)
        params = SendMessageRequest(message=message)

        # Process the message
        result = await handler.on_message_send(params, ServerCallContext())

        # Extract response data from artifact (handles TextPart + DataPart structure)
        artifact = result.artifacts[0]
        artifact_data = self.extract_data_from_artifact(artifact)

        # CRITICAL ASSERTIONS: All AdCP domain fields from CreateMediaBuyResponse schema
        # Required AdCP domain fields that were set (non-None values)
        assert "media_buy_id" in artifact_data, "Must include media_buy_id (AdCP spec domain field)"
        assert "packages" in artifact_data, "Must include packages (AdCP spec domain field)"
        assert "creative_deadline" in artifact_data, "Must include creative_deadline (AdCP spec domain field)"

        # Per AdCP spec, optional fields with None values should be omitted
        # errors field should NOT be present for successful operations (no errors)
        assert "errors" not in artifact_data, "errors field should be omitted when None (AdCP spec compliance)"

        # A2A-specific augmentation fields (added by wrapper layer)
        assert "success" in artifact_data, "A2A wrapper must add success field"
        assert "message" in artifact_data, "A2A wrapper must add message field"

        # Verify success case
        assert artifact_data["success"] is True, "Success should be True for successful operation"
        assert artifact_data["media_buy_id"] is not None, "media_buy_id must not be None for success"

    async def test_create_creative_missing_required_params_wire_envelope(self, handler, test_tenant, test_principal):
        """create_creative missing required params → two-layer envelope on the A2A wire.

        Required params per src/a2a_server/adcp_a2a_server.py:1745: format_id,
        content_uri, name. Omitting all three triggers the missing-params check
        BEFORE the unimplemented TODO branch, so this exercises only the
        validation path through the real dispatcher.

        Mirrors test_create_media_buy_validation_error_includes_errors_field at
        line 164 — the gold-standard pattern Konstantine cited.
        """
        identity = ResolvedIdentity(
            principal_id=test_principal["principal_id"],
            tenant_id=test_tenant["tenant_id"],
            tenant=test_tenant,
            auth_token=test_principal["access_token"],
            protocol="a2a",
        )
        handler._get_auth_token = MagicMock(return_value=test_principal["access_token"])
        handler._resolve_a2a_identity = MagicMock(return_value=identity)

        from src.core.config_loader import set_current_tenant

        set_current_tenant(test_tenant)

        # INVALID parameters — none of {format_id, content_uri, name}.
        message = self.create_message_with_skill("create_creative", {})
        params = SendMessageRequest(message=message)

        result = await handler.on_message_send(params, ServerCallContext())

        assert isinstance(result, Task)
        assert result.artifacts is not None
        assert len(result.artifacts) > 0

        artifact_data = self.extract_data_from_artifact(result.artifacts[0])

        # Full two-layer envelope on the wire.
        assert "adcp_error" in artifact_data, "Wire envelope must carry top-level adcp_error key"
        assert (
            artifact_data["adcp_error"]["code"] == "VALIDATION_ERROR"
        ), f"Wire code must be VALIDATION_ERROR, got {artifact_data['adcp_error'].get('code')}"
        assert "errors" in artifact_data, "Wire envelope must include errors array"
        assert len(artifact_data["errors"]) > 0, "errors array must not be empty"

        error = artifact_data["errors"][0]
        assert error["code"] == "VALIDATION_ERROR", f"Per-error code must match envelope code, got {error.get('code')}"
        # Per-error message enumerates the missing required fields.
        msg = error["message"]
        assert (
            "format_id" in msg and "content_uri" in msg and "name" in msg
        ), f"Per-error message must name all missing required fields, got: {msg}"

    async def test_assign_creative_missing_required_params_wire_envelope(self, handler, test_tenant, test_principal):
        """assign_creative missing required params → two-layer envelope on the A2A wire.

        Required params per src/a2a_server/adcp_a2a_server.py:1783: media_buy_id,
        package_id, creative_id. Sending only media_buy_id triggers the missing-
        params check BEFORE the unimplemented TODO branch.

        Mirrors test_create_media_buy_validation_error_includes_errors_field at
        line 164 — the gold-standard pattern Konstantine cited.
        """
        identity = ResolvedIdentity(
            principal_id=test_principal["principal_id"],
            tenant_id=test_tenant["tenant_id"],
            tenant=test_tenant,
            auth_token=test_principal["access_token"],
            protocol="a2a",
        )
        handler._get_auth_token = MagicMock(return_value=test_principal["access_token"])
        handler._resolve_a2a_identity = MagicMock(return_value=identity)

        from src.core.config_loader import set_current_tenant

        set_current_tenant(test_tenant)

        # INVALID parameters — only media_buy_id, missing package_id + creative_id.
        message = self.create_message_with_skill("assign_creative", {"media_buy_id": "mb_123"})
        params = SendMessageRequest(message=message)

        result = await handler.on_message_send(params, ServerCallContext())

        assert isinstance(result, Task)
        assert result.artifacts is not None
        assert len(result.artifacts) > 0

        artifact_data = self.extract_data_from_artifact(result.artifacts[0])

        assert "adcp_error" in artifact_data
        assert artifact_data["adcp_error"]["code"] == "VALIDATION_ERROR"
        assert "errors" in artifact_data
        assert len(artifact_data["errors"]) > 0

        error = artifact_data["errors"][0]
        assert error["code"] == "VALIDATION_ERROR"
        # Per-error message enumerates ONLY the missing fields (not the provided media_buy_id).
        msg = error["message"]
        assert "package_id" in msg and "creative_id" in msg, f"Per-error message must name missing fields, got: {msg}"


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
        """Skill handlers raise typed AdCPValidationError on missing required params.

        Previously the handler returned a custom error dict bypassing the
        envelope builder; Konstantine's structural follow-up flagged this as
        Critical. Now the handler raises and the outer dispatcher's
        ``_build_failed_skill_result`` produces the spec-compliant
        two-layer envelope. This test verifies the contract at the
        skill-handler layer.
        """
        from src.core.exceptions import AdCPValidationError

        identity = ResolvedIdentity(
            principal_id="test_principal",
            tenant_id="test_tenant",
            tenant={"tenant_id": "test_tenant"},
            auth_token="test_token",
            protocol="a2a",
        )

        with pytest.raises(AdCPValidationError) as exc_info:
            await handler._handle_create_media_buy_skill(
                parameters={"brand": {"domain": "testbrand.com"}},  # Missing required fields
                identity=identity,
            )

        assert exc_info.value.error_code == "VALIDATION_ERROR"
        assert "Missing required AdCP parameters" in str(exc_info.value)

    async def test_errors_field_structure_from_validation_error(self, integration_db, handler):
        """Validation errors raise typed AdCPValidationError with structured suggestion.

        The ``suggestion`` attribute carries the required-parameters list so
        the envelope's ``errors[].suggestion`` field documents what the
        caller should retry with. Replaces the prior ``required_parameters``
        top-level field on the dict-return shape.
        """
        from src.core.exceptions import AdCPValidationError

        identity = ResolvedIdentity(
            principal_id="test_principal",
            tenant_id="test_tenant",
            tenant={"tenant_id": "test_tenant"},
            auth_token="test_token",
            protocol="a2a",
        )

        with pytest.raises(AdCPValidationError) as exc_info:
            await handler._handle_create_media_buy_skill(
                parameters={
                    "brand": {"domain": "testbrand.com"},
                    # Missing: packages, budget, start_time, end_time
                },
                identity=identity,
            )

        assert exc_info.value.error_code == "VALIDATION_ERROR"
        # The required-parameters list is carried in suggestion so buyers see
        # what to add on retry through the envelope's errors[].suggestion field.
        assert "packages" in (exc_info.value.suggestion or "")

    async def test_adcp_error_carries_recovery_through_a2a_boundary(self, integration_db, handler):
        """AdCPError propagates from _handle_explicit_skill with recovery preserved.

        Post-B4 contract: _handle_explicit_skill no longer translates AdCPError
        to a JSON-RPC A2AError. The typed exception propagates so the outer
        dispatcher loop can wrap the two-layer envelope into a failed Task's
        artifact DataPart. The envelope built from the exception carries
        ``recovery`` from the AdCPError class default (or override).
        """
        from unittest.mock import patch

        from src.core.exceptions import (
            AdCPAdapterError,
            AdCPValidationError,
            build_two_layer_error_envelope,
        )

        # Test transient recovery (AdCPAdapterError)
        async def mock_adapter_fail(params, token):
            raise AdCPAdapterError("GAM timeout")

        with patch.object(handler, "_handle_get_products_skill", mock_adapter_fail):
            with pytest.raises(AdCPAdapterError) as exc_info:
                await handler._handle_explicit_skill("get_products", {}, "token")

            error = exc_info.value
            assert error.recovery == "transient", "AdCPAdapterError must have transient recovery"
            # Envelope built from the propagated exception preserves recovery.
            envelope = build_two_layer_error_envelope(error)
            assert envelope["adcp_error"]["recovery"] == "transient"
            assert envelope["errors"][0]["recovery"] == "transient"

        # Test correctable recovery (AdCPValidationError)
        async def mock_validation_fail(params, token):
            raise AdCPValidationError("invalid brief")

        with patch.object(handler, "_handle_get_products_skill", mock_validation_fail):
            with pytest.raises(AdCPValidationError) as exc_info:
                await handler._handle_explicit_skill("get_products", {}, "token")

            error = exc_info.value
            assert error.recovery == "correctable", "AdCPValidationError must have correctable recovery"
            envelope = build_two_layer_error_envelope(error)
            assert envelope["adcp_error"]["recovery"] == "correctable"

    async def test_custom_recovery_override_preserved_through_a2a(self, integration_db, handler):
        """Custom recovery= override on AdCPError is preserved when propagating.

        Post-B4 contract: a raise-site override on ``recovery`` survives the
        propagation through ``_handle_explicit_skill`` and round-trips through
        the two-layer envelope builder unchanged.
        """
        from unittest.mock import patch

        from src.core.exceptions import AdCPNotFoundError, build_two_layer_error_envelope

        async def mock_transient_not_found(params, token):
            raise AdCPNotFoundError("temporarily gone", recovery="transient")

        with patch.object(handler, "_handle_get_products_skill", mock_transient_not_found):
            with pytest.raises(AdCPNotFoundError) as exc_info:
                await handler._handle_explicit_skill("get_products", {}, "token")

            error = exc_info.value
            assert (
                error.recovery == "transient"
            ), "Custom recovery='transient' override must be preserved, not default 'terminal'"
            envelope = build_two_layer_error_envelope(error)
            assert envelope["adcp_error"]["recovery"] == "transient"

    async def test_valueerror_wraps_to_adcp_validation_error(self, integration_db, handler):
        """ValueError in a skill handler propagates as AdCPValidationError.

        Post-R3 fix: ``_handle_explicit_skill`` no longer translates ValueError
        to a JSON-RPC ``InvalidParamsError``. It wraps the ValueError as a
        synthetic ``AdCPValidationError`` and re-raises, so the outer dispatcher
        catches it via ``except AdCPError`` and produces a failed Task with a
        two-layer envelope — same wire shape as natively-raised AdCPErrors.
        """
        from unittest.mock import patch

        from src.core.exceptions import AdCPValidationError

        async def mock_valueerror(params, identity):
            raise ValueError("missing required field")

        with patch.object(handler, "_handle_get_products_skill", mock_valueerror):
            with pytest.raises(AdCPValidationError) as exc_info:
                await handler._handle_explicit_skill("get_products", {}, None)

            assert "missing required field" in str(exc_info.value)
            assert exc_info.value.error_code == "VALIDATION_ERROR"
            # AdCPValidationError class default — preserved through the wrap.
            assert exc_info.value.recovery == "correctable"
            # Original ValueError is chained via __cause__ for traceability.
            assert isinstance(exc_info.value.__cause__, ValueError)

    async def test_permissionerror_wraps_to_adcp_authorization_error(self, integration_db, handler):
        """PermissionError in a skill handler propagates as AdCPAuthorizationError.

        Symmetric with ValueError handling. Outer dispatcher produces a failed
        Task with envelope (AUTH_REQUIRED, recovery=terminal).
        """
        from unittest.mock import patch

        from src.core.exceptions import AdCPAuthorizationError

        async def mock_permerror(params, identity):
            raise PermissionError("tenant scope mismatch")

        with patch.object(handler, "_handle_get_products_skill", mock_permerror):
            with pytest.raises(AdCPAuthorizationError) as exc_info:
                await handler._handle_explicit_skill("get_products", {}, None)

            assert "tenant scope mismatch" in str(exc_info.value)
            assert exc_info.value.error_code == "AUTH_REQUIRED"
            assert isinstance(exc_info.value.__cause__, PermissionError)

    async def test_untyped_exception_falls_through_to_dispatcher(self, integration_db, handler):
        """Untyped exceptions from a skill handler are no longer caught locally.

        Post-R3 fix: the ``except Exception`` catch-all in ``_handle_explicit_skill``
        was removed. Untyped exceptions propagate to the outer dispatcher's
        ``except Exception`` branch, which routes them through
        ``_build_failed_skill_result`` for uniform envelope shape — no double
        wrapping, no JSON-RPC translation.
        """
        from unittest.mock import patch

        async def mock_runtime_error(params, identity):
            raise RuntimeError("downstream service exploded")

        with patch.object(handler, "_handle_get_products_skill", mock_runtime_error):
            with pytest.raises(RuntimeError) as exc_info:
                await handler._handle_explicit_skill("get_products", {}, None)

            assert "downstream service exploded" in str(exc_info.value)


@pytest.mark.integration
@pytest.mark.requires_db
@pytest.mark.asyncio
class TestA2AContextEcho:
    """AdCPError.context echoes through to the A2A wire DataPart.

    Distinct from the validation-error coverage in TestA2AErrorPropagation:
    this class focuses on the context-correlation field round-tripping
    through the full on_message_send → dispatcher → envelope-builder pipeline.

    Konstantine review (PR #1306, 2026-05-24): "Send a request that triggers
    an error carrying context, assert `context` appears in the response JSON."
    """

    @pytest.fixture
    def handler(self):
        """Create A2A handler instance."""
        return AdCPRequestHandler()

    async def test_adcp_error_with_context_echoes_through_a2a_wire_envelope(self, integration_db, handler):
        """ContextObject set on AdCPError echoes through to the A2A wire DataPart.

        AdCPError carries an optional ``context`` (spec 3.0.6 normative) so buyer
        agents can correlate failures to the request that produced them.
        build_two_layer_error_envelope serializes it at envelope top-level (not
        inside errors[]). _build_failed_skill_result surfaces the envelope as the
        artifact DataPart, so the context dict must reach the wire intact.

        Mirrors test_adcp_error_carries_recovery_through_a2a_boundary (line 467)
        for the context-correlation field, exercising the full on_message_send
        pipeline to prove the wire shape — not just the envelope builder.
        """
        from unittest.mock import patch

        from adcp.types.generated_poc.core.context import ContextObject

        from src.core.exceptions import AdCPValidationError

        # Construct context with multiple correlation fields to verify dict-level echo.
        echoed_context = ContextObject(
            session_id="sess_pr1306_context_echo",
            workflow_step="echo_validation",
            request_id="req_abc_42",
        )

        async def raise_with_context(params, identity):
            # @_a2a_skill decorator passes AdCPError through unchanged so the
            # dispatcher catches it and builds the envelope.
            raise AdCPValidationError(
                "Synthetic validation error to verify context echo",
                context=echoed_context,
            )

        # Patch get_products handler — it's dispatcher-routable and discovery-skill
        # so we don't need a real principal in DB (auth is optional).
        with patch.object(handler, "_handle_get_products_skill", raise_with_context):
            # Auth/identity layer mocked at the boundary (matches gold-standard pattern).
            handler._get_auth_token = MagicMock(return_value=None)
            handler._resolve_a2a_identity = MagicMock(return_value=None)

            message = create_a2a_message_with_skill("get_products", {"brief": "test"})
            req_params = SendMessageRequest(message=message)

            result = await handler.on_message_send(req_params, ServerCallContext())

        # Must be a Task with artifacts — AdCPError flows through dispatcher (not A2AError).
        assert isinstance(result, Task)
        assert result.artifacts is not None and len(result.artifacts) > 0

        artifact_data = extract_data_from_artifact(result.artifacts[0])

        # Two-layer envelope present
        assert "adcp_error" in artifact_data
        assert artifact_data["adcp_error"]["code"] == "VALIDATION_ERROR"

        # CRITICAL: context echoes at top-level of the envelope (NOT nested under errors[]).
        assert "context" in artifact_data, (
            f"AdCPError(context=...) must echo through to wire envelope DataPart. "
            f"Got keys: {sorted(artifact_data.keys())}"
        )
        echoed = artifact_data["context"]
        assert (
            echoed.get("session_id") == "sess_pr1306_context_echo"
        ), f"session_id must round-trip unchanged, got: {echoed}"
        assert (
            echoed.get("workflow_step") == "echo_validation"
        ), f"workflow_step must round-trip unchanged, got: {echoed}"
        assert echoed.get("request_id") == "req_abc_42", f"request_id must round-trip unchanged, got: {echoed}"
