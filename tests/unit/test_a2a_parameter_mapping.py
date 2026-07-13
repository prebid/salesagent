#!/usr/bin/env python3
"""
Unit tests for A2A server parameter mapping to AdCP schemas.

These tests validate that the A2A server correctly extracts and passes
parameters from A2A requests to the core implementation functions,
ensuring parameter names match the AdCP specification.

CRITICAL: These tests catch protocol mismatches like 'updates' vs 'packages'
before they reach production.
"""

from unittest.mock import MagicMock, patch

import pytest
from adcp.types import AccountReference as LibraryAccountReference

from tests.factories.principal import PrincipalFactory
from tests.utils.a2a_helpers import assert_delivery_forwarded_account

_MOCK_IDENTITY = PrincipalFactory.make_identity(
    principal_id="principal_123",
    tenant_id="tenant_123",
    tenant={"tenant_id": "tenant_123"},
    protocol="a2a",
)


class TestA2AParameterMapping:
    """Test parameter extraction and mapping in A2A skill handlers."""

    def test_update_media_buy_uses_packages_parameter(self):
        """
        Test that update_media_buy skill handler extracts 'packages' parameter.

        Regression test for: A2A server expecting 'updates' instead of 'packages'

        The handler should:
        1. Accept 'packages' field from A2A request (per AdCP v2.0+)
        2. Pass 'packages' to core implementation (not 'updates')
        3. Support backward compatibility with legacy 'updates' field
        """
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()

        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY),
            patch("src.a2a_server.adcp_a2a_server.core_update_media_buy_tool") as mock_update,
        ):
            mock_update.return_value = {"status": "success", "media_buy_id": "mb_123"}

            # Simulate A2A request with AdCP v2.0+ 'packages' field
            parameters = {
                "media_buy_id": "mb_123",
                "paused": False,  # adcp 2.12.0+: paused=False means resume
                "packages": [{"package_id": "pkg_1", "paused": False}],  # AdCP v2.12.0+ field name
                # Budget/pacing fields the skill handler must forward to the core tool
                # (#1544 parity fix — previously silently dropped on the A2A path).
                "currency": "USD",
                "pacing": "even",
                "daily_budget": 500.0,
            }

            # Call the skill handler (synchronous wrapper for async method)
            import asyncio

            result = asyncio.run(handler._handle_update_media_buy_skill(parameters=parameters, identity=_MOCK_IDENTITY))

            # Verify the core function was called with correct parameter name
            mock_update.assert_called_once()
            call_kwargs = mock_update.call_args.kwargs

            # CRITICAL: Must pass 'packages' parameter (not 'updates')
            assert "packages" in call_kwargs, "Core function should be called with 'packages' parameter (AdCP v2.0+)"

            # Verify packages data is passed through (may have additional fields from Pydantic serialization)
            assert len(call_kwargs["packages"]) == len(parameters["packages"]), "Package count should match"
            msg = "Package ID should match"
            assert call_kwargs["packages"][0]["package_id"] == parameters["packages"][0]["package_id"], msg

            # Should NOT use legacy 'updates' parameter
            assert "updates" not in call_kwargs, "Should not pass legacy 'updates' parameter to core function"

            # Verify other AdCP v2.12.0+ parameters are passed
            assert call_kwargs["media_buy_id"] == "mb_123"
            assert call_kwargs["paused"] is False  # adcp 2.12.0+: paused=False means resume

            # Budget/pacing parity (#1544): these three must reach the core tool.
            # Removing any of the handler's params.get(...) forwards makes the key
            # absent here, so this pins the plumbing that otherwise reverts green.
            assert call_kwargs["currency"] == "USD"
            assert call_kwargs["pacing"] == "even"
            assert call_kwargs["daily_budget"] == 500.0

    def test_update_media_buy_invalid_revision_emits_invalid_request(self):
        """A schema-invalid revision emits INVALID_REQUEST on the A2A skill path,
        matching MCP/REST — not the boundary's VALIDATION_ERROR.

        Regression for the transport-parity divergence (#1544): the skill handler
        used to validate ``revision`` inside ``adcp_validation_boundary`` (→
        VALIDATION_ERROR); it now defers the raw value to the shared translator
        (``invalid_update_request_error`` → INVALID_REQUEST).

        Drives the REAL boundary — ``on_message_send`` → skill dispatch → failed
        Task — and asserts on the wire error envelope in the Task's artifact
        DataPart, not a reconstructed Python exception. This is what pins the
        dispatcher + envelope-builder against regression (per tests/CLAUDE.md error
        policy); a probe of the private skill handler would miss those layers.
        Validation fails before any DB access, so no adapter/session mock is needed.
        """
        import asyncio

        from a2a.types import SendMessageRequest

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from tests.a2a_helpers import make_a2a_context
        from tests.helpers import assert_envelope_shape
        from tests.utils.a2a_helpers import create_a2a_message_with_skill, extract_data_from_artifact

        handler = AdCPRequestHandler()
        handler._get_auth_token = MagicMock(return_value="test-token")
        ctx = make_a2a_context(auth_token="test-token", headers={"host": "test.example.com"})
        message = create_a2a_message_with_skill(
            "update_media_buy",
            {"media_buy_id": "mb_123", "revision": 0},  # below schema minimum of 1
        )
        params = SendMessageRequest(message=message)

        with patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY):
            result = asyncio.run(handler.on_message_send(params, context=ctx))

        assert result.artifacts, "failed skill must still return a Task with an artifact"
        envelope = extract_data_from_artifact(result.artifacts[0])
        # Assert on the actual wire envelope the buyer receives, not exc.error_code.
        assert_envelope_shape(envelope, "INVALID_REQUEST", recovery="correctable")

    def test_update_media_buy_backward_compatibility_with_updates(self):
        """
        Test backward compatibility with legacy 'updates' field.

        Some older clients might still send 'updates' wrapper.
        We should support this for backward compatibility but extract
        the 'packages' data from within it.
        """
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()

        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY),
            patch("src.a2a_server.adcp_a2a_server.core_update_media_buy_tool") as mock_update,
        ):
            mock_update.return_value = {"status": "success"}

            # Legacy request format with 'updates' wrapper
            parameters = {
                "media_buy_id": "mb_123",
                "updates": {
                    "packages": [{"package_id": "pkg_1", "budget": 5000.0, "status": "active"}]
                },  # Legacy wrapper
            }

            import asyncio

            result = asyncio.run(handler._handle_update_media_buy_skill(parameters=parameters, identity=_MOCK_IDENTITY))

            # Should extract packages from legacy 'updates' wrapper
            mock_update.assert_called_once()
            call_kwargs = mock_update.call_args.kwargs

            # Verify packages were extracted from legacy 'updates' wrapper
            assert "packages" in call_kwargs, "Should have packages parameter"
            assert len(call_kwargs["packages"]) == 1, "Should have extracted 1 package"
            assert call_kwargs["packages"][0]["package_id"] == "pkg_1", "Package ID should match"

    def test_update_media_buy_validates_required_parameters(self):
        """
        Test that update_media_buy validates required parameters per AdCP spec.

        Per AdCP oneOf constraint: requires either 'media_buy_id' OR 'buyer_ref'
        """
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()

        with patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY):
            # Request with neither media_buy_id nor buyer_ref
            invalid_parameters = {"active": True, "packages": []}

            import asyncio

            # Skill handlers raise typed AdCPValidationError on missing params so the
            # dispatcher routes through the two-layer envelope (not a JSON-RPC error).
            from src.core.exceptions import AdCPValidationError

            with pytest.raises(AdCPValidationError) as exc_info:
                asyncio.run(
                    handler._handle_update_media_buy_skill(parameters=invalid_parameters, identity=_MOCK_IDENTITY)
                )

            # Error message should mention required parameter
            error_message = str(exc_info.value).lower()
            msg = "Error message should mention required parameter"
            assert "media_buy_id" in error_message or "buyer_ref" in error_message, msg

    def test_get_media_buy_delivery_uses_plural_media_buy_ids(self):
        """
        Test that get_media_buy_delivery uses 'media_buy_ids' (plural).

        AdCP spec uses plural 'media_buy_ids' for array parameter.
        """
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()

        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY),
            patch("src.a2a_server.adcp_a2a_server.core_get_media_buy_delivery_tool") as mock_delivery,
        ):
            mock_delivery.return_value = {"media_buys": []}

            # AdCP request with plural 'media_buy_ids'
            parameters = {"media_buy_ids": ["mb_1", "mb_2", "mb_3"]}

            import asyncio

            result = asyncio.run(
                handler._handle_get_media_buy_delivery_skill(parameters=parameters, identity=_MOCK_IDENTITY)
            )

            # Verify core function was called with correct parameter
            mock_delivery.assert_called_once()
            call_kwargs = mock_delivery.call_args.kwargs

            # Should use plural 'media_buy_ids' per AdCP spec
            assert "media_buy_ids" in call_kwargs, "Should pass 'media_buy_ids' (plural) per AdCP spec"
            assert call_kwargs["media_buy_ids"] == parameters["media_buy_ids"]

    def test_get_media_buy_delivery_optional_media_buy_ids(self):
        """
        Test that get_media_buy_delivery works without media_buy_ids.

        Per AdCP spec, all parameters are optional. When media_buy_ids is omitted,
        the server should return delivery data for all media buys the requester
        has access to, filtered by the provided criteria (status_filter, dates, etc).
        """
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()

        with (
            patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY),
            patch("src.a2a_server.adcp_a2a_server.core_get_media_buy_delivery_tool") as mock_delivery,
        ):
            mock_delivery.return_value = {"media_buys": []}

            # AdCP request with filters but no media_buy_ids
            parameters = {
                "status_filter": "active",
                "start_date": "2025-01-01",
                "end_date": "2025-01-31",
            }

            import asyncio

            result = asyncio.run(
                handler._handle_get_media_buy_delivery_skill(parameters=parameters, identity=_MOCK_IDENTITY)
            )

            # Verify core function was called with filters
            mock_delivery.assert_called_once()
            call_kwargs = mock_delivery.call_args.kwargs

            # Should pass None for media_buy_ids and include filters
            assert call_kwargs["media_buy_ids"] is None, "media_buy_ids should be None when omitted"
            assert call_kwargs["status_filter"] == "active", "Should pass status_filter"
            assert call_kwargs["start_date"] == "2025-01-01", "Should pass start_date"
            assert call_kwargs["end_date"] == "2025-01-31", "Should pass end_date"

    def test_get_media_buy_delivery_forwards_typed_account_reference(self):
        """A2A get_media_buy_delivery must pass the validated account model."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()

        with patch("src.a2a_server.adcp_a2a_server.core_get_media_buy_delivery_tool") as mock_delivery:
            mock_delivery.return_value = {"media_buys": []}

            parameters = {"account": {"account_id": "acct-1"}}

            import asyncio

            asyncio.run(handler._handle_get_media_buy_delivery_skill(parameters=parameters, identity=_MOCK_IDENTITY))

            expected = LibraryAccountReference.model_validate({"account_id": "acct-1"})

            assert_delivery_forwarded_account(mock_delivery, expected)

    def test_get_media_buy_delivery_forwards_natural_key_account_reference(self):
        """A2A get_media_buy_delivery forwards the validated {brand, operator} account form.

        Complements the {account_id} case above by pinning the natural-key
        AccountReference variant — the form the delivery conformance storyboard
        sends — whose nested brand exercises its own coercion path.
        """
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()

        with patch("src.a2a_server.adcp_a2a_server.core_get_media_buy_delivery_tool") as mock_delivery:
            mock_delivery.return_value = {"media_buys": []}

            account = {"brand": {"domain": "acmeoutdoor.example"}, "operator": "pinnacle-agency.example"}
            parameters = {"account": account}

            import asyncio

            asyncio.run(handler._handle_get_media_buy_delivery_skill(parameters=parameters, identity=_MOCK_IDENTITY))

            expected = LibraryAccountReference.model_validate(account)

            assert_delivery_forwarded_account(mock_delivery, expected)

    def test_get_media_buy_delivery_rejects_malformed_account(self):
        """Malformed account should fail validation and not call the core tool."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from src.core.exceptions import AdCPValidationError

        handler = AdCPRequestHandler()

        with patch("src.a2a_server.adcp_a2a_server.core_get_media_buy_delivery_tool") as mock_delivery:
            parameters = {"account": {}}

            import asyncio

            with pytest.raises(AdCPValidationError):
                asyncio.run(
                    handler._handle_get_media_buy_delivery_skill(
                        parameters=parameters,
                        identity=_MOCK_IDENTITY,
                    )
                )

            mock_delivery.assert_not_called()

    def test_create_media_buy_validates_required_adcp_parameters(self):
        """
        Test that create_media_buy validates required AdCP parameters.

        The handler should reject requests missing required fields per AdCP spec.
        """
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()

        with patch("src.core.resolved_identity.resolve_identity", return_value=_MOCK_IDENTITY):
            # Request missing required AdCP parameters
            incomplete_parameters = {
                "buyer_ref": "campaign_123",
                # Missing: brand, packages, start_time, end_time
            }

            import asyncio

            # Skill handlers raise typed AdCPValidationError on missing-params; the
            # outer dispatcher catches AdCPError and routes through
            # _build_failed_skill_result to produce the two-layer envelope.
            # Asserting on the raised exception (not a returned dict) verifies the
            # flat-dict bypass path is closed — handlers must raise, never return
            # {"success": False, ...} that bypasses envelope construction.
            from src.core.exceptions import AdCPValidationError

            with pytest.raises(AdCPValidationError) as exc_info:
                asyncio.run(
                    handler._handle_create_media_buy_skill(parameters=incomplete_parameters, identity=_MOCK_IDENTITY)
                )

            error_message = str(exc_info.value).lower()
            assert "brand" in error_message, "Error message should mention missing 'brand'"
            assert "packages" in error_message, "Error message should mention missing 'packages'"
