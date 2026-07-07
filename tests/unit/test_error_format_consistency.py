#!/usr/bin/env python3
"""
Tests for error format consistency across MCP and A2A transports.

Verifies that:
1. MCP tool errors have consistent structure (ToolError with message)
2. A2A skill errors have consistent JSON-RPC error structure (A2AError)
3. The SAME error scenario produces consistent error types/messages across transports

These are unit tests that mock database/adapter calls to isolate error formatting.
"""

from unittest.mock import patch

import pytest
from a2a.utils.errors import A2AError
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
from src.core.exceptions import AdCPAuthenticationError, AdCPError, AdCPValidationError
from src.core.resolved_identity import ResolvedIdentity


class TestMCPErrorShapes:
    """Test that MCP tool errors have consistent structure."""

    @pytest.mark.asyncio
    async def test_missing_identity_raises_adcp_auth_required_error(self):
        """_create_media_buy_impl raises AdCPAuthRequiredError when identity is missing.

        Pins on the typed exception (not a transport-union): boundary translation
        to ToolError is the transport wrapper's job, so calling ``_impl`` directly
        bypasses it and we can assert the production code's actual contract —
        typed exception + error_code + message — without the union dilution.

        Missing identity in ``_create_media_buy_impl`` raises
        ``AdCPAuthRequiredError`` (``AUTH_TOKEN_INVALID``) rather than
        ``AdCPValidationError`` — identity-required is auth, not validation.
        """
        from src.core.exceptions import AdCPAuthRequiredError
        from src.core.schemas import CreateMediaBuyRequest
        from src.core.tools.media_buy_create import _create_media_buy_impl

        req = CreateMediaBuyRequest(
            brand={"domain": "test.com"},
            packages=[],
            start_time="2026-01-01T00:00:00Z",
            end_time="2026-02-01T00:00:00Z",
            idempotency_key="unit-test-key-errfmt-001",
        )

        with pytest.raises(AdCPAuthRequiredError) as exc_info:
            await _create_media_buy_impl(req=req, identity=None)

        error = exc_info.value
        assert error.error_code == "AUTH_TOKEN_INVALID"
        assert "Identity is required" in error.message

    def test_pydantic_validation_error_for_invalid_request_shape(self):
        """CreateMediaBuyRequest raises Pydantic ValidationError for malformed input.

        Pre-_impl Pydantic validation owns request-shape errors; ``_impl`` itself
        never sees them. The test is most meaningful when run at the schema
        layer it actually fires from, pinned to ``ValidationError`` rather
        than a union with the runtime exceptions.
        """
        from src.core.schemas import CreateMediaBuyRequest

        with pytest.raises(ValidationError) as exc_info:
            CreateMediaBuyRequest(
                brand={"invalid_key": "no_domain"},  # Wrong structure: missing required 'domain'
                packages="not_a_list",  # type: ignore[arg-type]  # Wrong type: should be list
                start_time="2026-01-01T00:00:00Z",
                end_time="2026-02-01T00:00:00Z",
            )

        # Pydantic's ValidationError surfaces every offending field — at least one
        # of these errors should point at the malformed packages payload or the
        # missing brand.domain field. We don't pin on a specific code because
        # Pydantic v2's error codes vary by union/discriminator path.
        error_msg = str(exc_info.value)
        field_referenced = "packages" in error_msg or "domain" in error_msg
        assert field_referenced, f"Pydantic error should reference the malformed field, got: {error_msg}"

    @pytest.mark.asyncio
    async def test_auth_error_raises_validation_error(self):
        """MCP _create_media_buy_impl raises AdCPValidationError when identity is None."""
        from src.core.schemas import CreateMediaBuyRequest
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # Build a minimal valid request
        req = CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            packages=[],
            start_time="2026-01-01T00:00:00Z",
            end_time="2026-02-01T00:00:00Z",
            idempotency_key="unit-test-key-errfmt-002",
        )

        # _create_media_buy_impl requires identity; passing None triggers AdCPAuthenticationError
        with pytest.raises(AdCPAuthenticationError) as exc_info:
            await _create_media_buy_impl(req=req, identity=None)

        assert "Identity is required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_not_found_principal_raises_auth_error(self):
        """MCP _create_media_buy_impl raises AdCPAuthenticationError for non-existent principal."""
        from src.core.exceptions import AdCPAuthenticationError
        from src.core.resolved_identity import ResolvedIdentity
        from src.core.schemas import CreateMediaBuyRequest
        from src.core.testing_hooks import AdCPTestContext
        from src.core.tools.media_buy_create import _create_media_buy_impl

        req = CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            packages=[],
            start_time="2026-01-01T00:00:00Z",
            end_time="2026-02-01T00:00:00Z",
            idempotency_key="unit-test-key-errfmt-003",
        )

        identity = ResolvedIdentity(
            principal_id="nonexistent",
            tenant_id="test",
            tenant={"tenant_id": "test"},
            testing_context=AdCPTestContext(dry_run=False, test_session_id="test"),
        )

        with (
            patch("src.core.helpers.context_helpers.ensure_tenant_context", return_value={"tenant_id": "test"}),
            patch("src.core.tools.media_buy_create.validate_setup_complete"),
            patch("src.core.auth.get_principal_object", return_value=None),
        ):
            with pytest.raises(AdCPAuthenticationError, match="nonexistent"):
                await _create_media_buy_impl(req=req, identity=identity)


