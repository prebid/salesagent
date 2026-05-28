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

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.core.database.database_session import get_db_session
from tests.factories.principal import PrincipalFactory
from tests.helpers import assert_envelope_shape
from tests.helpers.adcp_factories import create_test_package_request_dict, setup_error_test_tenant_chain
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
        """Create test tenant + principal + product via the shared error-test chain helper.

        Note: human_review_required=False ensures media buy runs immediately
        rather than going to approval workflow (needed for response field tests).
        """
        from src.core.config_loader import set_current_tenant

        with get_db_session() as session:
            result = setup_error_test_tenant_chain(
                session,
                tenant_id="a2a_error_test",
                principal_id="a2a_error_principal",
                access_token="a2a_error_token_123",
                product_id="a2a_error_product",
                subdomain="a2aerror",
                tenant_name="A2A Error Test Tenant",
                advertiser_id="mock_adv_123",
            )
            set_current_tenant(result["tenant_dict"])
            yield result["tenant_dict"]

    @pytest.fixture
    def test_principal(self, integration_db, test_tenant):
        """Test principal info (created by the shared chain helper in test_tenant)."""
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
        identity = PrincipalFactory.make_identity(
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
        assert_envelope_shape(
            artifact_data,
            "VALIDATION_ERROR",
            message_substr="Missing required AdCP parameters",
        )

    async def test_create_media_buy_auth_error_includes_errors_field(self, handler, test_tenant):
        """Test that authentication errors include errors field in A2A response."""
        # Mock identity with non-existent principal — simulates resolved but invalid principal
        identity = PrincipalFactory.make_identity(
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
        # AdCPAuthorizationError producing the two-layer envelope. The
        # advisory pattern is documented in
        # test_media_buy.py::test_principal_not_found_returns_error_response
        # and is allowlist-permanent per the error-emission design decisions.
        assert "errors" in artifact_data, "Response must include 'errors' field for auth errors"
        assert len(artifact_data["errors"]) > 0, "errors array must not be empty"

        # Verify error is about authentication
        error = artifact_data["errors"][0]
        assert "code" in error, "Error must include code"
        assert error["code"] == "AUTH_REQUIRED"

        # Pin the envelope-level side: Pattern A advisory-on-success
        # responses do NOT carry the ``adcp_error`` envelope key (no
        # AdCPError was raised — the impl returned errors[] directly).
        # Asserting absence guards against a future regression that
        # accidentally wraps this advisory in a two-layer envelope (which
        # would change the wire shape for an allowlist-permanent site).
        assert "adcp_error" not in artifact_data, (
            "Pattern A advisory site emits errors[] only; the envelope-level "
            "adcp_error key is reserved for raised-AdCPError two-layer envelopes"
        )

    async def test_create_media_buy_success_has_no_errors_field(self, handler, test_tenant, test_principal):
        """Test that successful responses don't have errors field (or it's None/empty)."""
        identity = PrincipalFactory.make_identity(
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

        Exercises the full A2A transport pipeline end-to-end: real
        on_message_send → real handler raises AdCPValidationError →
        dispatcher routes through _build_failed_skill_result → wire envelope
        lands in DataPart. Mock-only equivalents do not prove the wiring.
        """
        identity = PrincipalFactory.make_identity(
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
        assert_envelope_shape(
            artifact_data,
            "VALIDATION_ERROR",
            message_substr="creatives",
        )

    async def test_create_media_buy_response_includes_all_adcp_fields(self, handler, test_tenant, test_principal):
        """Test that A2A response includes all AdCP domain fields (not just cherry-picked ones).

        Per AdCP v2.4 spec and PR #113:
        - Domain responses contain ONLY domain fields (media_buy_id, packages, errors)
        - Protocol fields (status, message, task_id, context_id) are added by ProtocolEnvelope wrapper
        - adcp_version is NOT included in individual responses (indicated by schema URL path)

        This test verifies that all domain fields from CreateMediaBuyResponse schema are preserved
        when wrapped by the A2A handler.
        """
        identity = PrincipalFactory.make_identity(
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

        Mirrors ``test_create_media_buy_validation_error_includes_errors_field``
        as the reference pattern for A2A wire-envelope verification.
        """
        identity = PrincipalFactory.make_identity(
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
        assert_envelope_shape(artifact_data, "VALIDATION_ERROR")
        # Per-error message enumerates the missing required fields.
        msg = artifact_data["errors"][0]["message"]
        assert (
            "format_id" in msg and "content_uri" in msg and "name" in msg
        ), f"Per-error message must name all missing required fields, got: {msg}"

    async def test_assign_creative_missing_required_params_wire_envelope(self, handler, test_tenant, test_principal):
        """assign_creative missing required params → two-layer envelope on the A2A wire.

        Required params per src/a2a_server/adcp_a2a_server.py:1783: media_buy_id,
        package_id, creative_id. Sending only media_buy_id triggers the missing-
        params check BEFORE the unimplemented TODO branch.

        Mirrors ``test_create_media_buy_validation_error_includes_errors_field``
        as the reference pattern for A2A wire-envelope verification.
        """
        identity = PrincipalFactory.make_identity(
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

        assert_envelope_shape(artifact_data, "VALIDATION_ERROR")
        # Per-error message enumerates ONLY the missing fields (not the provided media_buy_id).
        msg = artifact_data["errors"][0]["message"]
        assert "package_id" in msg and "creative_id" in msg, f"Per-error message must name missing fields, got: {msg}"

    async def test_update_media_buy_not_found_wire_envelope(self, handler, test_tenant, test_principal):
        """update_media_buy with unknown media_buy_id surfaces MEDIA_BUY_NOT_FOUND on the A2A wire.

        Drives ``AdCPMediaBuyNotFoundError`` raised inside ``_verify_principal``
        through the real ``on_message_send`` pipeline:
            update_media_buy_raw → _update_media_buy_impl → _verify_principal
                → repo.get_by_id returns None
                → raise AdCPMediaBuyNotFoundError
                → audit_step_failure re-raises (step is None at this point)
                → A2A dispatcher's _handle_explicit_skill catches the typed AdCPError
                → _build_failed_skill_result builds the two-layer envelope
                → DataPart on a failed Task

        MEDIA_BUY_NOT_FOUND is a STANDARD_ERROR_CODES entry (passthrough — not
        rewritten by ERROR_CODE_MAPPING) so the wire code matches the source
        exception's ``error_code``.
        """
        identity = PrincipalFactory.make_identity(
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

        # Send paused=True so _update_media_buy_impl has ≥1 updatable field and
        # reaches _verify_principal where the lookup fires the typed exception.
        skill_params = {"media_buy_id": "mb_does_not_exist_a2a_wire", "paused": True}
        message = self.create_message_with_skill("update_media_buy", skill_params)
        params = SendMessageRequest(message=message)

        result = await handler.on_message_send(params, ServerCallContext())

        assert isinstance(result, Task)
        assert result.artifacts is not None
        assert len(result.artifacts) > 0

        artifact_data = self.extract_data_from_artifact(result.artifacts[0])

        # Full two-layer envelope on the wire — typed-subclass code passes through.
        assert_envelope_shape(
            artifact_data,
            "MEDIA_BUY_NOT_FOUND",
            recovery="correctable",
            message_substr="mb_does_not_exist_a2a_wire",
        )


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

        Handlers raise rather than return custom error dicts so the outer
        dispatcher's ``_build_failed_skill_result`` produces the
        spec-compliant two-layer envelope. This test pins the contract at
        the skill-handler layer.
        """
        from src.core.exceptions import AdCPValidationError

        identity = PrincipalFactory.make_identity(
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

        identity = PrincipalFactory.make_identity(
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

        ``_handle_explicit_skill`` does not translate AdCPError into a
        JSON-RPC A2AError — the typed exception propagates with ``recovery``
        intact (from the class default or a raise-site override), and the
        two-layer envelope builder echoes it onto both layers. The end-to-end
        wire path — the dispatcher wrapping that envelope into a failed Task's
        artifact DataPart — is exercised by the ``on_message_send``-driven
        ``*_wire_envelope`` tests in this module.
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

        A raise-site override on ``recovery`` survives propagation through
        ``_handle_explicit_skill`` and round-trips through the two-layer
        envelope builder unchanged.
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

        ``_handle_explicit_skill`` wraps the ValueError as a synthetic
        ``AdCPValidationError`` and re-raises, so the outer dispatcher
        catches it via ``except AdCPError`` and produces a failed Task
        with a two-layer envelope — same wire shape as natively-raised
        AdCPErrors. No JSON-RPC ``InvalidParamsError`` translation.
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

        ``_handle_explicit_skill`` has no local ``except Exception``
        catch-all — untyped exceptions propagate to the outer dispatcher's
        ``except Exception`` branch, which routes them through
        ``_build_failed_skill_result`` for uniform envelope shape. No
        double wrapping, no JSON-RPC translation.
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

    Pins that a request triggering an error carrying ``context`` produces
    a wire envelope where the ``context`` field round-trips into the
    response JSON.
    """

    @pytest.fixture
    def handler(self):
        """Create A2A handler instance."""
        return AdCPRequestHandler()

    async def test_adcp_error_with_context_echoes_through_a2a_wire_envelope(self, integration_db, handler):
        """ContextObject set on AdCPError echoes through to the A2A wire DataPart.

        AdCPError carries an optional ``context`` (spec 3.0.0 normative) so buyer
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
            session_id="sess_a2a_context_echo",
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
        assert_envelope_shape(artifact_data, "VALIDATION_ERROR")

        # CRITICAL: context echoes at top-level of the envelope (NOT nested under errors[]).
        assert "context" in artifact_data, (
            f"AdCPError(context=...) must echo through to wire envelope DataPart. "
            f"Got keys: {sorted(artifact_data.keys())}"
        )
        echoed = artifact_data["context"]
        assert (
            echoed.get("session_id") == "sess_a2a_context_echo"
        ), f"session_id must round-trip unchanged, got: {echoed}"
        assert (
            echoed.get("workflow_step") == "echo_validation"
        ), f"workflow_step must round-trip unchanged, got: {echoed}"
        assert echoed.get("request_id") == "req_abc_42", f"request_id must round-trip unchanged, got: {echoed}"