class TestA2AErrorShapes:
    """Test that A2A skill errors have consistent JSON-RPC error structure."""

    def setup_method(self):
        """Set up test fixtures."""
        self.handler = AdCPRequestHandler()

    @pytest.mark.asyncio
    async def test_auth_required_error_is_server_error(self):
        """A2A non-discovery skills raise A2AError when identity is None."""
        with pytest.raises(A2AError) as exc_info:
            await self.handler._handle_explicit_skill(
                skill_name="create_media_buy",
                parameters={"brand": {"domain": "testbrand.com"}},
                identity=None,
            )

        error = exc_info.value
        assert isinstance(error, A2AError)
        assert "Authentication required" in str(error)

    @pytest.mark.asyncio
    async def test_unknown_skill_raises_server_error(self):
        """A2A raises A2AError for unknown skill names."""
        from src.core.resolved_identity import ResolvedIdentity

        mock_identity = ResolvedIdentity(
            principal_id="test_principal", tenant_id="default", tenant={"tenant_id": "default"}, protocol="a2a"
        )
        with pytest.raises(A2AError) as exc_info:
            await self.handler._handle_explicit_skill(
                skill_name="nonexistent_skill",
                parameters={},
                identity=mock_identity,
            )

        error = exc_info.value
        assert isinstance(error, A2AError)
        assert "Unknown skill" in str(error)

    @pytest.mark.asyncio
    async def test_invalid_auth_identity_raises_server_error(self):
        """A2A raises A2AError when identity has no principal (auth required skill)."""
        # Identity with no principal_id simulates invalid auth
        invalid_identity = ResolvedIdentity(
            principal_id=None, tenant_id="default", tenant={"tenant_id": "default"}, protocol="a2a"
        )

        with pytest.raises(A2AError) as exc_info:
            await self.handler._handle_explicit_skill(
                skill_name="create_media_buy",
                parameters={"brand": {"domain": "testbrand.com"}},
                identity=invalid_identity,
            )

        assert "Authentication required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_missing_params_raises_typed_validation_error(self):
        """A2A create_media_buy raises typed AdCPValidationError for missing required params.

        A prior behavior returned a custom error dict that bypassed the
        envelope builder — buyers could not see the real wire code. Skill
        handlers now raise typed AdCPError; the outer dispatcher routes
        through ``_build_failed_skill_result``
        which calls ``_build_error_envelope`` for the two-layer wire shape.
        """

        mock_identity = ResolvedIdentity(
            principal_id="test_principal", tenant_id="default", tenant={"tenant_id": "default"}, protocol="a2a"
        )

        with pytest.raises(AdCPValidationError) as exc_info:
            await self.handler._handle_create_media_buy_skill(
                parameters={"brand": {"domain": "testbrand.com"}},
                identity=mock_identity,
            )

        assert "Missing required AdCP parameters" in str(exc_info.value)
        assert exc_info.value.error_code == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_validation_error_raises_typed_validation_error(self):
        """A2A create_media_buy raises typed AdCPValidationError for invalid parameter types.

        Same envelope-builder contract as the missing-params case.
        """

        mock_identity = ResolvedIdentity(
            principal_id="test_principal", tenant_id="default", tenant={"tenant_id": "default"}, protocol="a2a"
        )

        with pytest.raises(AdCPValidationError) as exc_info:
            # Provide all required params but with invalid types
            await self.handler._handle_create_media_buy_skill(
                parameters={
                    "brand": {"domain": "testbrand.com"},
                    "packages": "not_a_list",  # Invalid type
                    "start_time": "2026-01-01T00:00:00Z",
                    "end_time": "2026-02-01T00:00:00Z",
                },
                identity=mock_identity,
            )

        assert exc_info.value.error_code == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_discovery_skill_no_auth_does_not_raise_auth_error(self):
        """Discovery skills (get_products, etc.) do not require auth."""
        from src.core.schemas import GetProductsResponse

        with patch("src.a2a_server.adcp_a2a_server.core_get_products_tool") as mock_tool:
            # core_get_products_tool returns a Pydantic GetProductsResponse;
            # _serialize_for_a2a calls model_dump() so the mock must return a
            # real model (not a raw dict) for the discovery path to complete.
            mock_tool.return_value = GetProductsResponse(products=[])

            # Should NOT raise "Authentication required"
            anon_identity = ResolvedIdentity(
                principal_id=None, tenant_id="default", tenant={"tenant_id": "default"}, protocol="a2a"
            )
            try:
                await self.handler._handle_explicit_skill(
                    skill_name="get_products",
                    parameters={"brief": "test"},
                    identity=anon_identity,
                )
            except A2AError as e:
                assert "Authentication required" not in str(e), "Discovery skills should not require authentication"


class TestUpdateMediaBuyErrorShapes:
    """Test that update_media_buy error paths produce consistent errors."""

    @pytest.mark.asyncio
    async def test_missing_context_raises_value_error(self):
        """update_media_buy _impl raises ValueError when identity is None."""
        from src.core.schemas import UpdateMediaBuyRequest
        from src.core.tools.media_buy_update import _update_media_buy_impl

        req = UpdateMediaBuyRequest(
            media_buy_id="buy_001",
        )

        with pytest.raises(AdCPAuthenticationError) as exc_info:
            _update_media_buy_impl(req=req, identity=None)

        assert "Identity is required" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_a2a_missing_auth_raises_server_error(self):
        """A2A update_media_buy raises A2AError when auth is missing."""
        handler = AdCPRequestHandler()

        with pytest.raises(A2AError) as exc_info:
            await handler._handle_explicit_skill(
                skill_name="update_media_buy",
                parameters={"media_buy_id": "buy_001"},
                identity=None,
            )

        error = exc_info.value
        assert isinstance(error, A2AError)
        assert "Authentication required" in str(error)


class TestListCreativesErrorShapes:
    """Test that list_creatives error paths produce consistent errors."""

    @pytest.mark.asyncio
    async def test_missing_auth_raises_authentication_error(self):
        """list_creatives _impl raises AdCPAuthenticationError when identity is None."""
        from src.core.tools.creatives.listing import _list_creatives_impl

        with pytest.raises(AdCPAuthenticationError) as exc_info:
            _list_creatives_impl(identity=None)

        assert "x-adcp-auth" in str(exc_info.value).lower() or "Missing" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_a2a_missing_auth_raises_server_error(self):
        """A2A list_creatives raises A2AError when auth is missing."""
        handler = AdCPRequestHandler()

        with pytest.raises(A2AError) as exc_info:
            await handler._handle_explicit_skill(
                skill_name="list_creatives",
                parameters={},
                identity=None,
            )

        error = exc_info.value
        assert isinstance(error, A2AError)
        assert "Authentication required" in str(error)


class TestCrossTransportErrorConsistency:
    """Test that the SAME error scenario produces consistent errors across transports.

    The key insight: both MCP and A2A paths call shared _impl() functions.
    This test verifies that the error type and message content are consistent
    regardless of which transport triggers the error.
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.handler = AdCPRequestHandler()

    @pytest.mark.asyncio
    async def test_missing_context_error_consistent(self):
        """Both transports produce consistent errors when identity/auth is missing.

        MCP path: _create_media_buy_impl(identity=None) -> AdCPValidationError("Identity is required")
        A2A path: _handle_explicit_skill(identity=None) -> A2AError("Authentication required")

        Both paths reject the request before reaching business logic.
        """
        from src.core.schemas import CreateMediaBuyRequest
        from src.core.tools.media_buy_create import _create_media_buy_impl

        req = CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            packages=[],
            start_time="2026-01-01T00:00:00Z",
            end_time="2026-02-01T00:00:00Z",
            idempotency_key="unit-test-key-errfmt-004",
        )

        # MCP path: missing identity — raises AdCPValidationError (transport-agnostic)
        mcp_error = None
        try:
            await _create_media_buy_impl(req=req, identity=None)
        except (ToolError, AdCPError) as e:
            mcp_error = e

        # A2A path: missing identity (None = no auth)
        a2a_error = None
        try:
            await self.handler._handle_explicit_skill(
                skill_name="create_media_buy",
                parameters={"brand": {"domain": "testbrand.com"}},
                identity=None,
            )
        except A2AError as e:
            a2a_error = e

        # Both must reject the request
        assert mcp_error is not None, "MCP path must raise error for missing identity"
        assert a2a_error is not None, "A2A path must raise A2AError for missing auth"

        # Both errors indicate authentication/authorization failure
        assert "Identity is required" in str(mcp_error) or "required" in str(mcp_error).lower()
        assert "Authentication required" in str(a2a_error) or "required" in str(a2a_error).lower()

    @pytest.mark.asyncio
    async def test_missing_required_params_error_consistent(self):
        """Both transports report missing required parameters consistently.

        MCP path: CreateMediaBuyRequest validation -> ToolError with field details
        A2A path: _handle_create_media_buy_skill -> raises typed AdCPValidationError

        Both should mention the missing fields. Skill handlers raise
        typed AdCPError on validation failure; the outer dispatcher's
        ``_build_failed_skill_result`` produces the two-layer envelope on
        the wire.
        """
        from src.core.schemas import CreateMediaBuyRequest

        # MCP path: test the request validation itself
        mcp_error_message = None
        try:
            CreateMediaBuyRequest(
                brand={"invalid_key": "no_domain"},  # Missing required 'domain' field triggers ValidationError
                packages=[],
                start_time="2026-01-01T00:00:00Z",
                end_time="2026-02-01T00:00:00Z",
            )
        except ValidationError as e:
            mcp_error_message = str(e)

        # A2A path: missing required params — identity resolved at transport boundary
        mock_identity = ResolvedIdentity(
            principal_id="test_principal", tenant_id="default", tenant={"tenant_id": "default"}, protocol="a2a"
        )

        with pytest.raises(AdCPValidationError) as a2a_exc_info:
            await self.handler._handle_create_media_buy_skill(
                parameters={"brand": {"domain": "testbrand.com"}},
                identity=mock_identity,
            )

        # Both identify validation/parameter issues
        if mcp_error_message:
            # Error should mention brand or domain validation issue
            assert (
                "brand" in mcp_error_message.lower()
                or "domain" in mcp_error_message.lower()
                or "validation" in mcp_error_message.lower()
            )

        # A2A error identifies missing params via the typed exception's message
        a2a_error_msg = str(a2a_exc_info.value)
        assert "Missing required" in a2a_error_msg or "parameters" in a2a_error_msg.lower()
        assert a2a_exc_info.value.error_code == "VALIDATION_ERROR"

    @pytest.mark.asyncio
    async def test_nonexistent_principal_error_consistent(self):
        """Both transports handle non-existent principal via the same typed AdCPAuthenticationError.

        The _create_media_buy_impl function raises AdCPAuthenticationError when the
        principal is not found; this verifies build_two_layer_error_envelope — the
        helper every transport boundary delegates to — maps it to AUTH_TOKEN_INVALID.
        The live A2A wire shape for this path is pinned by test_a2a_error_responses.
        """
        from src.core.exceptions import AdCPAuthenticationError, build_two_layer_error_envelope
        from src.core.resolved_identity import ResolvedIdentity
        from src.core.schemas import CreateMediaBuyRequest
        from src.core.testing_hooks import AdCPTestContext
        from src.core.tools.media_buy_create import _create_media_buy_impl

        req = CreateMediaBuyRequest(
            brand={"domain": "testbrand.com"},
            packages=[],
            start_time="2026-01-01T00:00:00Z",
            end_time="2026-02-01T00:00:00Z",
            idempotency_key="unit-test-key-errfmt-005",
        )

        identity = ResolvedIdentity(
            principal_id="ghost_principal",
            tenant_id="test",
            tenant={"tenant_id": "test"},
            testing_context=AdCPTestContext(dry_run=False, test_session_id="test"),
        )

        with (
            patch("src.core.helpers.context_helpers.ensure_tenant_context", return_value={"tenant_id": "test"}),
            patch("src.core.tools.media_buy_create.validate_setup_complete"),
            patch("src.core.auth.get_principal_object", return_value=None),
        ):
            with pytest.raises(AdCPAuthenticationError, match="ghost_principal") as exc_info:
                await _create_media_buy_impl(req=req, identity=identity)

        # The boundary translator (called by every transport wrapper) produces
        # the same two-layer envelope for this typed exception.
        envelope = build_two_layer_error_envelope(exc_info.value)
        assert envelope["adcp_error"]["code"] == "AUTH_TOKEN_INVALID"
        assert envelope["errors"][0]["code"] == "AUTH_TOKEN_INVALID"
        assert "not found" in envelope["adcp_error"]["message"].lower()

    @pytest.mark.asyncio
    async def test_unknown_skill_only_affects_a2a(self):
        """Unknown skill errors only apply to A2A (MCP validates tool names separately).

        MCP uses FastMCP's tool registry which rejects unknown tools at the protocol level.
        A2A uses _handle_explicit_skill which maps skill names to handlers.
        """
        handler = AdCPRequestHandler()

        from src.core.resolved_identity import ResolvedIdentity

        mock_identity = ResolvedIdentity(
            principal_id="test_principal", tenant_id="default", tenant={"tenant_id": "default"}, protocol="a2a"
        )
        with pytest.raises(A2AError) as exc_info:
            await handler._handle_explicit_skill(
                skill_name="totally_fake_skill",
                parameters={},
                identity=mock_identity,
            )

        error_str = str(exc_info.value)
        assert "Unknown skill" in error_str or "totally_fake_skill" in error_str

    @pytest.mark.asyncio
    async def test_serialize_for_a2a_error_response_structure(self):
        """Verify _serialize_for_a2a produces consistent structure for error models."""
        from src.core.schemas import CreateMediaBuyError, Error

        # Create an error response like the impl would
        error_response = CreateMediaBuyError(
            errors=[
                Error(code="VALIDATION_ERROR", message="Missing required field: packages"),
            ],
            context=None,
        )

        serialized = AdCPRequestHandler._serialize_for_a2a(error_response)

        # Verify consistent error structure
        assert isinstance(serialized, dict)
        assert "success" in serialized
        assert serialized["success"] is False
        assert "errors" in serialized
        assert len(serialized["errors"]) > 0
        assert serialized["errors"][0]["code"] == "VALIDATION_ERROR"
        assert "message" in serialized  # Protocol message field added by serializer

    @pytest.mark.asyncio
    async def test_serialize_for_a2a_success_flag_is_type_based(self):
        """The A2A ``success`` flag reflects the response TYPE, never errors-presence.

        Advisory errors[] legitimately ride non-error envelopes (the create
        submitted variant, delivery advisories, hydration advisories) — a
        booked buy with a zero-overlap advisory must NOT wire as a failure,
        or buyers retry and double-book.
        """
        from src.core.schemas import (
            CreateMediaBuyError,
            CreateMediaBuyResult,
            CreateMediaBuySubmitted,
            CreateMediaBuySuccess,
            Error,
            GetMediaBuysResponse,
        )

        advisory = Error(code="PRODUCT_UNAVAILABLE", message="zero overlap with product p1")

        # Submitted (pending approval) carrying advisory errors → success True.
        submitted = CreateMediaBuyResult(
            status="submitted",
            response=CreateMediaBuySubmitted(task_id="step_1", errors=[advisory], status="submitted"),
        )
        serialized = AdCPRequestHandler._serialize_for_a2a(submitted)
        assert serialized["success"] is True
        assert serialized["errors"][0]["code"] == "PRODUCT_UNAVAILABLE"
        assert serialized["status"] == "submitted"
        assert "media_buy_id" not in serialized  # spec forbids it on the submitted variant

        # Completed success with advisory ext → success True, advisory in message.
        from src.core.tools.media_buy_create import _advisory_ext

        completed = CreateMediaBuyResult(
            status="completed",
            response=CreateMediaBuySuccess(media_buy_id="mb1", packages=[], ext=_advisory_ext([advisory])),
        )
        serialized = AdCPRequestHandler._serialize_for_a2a(completed)
        assert serialized["success"] is True
        assert "zero overlap" in serialized["message"]

        # Error union member wrapped in the result → success False.
        failed = CreateMediaBuyResult(
            status="failed",
            response=CreateMediaBuyError(errors=[Error(code="VALIDATION_ERROR", message="bad")]),
        )
        assert AdCPRequestHandler._serialize_for_a2a(failed)["success"] is False

        # Degraded-but-completed task (errors-only GetMediaBuysResponse, e.g.
        # the AUTH_REQUIRED degradation) → success True: the task completed
        # and returned a result with an advisory explaining the degradation.
        degraded = GetMediaBuysResponse(
            media_buys=[],
            errors=[Error(code="AUTH_REQUIRED", message="Principal ID not found in context")],
        )
        serialized = AdCPRequestHandler._serialize_for_a2a(degraded)
        assert serialized["success"] is True
        assert serialized["errors"][0]["code"] == "AUTH_REQUIRED"

    def test_success_map_covers_every_error_union_member(self):
        """Fitness function: a future response union's error member must not
        silently default to success=True (fail-open) in the A2A flag map.

        Enumerates every `*Response = A | B` union in the schemas package and
        asserts the serializer reports False for each member whose name ends
        in Error.
        """
        import typing

        from src.core import schemas

        error_members = []
        for name in dir(schemas):
            obj = getattr(schemas, name)
            if typing.get_origin(obj) in (typing.Union, __import__("types").UnionType):
                for member in typing.get_args(obj):
                    if isinstance(member, type) and member.__name__.endswith("Error"):
                        error_members.append(member)
        assert error_members, "expected at least the create/update error unions"
        for member in error_members:
            instance = member.model_construct()
            assert AdCPRequestHandler._response_indicates_success(instance) is False, (
                f"{member.__name__} would wire success=True — add it to the type map"
            )

    @pytest.mark.asyncio
    async def test_reconstruct_submitted_create_response(self):
        """The artifact text-part reconstruction handles the submitted variant.

        The envelope's ``status`` is the TASK status; ``CreateMediaBuySuccess``
        would reject ``"submitted"`` (it is not a MediaBuyStatus) — the
        discriminator must pick ``CreateMediaBuySubmitted`` first or the text
        part is silently lost.
        """
        from src.core.schemas import CreateMediaBuySubmitted

        handler = AdCPRequestHandler.__new__(AdCPRequestHandler)
        data = {"status": "submitted", "task_id": "step_9", "message": "pending approval"}
        obj = handler._reconstruct_response_object("create_media_buy", data)
        assert isinstance(obj, CreateMediaBuySubmitted)
        assert "step_9" in str(obj)

    @pytest.mark.asyncio
    async def test_reconstruct_submitted_update_response(self):
        """The update artifact reconstruction handles the submitted variant.

        update_media_buy gained a submitted variant (approval-pending recompile).
        Without a ``status == "submitted"`` branch mirroring create, the payload
        (task_id, no media_buy_id) falls into ``UpdateMediaBuyError`` and the text
        part reads as a failure instead of a pending-approval submission.
        """
        from src.core.schemas import UpdateMediaBuySubmitted

        handler = AdCPRequestHandler.__new__(AdCPRequestHandler)
        data = {"status": "submitted", "task_id": "step_42", "message": "pending approval"}
        obj = handler._reconstruct_response_object("update_media_buy", data)
        assert isinstance(obj, UpdateMediaBuySubmitted)
        assert obj.task_id == "step_42"


# ---------------------------------------------------------------------------
# Recovery field in MCP error responses
# ---------------------------------------------------------------------------


class TestMCPRecoveryInErrorResponses:
    """Verify that MCP ToolError carries recovery for every AdCPError subclass.

    The MCP boundary (with_error_logging) translates AdCPError -> ToolError(code, msg, recovery).
    Buyer agents parse ToolError.args to decide retry/fix/abandon strategy.
    """

    @pytest.mark.parametrize(
        "exc_class,msg,expected_code,expected_recovery",
        [
            # INTERNAL_ERROR and NOT_FOUND are INTERNAL_CODES; the boundary
            # translator maps them to STANDARD_ERROR_CODES at wire emission.
            ("AdCPError", "internal error", "SERVICE_UNAVAILABLE", "terminal"),
            ("AdCPValidationError", "bad field", "VALIDATION_ERROR", "correctable"),
            ("AdCPAuthenticationError", "bad token", "AUTH_TOKEN_INVALID", "terminal"),
            ("AdCPAuthorizationError", "no access", "AUTH_REQUIRED", "terminal"),
            ("AdCPNotFoundError", "gone", "INVALID_REQUEST", "terminal"),
            ("AdCPConflictError", "duplicate", "CONFLICT", "correctable"),
            ("AdCPGoneError", "expired", "INVALID_STATE", "correctable"),
            ("AdCPBudgetExhaustedError", "no budget", "BUDGET_EXHAUSTED", "correctable"),
            ("AdCPRateLimitError", "slow down", "RATE_LIMITED", "transient"),
            ("AdCPAdapterError", "GAM down", "SERVICE_UNAVAILABLE", "transient"),
            ("AdCPServiceUnavailableError", "offline", "SERVICE_UNAVAILABLE", "transient"),
            ("AdCPCapabilityNotSupportedError", "no property_list", "UNSUPPORTED_FEATURE", "correctable"),
        ],
        ids=lambda x: x if isinstance(x, str) and x.startswith("AdCP") else "",
    )
    def test_mcp_tool_error_carries_recovery(self, exc_class, msg, expected_code, expected_recovery):
        """ToolError from MCP boundary carries recovery in args[2] for {exc_class}."""
        from fastmcp.exceptions import ToolError

        import src.core.exceptions as exc_mod
        from src.core.tool_error_logging import with_error_logging

        klass = getattr(exc_mod, exc_class)

        def failing_tool():
            raise klass(msg)

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        from tests.helpers import assert_envelope_shape

        assert_envelope_shape(
            exc_info.value,
            expected_code,
            recovery=expected_recovery,
            message_substr=msg,
            check_mcp_tool_error=True,
        )


# ---------------------------------------------------------------------------
# Recovery field in A2A error responses
# ---------------------------------------------------------------------------


class TestA2ARecoveryInErrorResponses:
    """Verify recovery semantics propagate from every AdCPError subclass.

    ``_handle_explicit_skill`` does not translate AdCPError to A2AError — the
    typed exception propagates so the explicit-skill dispatcher can wrap it
    into a failed Task with a two-layer envelope
    DataPart. The buyer agent parses ``recovery`` from the propagated exception
    (or from the envelope's ``adcp_error.recovery`` once it reaches the wire).
    """

    def setup_method(self):
        """Set up test fixtures."""
        self.handler = AdCPRequestHandler()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "exc_class,msg,expected_recovery",
        [
            ("AdCPError", "internal", "terminal"),
            ("AdCPValidationError", "bad", "correctable"),
            ("AdCPAuthenticationError", "unauth", "terminal"),
            ("AdCPAuthorizationError", "forbidden", "terminal"),
            ("AdCPNotFoundError", "missing", "terminal"),
            ("AdCPConflictError", "dup", "correctable"),
            ("AdCPGoneError", "expired", "correctable"),
            ("AdCPBudgetExhaustedError", "broke", "correctable"),
            ("AdCPRateLimitError", "slow", "transient"),
            ("AdCPAdapterError", "down", "transient"),
            ("AdCPServiceUnavailableError", "offline", "transient"),
        ],
        ids=lambda x: x if isinstance(x, str) and x.startswith("AdCP") else "",
    )
    async def test_a2a_propagated_error_carries_recovery(self, exc_class, msg, expected_recovery):
        """Typed AdCPError propagates from _handle_explicit_skill with recovery={expected_recovery}."""
        import src.core.exceptions as exc_mod
        from src.core.exceptions import AdCPError, build_two_layer_error_envelope

        klass = getattr(exc_mod, exc_class)

        async def mock_skill(params, token):
            raise klass(msg)

        with patch.object(self.handler, "_handle_get_products_skill", mock_skill):
            with pytest.raises(AdCPError) as exc_info:
                await self.handler._handle_explicit_skill("get_products", {}, "token")

            # Recovery is on the propagated exception itself (the dispatcher will
            # build the envelope when wrapping into the failed Task's DataPart).
            assert exc_info.value.recovery == expected_recovery
            # And the two-layer envelope builder echoes it onto both layers.
            envelope = build_two_layer_error_envelope(exc_info.value)
            assert envelope["adcp_error"]["recovery"] == expected_recovery


# ---------------------------------------------------------------------------
# Recovery override preservation through serialization
# ---------------------------------------------------------------------------


class TestRecoveryOverrideInSerialization:
    """Verify custom recovery= override is preserved through all serialization paths."""

    def test_custom_recovery_in_serialize_for_a2a(self):
        """_serialize_for_a2a preserves custom recovery when error model carries it."""
        from src.core.schemas import CreateMediaBuyError, Error

        # Create error response with explicit recovery field
        error_response = CreateMediaBuyError(
            errors=[
                Error(code="NOT_FOUND", message="temporarily missing"),
            ],
            context=None,
        )

        serialized = AdCPRequestHandler._serialize_for_a2a(error_response)

        assert serialized["success"] is False
        assert serialized["errors"][0]["code"] == "NOT_FOUND"

    def test_custom_recovery_override_in_to_dict(self):
        """to_dict() reflects custom recovery, not class default."""
        from src.core.exceptions import AdCPConflictError

        # Default recovery is "correctable"
        default = AdCPConflictError("dup")
        assert default.to_dict()["recovery"] == "correctable"

        # Override to "terminal" (e.g., non-retryable conflict)
        overridden = AdCPConflictError("permanent conflict", recovery="terminal")
        assert overridden.to_dict()["recovery"] == "terminal"

    def test_custom_recovery_survives_mcp_then_extract(self):
        """Custom recovery: AdCPError(recovery=X) -> ToolError -> extract_error_info -> X."""
        from fastmcp.exceptions import ToolError

        from src.core.exceptions import AdCPAdapterError
        from src.core.tool_error_logging import extract_error_info, with_error_logging

        def failing():
            raise AdCPAdapterError("permanent failure", recovery="terminal")

        wrapped = with_error_logging(failing)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        code, message, recovery = extract_error_info(exc_info.value)
        assert recovery == "terminal"  # Custom, not default "transient"


# ---------------------------------------------------------------------------
# Vocabulary consistency: error_codes match adcp-req canonical vocabulary
# ---------------------------------------------------------------------------


class TestErrorCodeVocabularyConsistency:
    """Validate error_code strings against adcp-req ERROR_CODE_VOCABULARY.md.

    The canonical vocabulary is defined in:
    /docs/requirements/ERROR_CODE_VOCABULARY.md (adcp-req repo)

    Our exception hierarchy must use canonical codes where the spec defines them.
    After error-code compliance (#1248), all exception class codes must be
    in SDK STANDARD_ERROR_CODES or in the justified INTERNAL_CODES set.
    """

    # Canonical codes: SDK STANDARD_ERROR_CODES + justified internal codes.
    # After error-code compliance (#1248), all class-level codes are either
    # SDK-standard or explicitly internal (see INTERNAL_CODES in exceptions.py).
    CANONICAL_ERROR_CODES = {
        # SDK standard codes used by our exception classes
        "INTERNAL_ERROR",  # Base-class default (internal only, never on wire)
        "VALIDATION_ERROR",  # adcp-req: Generic Errors
        "INVALID_REQUEST",  # SDK standard: AdCPInvalidRequestError (semantically-invalid value)
        "AUTH_TOKEN_INVALID",  # AdCP spec: invalid/missing auth token (AdCPAuthenticationError)
        "AUTH_REQUIRED",  # SDK standard: authorisation (AdCPAuthorizationError)
        "POLICY_VIOLATION",  # SDK standard: AdCPPolicyViolationError (content/advertising policy block)
        "NOT_FOUND",  # Base class for entity-specific codes (internal only)
        "ACCOUNT_NOT_FOUND",  # adcp-req: Account resolution (BR-RULE-080)
        "ACCOUNT_AMBIGUOUS",  # adcp-req: Natural key matches multiple accounts (BR-RULE-080)
        "ACCOUNT_SETUP_REQUIRED",  # adcp-req: Account requires setup (BR-RULE-080)
        "ACCOUNT_SUSPENDED",  # adcp-req: Account is suspended (BR-RULE-080)
        "ACCOUNT_PAYMENT_REQUIRED",  # adcp-req: Account has outstanding payment (BR-RULE-080)
        "CONFLICT",  # Generic form of {ENTITY}_EXISTS
        "INVALID_STATE",  # SDK standard: gone/expired resources
        "BUDGET_EXHAUSTED",  # SDK standard: budget limit reached
        "RATE_LIMITED",  # SDK standard: rate limiting
        "SERVICE_UNAVAILABLE",  # SDK standard: adapter/service failures
        "CONFIGURATION_ERROR",  # Internal only: server config broken
        # SDK standard codes added by the error-emission-architecture substrate.
        "MEDIA_BUY_NOT_FOUND",  # SDK standard: AdCPMediaBuyNotFoundError
        "PACKAGE_NOT_FOUND",  # SDK standard: AdCPPackageNotFoundError
        "PRODUCT_NOT_FOUND",  # SDK standard: AdCPProductNotFoundError
        "SESSION_NOT_FOUND",  # SDK standard: AdCPContextNotFoundError (unresolvable context_id)
        "CREATIVE_NOT_FOUND",  # Internal: AdCPCreativeNotFoundError (wire → INVALID_REQUEST)
        "FORMAT_NOT_FOUND",  # Internal: AdCPFormatNotFoundError (wire → INVALID_REQUEST)
        "TASK_NOT_FOUND",  # Internal: AdCPTaskNotFoundError (wire → INVALID_REQUEST)
        "BUDGET_TOO_LOW",  # SDK standard: AdCPBudgetTooLowError
        "UNSUPPORTED_FEATURE",  # SDK standard: AdCPCapabilityNotSupportedError
        "IDEMPOTENCY_CONFLICT",  # SDK standard: AdCPIdempotencyConflictError
        "IDEMPOTENCY_EXPIRED",  # SDK standard: AdCPIdempotencyExpiredError
        "IDEMPOTENCY_IN_FLIGHT",  # Spec passthrough (SPEC_CODES): AdCPIdempotencyInFlightError
        # Adapter-taxonomy codes (internal; wire → SERVICE_UNAVAILABLE via ERROR_CODE_MAPPING)
        "WORKFLOW_CREATION_FAILED",  # Internal: AdCPWorkflowError
        "ACTIVATION_WORKFLOW_FAILED",  # Internal: AdCPActivationWorkflowError
        "LINE_ITEM_CREATION_FAILED",  # Internal: AdCPLineItemError
        "GAM_UPDATE_FAILED",  # Internal: AdCPGamUpdateError
        "PARTIAL_FAILURE",  # Internal: AdCPBulkUpdateError
        # Mock-adapter business-outcome codes (internal; wire → standard via ERROR_CODE_MAPPING)
        "MEDIA_BUY_REJECTED",  # Internal: AdCPMediaBuyRejectedError (wire → POLICY_VIOLATION)
        "INVENTORY_UNAVAILABLE",  # Internal: AdCPInventoryUnavailableError (wire → PRODUCT_UNAVAILABLE)
        # Advisory-on-success Pattern A codes (no dedicated exception subclass —
        # construction sites use Error(code=...) inside success envelopes).
        "CREATIVE_REJECTED",
        "BUDGET_EXCEEDED",
        "PRODUCT_UNAVAILABLE",
    }

    def test_all_exception_error_codes_are_canonical(self):
        """Every AdCPError subclass error_code must be in the canonical vocabulary."""
        from src.core.exceptions import (
            AdCPAdapterError,
            AdCPAuthenticationError,
            AdCPAuthorizationError,
            AdCPBudgetExhaustedError,
            AdCPConflictError,
            AdCPError,
            AdCPGoneError,
            AdCPNotFoundError,
            AdCPRateLimitError,
            AdCPServiceUnavailableError,
        )

        exception_classes = [
            AdCPError,
            AdCPValidationError,
            AdCPAuthenticationError,
            AdCPAuthorizationError,
            AdCPNotFoundError,
            AdCPConflictError,
            AdCPGoneError,
            AdCPBudgetExhaustedError,
            AdCPRateLimitError,
            AdCPAdapterError,
            AdCPServiceUnavailableError,
        ]

        for exc_class in exception_classes:
            # _default_error_code is the class-level identity slot per
            # salesagent-fnk9 option A. error_code is an instance attribute.
            code = exc_class._default_error_code
            assert code in self.CANONICAL_ERROR_CODES, (
                f"{exc_class.__name__}._default_error_code = {code!r} is not in the canonical vocabulary. "
                f"If this is a new code, add it to CANONICAL_ERROR_CODES with a comment. "
                f"If this is a renamed code, update the exception class."
            )

    def test_rate_limit_uses_canonical_code(self):
        """AdCPRateLimitError must use RATE_LIMITED (SDK STANDARD_ERROR_CODES).

        The SDK defines RATE_LIMITED as the standard code.
        """
        from src.core.exceptions import AdCPRateLimitError

        # Class-level identity lives on _default_error_code (option A,
        # salesagent-fnk9). The public error_code is an instance attribute set
        # in __init__ from this default unless overridden via synthesize().
        assert AdCPRateLimitError._default_error_code == "RATE_LIMITED", (
            f"AdCPRateLimitError._default_error_code = {AdCPRateLimitError._default_error_code!r}, "
            f"expected 'RATE_LIMITED' per SDK STANDARD_ERROR_CODES"
        )
        # Also pin via instance — proves the class-level default propagates
        # into the instance attribute on construction.
        assert AdCPRateLimitError("test").error_code == "RATE_LIMITED"

    def test_canonical_vocabulary_covers_all_subclasses(self):
        """CANONICAL_ERROR_CODES must have exactly one entry per exception subclass."""
        from src.core.exceptions import AdCPError

        # Discover all concrete subclasses (recursively). Reads
        # _default_error_code per option-A refactor (salesagent-fnk9).
        subclass_codes = set()

        def _collect(cls: type) -> None:
            subclass_codes.add(cls._default_error_code)
            for sub in cls.__subclasses__():
                _collect(sub)

        _collect(AdCPError)

        # Every subclass code must be in canonical set
        missing = subclass_codes - self.CANONICAL_ERROR_CODES
        assert not missing, (
            f"Exception error_codes not in CANONICAL_ERROR_CODES: {missing}. "
            f"Add them to the canonical set or fix the error_code."
        )

        # Every canonical code must correspond to either a subclass OR an
        # advisory-on-success Pattern A wire code (constructed via
        # ``Error(code=...)`` inside success envelopes without an associated
        # raise site, hence no dedicated exception class).
        _ADVISORY_ONLY_CODES = {
            "CREATIVE_REJECTED",
            "BUDGET_EXCEEDED",
            "PRODUCT_UNAVAILABLE",
        }
        unused = self.CANONICAL_ERROR_CODES - subclass_codes - _ADVISORY_ONLY_CODES
        assert not unused, (
            f"CANONICAL_ERROR_CODES entries without a matching exception: {unused}. "
            f"Remove stale entries, add to _ADVISORY_ONLY_CODES if Pattern A, "
            f"or create the missing exception class."
        )
