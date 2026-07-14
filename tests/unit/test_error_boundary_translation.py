"""Tests for error boundary translation — AdCPError at each transport boundary.

Validates that:
- MCP boundary: AdCPError → ToolError with preserved error_code, message, and recovery
- A2A boundary: AdCPError → A2AError with correct JSON-RPC error code and recovery
- REST boundary: AdCPError → proper HTTP status code with recovery field
- ValueError and PermissionError are caught at boundaries
- extract_error_info handles AdCPError instances

beads: salesagent-pyeu, salesagent-d50c
"""

import json
from unittest.mock import patch

import pytest

from src.core.exceptions import (
    AdCPAdapterError,
    AdCPError,
    AdCPNotFoundError,
    AdCPValidationError,
)

# ---------------------------------------------------------------------------
# Wire-shape helpers — every boundary produces the AdCP spec two-layer
# envelope, so the boundary-specific wrappers below delegate to a single
# shared ``assert_envelope_shape`` in ``tests/helpers/``. A spec change to
# the envelope is now a one-place update.
# ---------------------------------------------------------------------------
from tests.helpers import assert_envelope_shape  # noqa: E402

# Per-boundary assertion wrappers were removed in favor of the canonical
# `assert_envelope_shape` helper. Call sites use keyword flags directly:
#   MCP:  assert_envelope_shape(exc, code, check_mcp_tool_error=True, ...)
#   A2A:  assert_envelope_shape(data, code, recovery=...)
#   REST: assert_envelope_shape(body, code, recovery=..., message_substr=...)


# ---------------------------------------------------------------------------
# MCP Boundary: extract_error_info
# ---------------------------------------------------------------------------


class TestExtractErrorInfoAdCPError:
    """extract_error_info must recognize AdCPError and extract error_code + message + recovery."""

    def test_adcp_validation_error_extracts_code_and_message(self):
        """AdCPValidationError → ('VALIDATION_ERROR', 'bad field', 'correctable')."""
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPValidationError("bad field")
        code, message, recovery = extract_error_info(exc)
        assert code == "VALIDATION_ERROR"
        assert message == "bad field"
        assert recovery == "correctable"

    def test_adcp_not_found_extracts_code_and_message(self):
        """AdCPNotFoundError → ('NOT_FOUND', 'resource missing', 'correctable').

        Recovery follows the wire code INVALID_REQUEST=correctable (salesagent-nr2q).
        """
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPNotFoundError("resource missing")
        code, message, recovery = extract_error_info(exc)
        assert code == "NOT_FOUND"
        assert message == "resource missing"
        assert recovery == "correctable"

    def test_adcp_adapter_error_extracts_code_and_message(self):
        """AdCPAdapterError → ('SERVICE_UNAVAILABLE', 'GAM down', 'transient')."""
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPAdapterError("GAM down")
        code, message, recovery = extract_error_info(exc)
        assert code == "SERVICE_UNAVAILABLE"
        assert message == "GAM down"
        assert recovery == "transient"

    # AdCPConflictError recovery (CONFLICT → transient) and AdCPBudgetExhaustedError
    # recovery (BUDGET_EXHAUSTED → terminal) are graded against the pinned enum by the
    # recovery-conformance oracle (#1417). The prior per-class literal methods
    # asserted the old correctable values and are removed.

    def test_adcp_gone_error_extracts_code_and_message(self):
        """AdCPGoneError → ('INVALID_STATE', 'proposal expired', 'correctable').

        Recovery defaults to ``correctable`` — the resource itself is gone but
        the buyer can recover by referencing a different resource.
        """
        from src.core.exceptions import AdCPGoneError
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPGoneError("proposal expired")
        code, message, recovery = extract_error_info(exc)
        assert code == "INVALID_STATE"
        assert message == "proposal expired"
        assert recovery == "correctable"

    def test_adcp_service_unavailable_error_extracts_code_and_message(self):
        """AdCPServiceUnavailableError → ('SERVICE_UNAVAILABLE', 'product unavailable', 'transient')."""
        from src.core.exceptions import AdCPServiceUnavailableError
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPServiceUnavailableError("product unavailable")
        code, message, recovery = extract_error_info(exc)
        assert code == "SERVICE_UNAVAILABLE"
        assert message == "product unavailable"
        assert recovery == "transient"

    def test_adcp_base_error_extracts_code_and_message(self):
        """AdCPError base → ('INTERNAL_ERROR', 'something broke', 'transient').

        Recovery follows the wire code SERVICE_UNAVAILABLE=transient (salesagent-nr2q).
        """
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPError("something broke")
        code, message, recovery = extract_error_info(exc)
        assert code == "INTERNAL_ERROR"
        assert message == "something broke"
        assert recovery == "transient"

    def test_adcp_rate_limit_error_extracts_transient_recovery(self):
        """AdCPRateLimitError → ('RATE_LIMITED', 'too fast', 'transient')."""
        from src.core.exceptions import AdCPRateLimitError
        from src.core.tool_error_logging import extract_error_info

        exc = AdCPRateLimitError("too fast")
        code, message, recovery = extract_error_info(exc)
        assert code == "RATE_LIMITED"
        assert message == "too fast"
        assert recovery == "transient"

    def test_plain_exception_returns_none_recovery(self):
        """Non-AdCPError exceptions return None for recovery."""
        from src.core.tool_error_logging import extract_error_info

        exc = RuntimeError("unexpected")
        code, message, recovery = extract_error_info(exc)
        assert code == "RuntimeError"
        assert message == "unexpected"
        assert recovery is None

    def test_tool_error_with_recovery_arg(self):
        """ToolError with 3 args extracts recovery from third arg."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import extract_error_info

        exc = ToolError("SERVICE_UNAVAILABLE", "GAM down", "transient")
        code, message, recovery = extract_error_info(exc)
        assert code == "SERVICE_UNAVAILABLE"
        assert message == "GAM down"
        assert recovery == "transient"

    def test_tool_error_without_recovery_returns_none(self):
        """ToolError with 2 args returns None for recovery."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import extract_error_info

        exc = ToolError("VALIDATION_ERROR", "bad field")
        code, message, recovery = extract_error_info(exc)
        assert code == "VALIDATION_ERROR"
        assert message == "bad field"
        assert recovery is None


# ---------------------------------------------------------------------------
# MCP Boundary: with_error_logging translates AdCPError → ToolError
# ---------------------------------------------------------------------------


class TestMCPBoundaryAdCPErrorTranslation:
    """with_error_logging must catch AdCPError and re-raise as ToolError with recovery."""

    def test_adcp_validation_becomes_tool_error(self):
        """AdCPValidationError from tool → ToolError with VALIDATION_ERROR code."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import with_error_logging

        def failing_tool():
            raise AdCPValidationError("bad field")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        # ToolError should carry the error code from AdCPError
        assert "VALIDATION_ERROR" in str(exc_info.value) or (
            exc_info.value.args and exc_info.value.args[0] == "VALIDATION_ERROR"
        )

    def test_adcp_validation_tool_error_carries_recovery(self):
        """AdCPValidationError → ToolError envelope carries 'correctable' recovery."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import with_error_logging

        def failing_tool():
            raise AdCPValidationError("bad field")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        assert_envelope_shape(exc_info.value, "VALIDATION_ERROR", check_mcp_tool_error=True, recovery="correctable")

    def test_adcp_adapter_tool_error_carries_transient_recovery(self):
        """AdCPAdapterError → ToolError envelope carries 'transient' recovery."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import with_error_logging

        def failing_tool():
            raise AdCPAdapterError("GAM down")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        assert_envelope_shape(
            exc_info.value,
            "SERVICE_UNAVAILABLE",
            check_mcp_tool_error=True,
            recovery="transient",
            message_substr="GAM down",
        )

    @pytest.mark.asyncio
    async def test_async_adcp_validation_becomes_tool_error(self):
        """Async: AdCPValidationError → ToolError envelope with preserved code and recovery."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import with_error_logging

        async def failing_tool():
            raise AdCPValidationError("bad field")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            await wrapped()

        assert_envelope_shape(exc_info.value, "VALIDATION_ERROR", check_mcp_tool_error=True, recovery="correctable")

    def test_tool_error_still_passes_through(self):
        """Existing ToolError behavior must be preserved — re-raised unchanged."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import with_error_logging

        def failing_tool():
            raise ToolError("EXISTING_CODE", "existing message")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        # Should be the same ToolError, not wrapped
        assert exc_info.value.args[0] == "EXISTING_CODE"

    def test_valueerror_becomes_tool_error(self):
        """ValueError from tool → ToolError with VALIDATION_ERROR code."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import with_error_logging

        def failing_tool():
            raise ValueError("invalid input")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        assert "VALIDATION_ERROR" in str(exc_info.value) or (
            exc_info.value.args and exc_info.value.args[0] == "VALIDATION_ERROR"
        )

    def test_permission_error_becomes_tool_error(self):
        """PermissionError from tool → ToolError with AUTH_REQUIRED code."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import with_error_logging

        def failing_tool():
            raise PermissionError("access denied")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        assert "AUTH_REQUIRED" in str(exc_info.value) or (
            exc_info.value.args and exc_info.value.args[0] == "AUTH_REQUIRED"
        )


# ---------------------------------------------------------------------------
# A2A Boundary: AdCPError → A2AError with proper JSON-RPC error code
# ---------------------------------------------------------------------------


class TestA2AHandlerExplicitSkillReraises:
    """``_handle_explicit_skill`` re-raises typed AdCPError verbatim.

    This class verifies the handler-internal contract: the skill dispatcher
    catches the typed exception, audits the workflow step, and re-raises so
    the OUTER ``on_message_send`` boundary wraps the failure into a Task
    artifact carrying the two-layer envelope DataPart.

    Wire-envelope coverage for the A2A boundary lives in
    ``tests/integration/test_a2a_error_responses.py`` — those tests drive
    ``handler.on_message_send(...)`` and assert on ``result.artifacts[0]``
    (the gold-standard pattern). The tests in this class stop at the
    handler-internal re-raise so they're cheap unit-level pins for the
    re-raise contract; they do NOT validate the envelope reaches the wire.
    """

    @pytest.mark.asyncio
    async def test_adcp_validation_propagates_for_dispatcher_wrap(self):
        """AdCPValidationError propagates verbatim; dispatcher will build envelope."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()

        async def mock_skill(params, token):
            raise AdCPValidationError("invalid param")

        with patch.object(handler, "_handle_get_products_skill", mock_skill):
            with pytest.raises(AdCPValidationError) as exc_info:
                await handler._handle_explicit_skill("get_products", {}, "token")

            assert "invalid param" in exc_info.value.message
            assert exc_info.value.error_code == "VALIDATION_ERROR"
            assert exc_info.value.recovery == "correctable"

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_adcp_adapter_propagates_for_dispatcher_wrap(self):
        """AdCPAdapterError propagates with transient recovery."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()

        async def mock_skill(params, token):
            raise AdCPAdapterError("GAM down")

        with patch.object(handler, "_handle_get_products_skill", mock_skill):
            with pytest.raises(AdCPAdapterError) as exc_info:
                await handler._handle_explicit_skill("get_products", {}, "token")

            assert "GAM down" in exc_info.value.message
            assert exc_info.value.recovery == "transient"

    @pytest.mark.asyncio
    async def test_server_error_still_passes_through(self):
        """Existing A2AError behavior preserved — re-raised unchanged."""
        from a2a.types import MethodNotFoundError

        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        handler = AdCPRequestHandler()

        async def mock_skill(params, token):
            raise MethodNotFoundError(message="not found")

        with patch.object(handler, "_handle_get_products_skill", mock_skill):
            with pytest.raises(MethodNotFoundError) as exc_info:
                await handler._handle_explicit_skill("get_products", {}, "token")

            # a2a-sdk 1.0: MethodNotFoundError is an A2AError subclass, re-raised as-is
            assert exc_info.value.message == "not found"


class TestA2ADispatcherFailedSkillResult:
    """``_build_failed_skill_result`` emits a spec-compliant envelope for every exception.

    Both the AdCPError branch and the untyped-Exception fallthrough in the
    explicit-skill dispatcher land here, so the artifact DataPart always
    carries the two-layer envelope shape — never a flat ``{error: ...}`` dict.
    Storyboard runners depend on ``adcp_error.code`` and ``errors[0].code``
    being readable from any failure path.
    """

    def test_adcp_error_keeps_typed_code(self):
        """AdCPError instances flow through unchanged — typed code preserved."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        result = AdCPRequestHandler._build_failed_skill_result("get_products", AdCPValidationError("bad input"))

        assert result["success"] is False
        assert result["skill"] == "get_products"
        env = result["error_envelope"]
        assert env["errors"][0]["message"] == "bad input"
        assert env["adcp_error"]["code"] == "VALIDATION_ERROR"
        assert env["errors"][0]["code"] == "VALIDATION_ERROR"
        assert env["errors"][0]["recovery"] == "correctable"

    def test_untyped_exception_wrapped_in_sanitized_adcp_error(self):
        """Bare ``Exception`` is wrapped in a SANITIZED synthetic AdCPError.

        Per the A2A boundary security policy (``_safe_adcp_error``), an untyped
        exception must NOT expose its raw ``str(exc)`` — which may carry credentials,
        connection strings, SQL, or hostnames. The message is replaced with a generic
        internal error, and the wire code is the safe ``SERVICE_UNAVAILABLE``
        (``AdCPError`` defaults to ``INTERNAL_ERROR`` → translated by ``wire_error_code``).
        """
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        result = AdCPRequestHandler._build_failed_skill_result(
            "get_products", RuntimeError("postgresql://svc:hunter2@db.internal/prod unexpected boom")
        )

        assert result["success"] is False
        env = result["error_envelope"]
        # Wire code is translated via ERROR_CODE_MAPPING
        assert env["adcp_error"]["code"] == "SERVICE_UNAVAILABLE"
        assert env["errors"][0]["code"] == "SERVICE_UNAVAILABLE"
        # The raw exception text is NEVER on the wire — only a generic internal message.
        message = env["errors"][0]["message"]
        assert "unexpected boom" not in message
        assert "hunter2" not in message and "postgresql://" not in message
        assert "internal error" in message.lower()

    def test_untyped_exception_with_empty_message_still_sanitized(self):
        """An untyped exception with no string content still yields the generic sanitized
        message — never an empty ``message`` (spec requires non-empty) and never the raw
        exception class name (which could itself hint at internals).
        """
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        result = AdCPRequestHandler._build_failed_skill_result("get_products", RuntimeError())

        env = result["error_envelope"]
        message = env["errors"][0]["message"]
        assert message, "wire envelope message must be non-empty"
        assert message != "RuntimeError", "must not fall back to the exception class name"
        assert "internal error" in message.lower()

    def test_internal_bucket_typed_error_message_is_scrubbed(self):
        """A TYPED internal/infra error (SERVICE_UNAVAILABLE bucket) that interpolated a
        secret into its message is scrubbed at the boundary — code + recovery preserved.

        Reachable handlers build e.g. ``AdCPAdapterError(f"...: {e}")`` where ``e`` carries a
        DB connection string. Because it is already an ``AdCPError`` a naive sanitizer would
        trust it; ``_safe_adcp_error`` instead replaces the message for the
        ``wire_error_code == "SERVICE_UNAVAILABLE"`` bucket while keeping the wire code and the
        buyer-facing retry semantics (``recovery``).
        """
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        secret = "postgresql://svc:hunter2@db.internal/prod SELECT * FROM principals"
        result = AdCPRequestHandler._build_failed_skill_result(
            "create_media_buy", AdCPAdapterError(f"Failed to create media buy: {secret}")
        )

        env = result["error_envelope"]
        blob = json.dumps(env)
        for leak in ("hunter2", "postgresql://", "db.internal", "SELECT", "principals"):
            assert leak not in blob, f"internal-error message leaked {leak!r} to the wire: {blob}"
        # Wire code preserved (adapter → SERVICE_UNAVAILABLE); recovery preserved (transient).
        assert env["adcp_error"]["code"] == "SERVICE_UNAVAILABLE"
        assert env["errors"][0]["code"] == "SERVICE_UNAVAILABLE"
        assert env["errors"][0]["recovery"] == "transient"
        assert "internal error" in env["errors"][0]["message"].lower()

    def test_client_correctable_typed_error_message_is_preserved(self):
        """A client-correctable typed error keeps its controlled message — the boundary must
        NOT over-sanitize. Buyers need the specific guidance to fix their request.
        """
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        result = AdCPRequestHandler._build_failed_skill_result(
            "get_products", AdCPValidationError("brief must not be empty")
        )

        env = result["error_envelope"]
        assert env["errors"][0]["code"] == "VALIDATION_ERROR"
        assert env["errors"][0]["recovery"] == "correctable"
        assert env["errors"][0]["message"] == "brief must not be empty"

    def test_envelope_shape_matches_typed_branch(self):
        """Untyped fallthrough produces the SAME envelope shape as the typed branch.

        Storyboard runners must be able to parse the DataPart uniformly
        regardless of which catch branch produced the failure result. The
        set-equality on key names alone is not enough — values must also be
        type-equivalent (e.g., ``recovery`` is a string in both branches,
        not ``None`` in one and a value in the other) so a regression that
        nulls one branch's recovery is caught here.
        """
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler

        # Conformant raise sites carry a top-level suggestion (#1417); the untyped
        # branch synthesizes one, so the typed sample must too for shape parity.
        typed = AdCPRequestHandler._build_failed_skill_result(
            "s", AdCPValidationError("bad", suggestion="Correct the request and resend.")
        )
        untyped = AdCPRequestHandler._build_failed_skill_result("s", RuntimeError("boom"))

        assert set(typed.keys()) == set(untyped.keys())
        assert set(typed["error_envelope"].keys()) == set(untyped["error_envelope"].keys())

        typed_envelope = typed["error_envelope"]
        untyped_envelope = untyped["error_envelope"]
        assert set(typed_envelope["errors"][0].keys()) == set(untyped_envelope["errors"][0].keys())

        # Field-value pin: both branches must populate the same keys with
        # non-None values for the contract storyboard runners depend on.
        typed_adcp_error = typed_envelope["adcp_error"]
        untyped_adcp_error = untyped_envelope["adcp_error"]
        assert typed_adcp_error["code"] and untyped_adcp_error["code"], (
            f"Both branches must populate adcp_error.code; "
            f"typed={typed_adcp_error.get('code')!r}, untyped={untyped_adcp_error.get('code')!r}"
        )
        assert typed_adcp_error.get("recovery") and untyped_adcp_error.get("recovery"), (
            f"Both branches must populate adcp_error.recovery; "
            f"typed={typed_adcp_error.get('recovery')!r}, untyped={untyped_adcp_error.get('recovery')!r}"
        )

        typed_err0 = typed_envelope["errors"][0]
        untyped_err0 = untyped_envelope["errors"][0]
        assert typed_err0["code"] and untyped_err0["code"], (
            f"Both branches must populate errors[0].code; "
            f"typed={typed_err0.get('code')!r}, untyped={untyped_err0.get('code')!r}"
        )
        assert typed_err0["message"] and untyped_err0["message"], (
            f"Both branches must populate errors[0].message; "
            f"typed={typed_err0.get('message')!r}, untyped={untyped_err0.get('message')!r}"
        )


# ---------------------------------------------------------------------------
# REST Boundary: AdCPError → HTTP status code via exception handler
# ---------------------------------------------------------------------------


class TestRESTBoundaryAdCPErrorTranslation:
    """REST endpoints propagate AdCPError to the app-level exception handler with recovery."""

    def test_adcp_validation_from_impl_returns_400(self):
        """AdCPValidationError raised in _impl → REST returns 400 with correctable recovery."""
        from starlette.testclient import TestClient

        from src.app import app

        with patch(
            "src.core.tools.capabilities.get_adcp_capabilities_raw",
            side_effect=AdCPValidationError("invalid request"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/capabilities")
            assert response.status_code == 400
            assert_envelope_shape(
                response.json(), "VALIDATION_ERROR", recovery="correctable", message_substr="invalid request"
            )

    def test_adcp_not_found_from_impl_returns_404(self):
        """AdCPNotFoundError raised in _impl → REST returns 404 with correctable recovery.

        Recovery matches the pinned enumMetadata of the WIRE code:
        INVALID_REQUEST=correctable (salesagent-nr2q).
        """
        from starlette.testclient import TestClient

        from src.app import app

        with patch(
            "src.core.tools.capabilities.get_adcp_capabilities_raw",
            side_effect=AdCPNotFoundError("resource not found"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/capabilities")
            assert response.status_code == 404
            # AdCPNotFoundError's NOT_FOUND is INTERNAL_CODES; envelope translates
            # to INVALID_REQUEST so the wire code stays in STANDARD_ERROR_CODES.
            assert_envelope_shape(response.json(), "INVALID_REQUEST", recovery="correctable")

    def test_adcp_adapter_from_impl_returns_502(self):
        """AdCPAdapterError raised in _impl → REST returns 502 with transient recovery."""
        from starlette.testclient import TestClient

        from src.app import app

        with patch(
            "src.core.tools.capabilities.get_adcp_capabilities_raw",
            side_effect=AdCPAdapterError("GAM unavailable"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/capabilities")
            assert response.status_code == 502
            assert_envelope_shape(response.json(), "SERVICE_UNAVAILABLE", recovery="transient")

    def test_adcp_conflict_from_impl_returns_409(self):
        """AdCPConflictError raised in _impl → REST returns 409 with transient recovery."""
        from starlette.testclient import TestClient

        from src.app import app
        from src.core.exceptions import AdCPConflictError

        with patch(
            "src.core.tools.capabilities.get_adcp_capabilities_raw",
            side_effect=AdCPConflictError("duplicate key"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/capabilities")
            assert response.status_code == 409
            # CONFLICT recovery is transient per the pinned enum (#1417).
            assert_envelope_shape(response.json(), "CONFLICT", recovery="transient")

    def test_adcp_service_unavailable_from_impl_returns_503(self):
        """AdCPServiceUnavailableError raised in _impl → REST returns 503 with transient recovery."""
        from starlette.testclient import TestClient

        from src.app import app
        from src.core.exceptions import AdCPServiceUnavailableError

        with patch(
            "src.core.tools.capabilities.get_adcp_capabilities_raw",
            side_effect=AdCPServiceUnavailableError("product unavailable"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/capabilities")
            assert response.status_code == 503
            assert_envelope_shape(response.json(), "SERVICE_UNAVAILABLE", recovery="transient")


class TestGlobalToolErrorHandler:
    """Global ``@app.exception_handler(ToolError)`` translates ToolError to envelope.

    Removes the need for every REST route to wrap its body in
    ``try/except ToolError`` — the global handler catches both plain ``ToolError``
    and ``AdCPToolError`` (subclass) and produces the same envelope shape as
    the per-route ``handle_tool_error`` did. Verifies the wiring works
    end-to-end through the REST stack.
    """

    def test_adcp_tool_error_through_global_handler_preserves_status(self):
        """AdCPToolError from _impl is caught by the global handler with original status_code."""
        from starlette.testclient import TestClient

        from src.app import app
        from src.core.exceptions import AdCPMediaBuyNotFoundError, build_two_layer_error_envelope
        from src.core.tool_error_logging import AdCPToolError

        source = AdCPMediaBuyNotFoundError("buy_x missing")
        tool_error = AdCPToolError(build_two_layer_error_envelope(source), status_code=source.status_code)

        with patch(
            "src.core.tools.capabilities.get_adcp_capabilities_raw",
            side_effect=tool_error,
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/capabilities")
            assert response.status_code == 404
            assert_envelope_shape(response.json(), "MEDIA_BUY_NOT_FOUND", recovery="correctable")

    def test_plain_tool_error_with_known_code_through_global_handler(self):
        """Plain ToolError("VALIDATION_ERROR", "msg") → 400 via global handler + status map."""
        from fastmcp.exceptions import ToolError
        from starlette.testclient import TestClient

        from src.app import app

        with patch(
            "src.core.tools.capabilities.get_adcp_capabilities_raw",
            side_effect=ToolError("VALIDATION_ERROR", "missing field"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/capabilities")
            assert response.status_code == 400


class TestRESTSymmetricValueErrorAndPermissionError:
    """REST mirrors MCP/A2A by wrapping ValueError and PermissionError in envelopes.

    Without these handlers, a raw ``ValueError`` raised by application code
    would surface as a 500 server error on REST while the same exception
    produces a 400 VALIDATION_ERROR envelope on MCP and A2A. Cross-transport
    symmetry: every transport translates the same Python exception to the
    same wire shape.
    """

    def test_value_error_returns_400_with_validation_envelope(self):
        """Raw ValueError → 400 with VALIDATION_ERROR envelope (mirrors MCP wrapper)."""
        from starlette.testclient import TestClient

        from src.app import app

        with patch(
            "src.core.tools.capabilities.get_adcp_capabilities_raw",
            side_effect=ValueError("invalid input shape"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/capabilities")
            assert response.status_code == 400
            assert_envelope_shape(
                response.json(),
                "VALIDATION_ERROR",
                recovery="correctable",
                message_substr="invalid input shape",
            )

    def test_request_validation_error_unaffected(self):
        """FastAPI's RequestValidationError handler is NOT overridden by our ValueError handler.

        ``RequestValidationError`` is not a ``ValueError`` subclass, so FastAPI's
        existing 422 + ``{"detail": [...]}`` response shape for request-body
        validation failures continues to work — only application-raised
        ``ValueError`` is wrapped into the AdCP envelope.
        """
        from fastapi.exceptions import RequestValidationError

        assert not issubclass(RequestValidationError, ValueError), (
            "RequestValidationError must not inherit ValueError, otherwise our "
            "ValueError handler would shadow FastAPI's request-body 422 handler."
        )


# ---------------------------------------------------------------------------
# REST defensive ToolError catch: handle_tool_error must preserve status_code
# ---------------------------------------------------------------------------


def _synthetic_tool_error(source):
    """Wrap an AdCPError as the AdCPToolError a REST route catches defensively."""
    from src.core.exceptions import build_two_layer_error_envelope
    from src.core.tool_error_logging import AdCPToolError

    return AdCPToolError(build_two_layer_error_envelope(source), status_code=source.status_code)


class TestHandleToolErrorPreservesStatusCode:
    """``handle_tool_error`` must use the source AdCPError's status_code.

    REST routes catch ``ToolError`` defensively (when downstream code is
    wrapped by ``with_error_logging`` and translates AdCPError → AdCPToolError).
    The wire HTTP status must reflect the original AdCPError's classification
    (400/401/403/404/422/etc.) — not the hardcoded 500 it used to default to,
    which caused 4xx errors to be mislabeled as 5xx on this defensive path.
    """

    @pytest.mark.parametrize(
        ("source_cls_name", "message", "expected_status"),
        [
            ("AdCPValidationError", "invalid request", 400),
            ("AdCPAuthenticationError", "token expired", 401),
            ("AdCPMediaBuyNotFoundError", "buy_x missing", 404),
            ("AdCPBudgetTooLowError", "below minimum", 422),
            ("AdCPAdapterError", "GAM unavailable", 502),
        ],
    )
    def test_preserves_source_status_code(self, source_cls_name, message, expected_status):
        """handle_tool_error uses the source AdCPError's status_code, not a hardcoded 500.

        A new typed subclass is one parametrize row, not one method.
        """
        import src.core.exceptions as exceptions_mod
        from src.core.tool_error_logging import handle_tool_error

        source = getattr(exceptions_mod, source_cls_name)(message)
        response = handle_tool_error(_synthetic_tool_error(source))
        assert response.status_code == expected_status

    def test_plain_tool_error_falls_back_to_500(self):
        """Plain ToolError with no recognized wire code defaults to 500."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import handle_tool_error

        response = handle_tool_error(ToolError("unstructured failure"))
        assert response.status_code == 500

    def test_plain_tool_error_with_known_code_uses_status_map(self):
        """Plain ToolError("VALIDATION_ERROR", "msg") → 400 via _ERROR_CODE_TO_STATUS.

        Legacy paths that construct ToolError directly (without going through
        AdCPToolError) used to land at 500 because ``AdCPError`` defaulted to
        500 and only ``error_code`` was overridden. The map ensures the HTTP
        status matches the wire code on this defensive fallback path.
        """
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import handle_tool_error

        response = handle_tool_error(ToolError("VALIDATION_ERROR", "missing required field"))
        assert response.status_code == 400

    def test_plain_tool_error_with_auth_code_returns_403(self):
        """Plain ToolError("AUTH_REQUIRED", "msg") → 403 via _ERROR_CODE_TO_STATUS.

        AUTH_REQUIRED is declared by both AdCPAuthenticationError (401) and
        AdCPAuthorizationError (403). The auto-derived table picks the
        more restrictive status (403) since a plain-ToolError fallback
        carries no context to disambiguate. A prior hand-coded
        ``AUTH_REQUIRED → 401`` mapping conflicted with
        AdCPAuthorizationError.status_code=403.
        """
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import handle_tool_error

        response = handle_tool_error(ToolError("AUTH_REQUIRED", "missing token"))
        assert response.status_code == 403

    def test_plain_tool_error_with_not_found_code_returns_404(self):
        """Plain ToolError("MEDIA_BUY_NOT_FOUND", "msg") → 404 via _ERROR_CODE_TO_STATUS."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import handle_tool_error

        response = handle_tool_error(ToolError("MEDIA_BUY_NOT_FOUND", "buy_x missing"))
        assert response.status_code == 404

    def test_plain_tool_error_with_unknown_code_falls_back_to_500(self):
        """Plain ToolError with an unmapped wire code defaults to 500."""
        from fastmcp.exceptions import ToolError

        from src.core.tool_error_logging import handle_tool_error

        response = handle_tool_error(ToolError("WEIRD_LEGACY_CODE", "what is this"))
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# to_dict() serialization: recovery field present and correct
# ---------------------------------------------------------------------------


class TestToDictRecoveryField:
    """AdCPError.to_dict() must include recovery in the serialized dict."""

    def test_to_dict_includes_recovery_for_all_subclasses(self):
        """Every AdCPError subclass produces recovery in to_dict() output."""
        from src.core.exceptions import (
            AdCPAdapterError,
            AdCPBudgetExhaustedError,
            AdCPConflictError,
            AdCPError,
            AdCPGoneError,
            AdCPNotFoundError,
            AdCPRateLimitError,
            AdCPServiceUnavailableError,
            AdCPValidationError,
        )

        cases = [
            # Recovery follows the wire code (salesagent-nr2q): base
            # AdCPError→SERVICE_UNAVAILABLE=transient,
            # AdCPNotFoundError→INVALID_REQUEST=correctable.
            (AdCPError("internal"), "transient"),
            (AdCPValidationError("bad field"), "correctable"),
            (AdCPNotFoundError("missing"), "correctable"),
            (AdCPConflictError("duplicate"), "transient"),
            (AdCPGoneError("expired"), "correctable"),
            (AdCPBudgetExhaustedError("no budget"), "terminal"),
            (AdCPRateLimitError("slow down"), "transient"),
            (AdCPAdapterError("GAM down"), "transient"),
            (AdCPServiceUnavailableError("unavailable"), "transient"),
        ]

        for exc, expected_recovery in cases:
            d = exc.to_dict()
            assert "recovery" in d, f"{type(exc).__name__}.to_dict() missing 'recovery' key"
            msg = f"{type(exc).__name__}.to_dict() recovery={d['recovery']!r}, expected {expected_recovery!r}"
            assert d["recovery"] == expected_recovery, msg

    def test_to_dict_custom_recovery_override(self):
        """Custom recovery= kwarg overrides class default in to_dict() output."""
        from src.core.exceptions import AdCPNotFoundError

        # Default is "correctable" (wire INVALID_REQUEST, salesagent-nr2q)
        default_exc = AdCPNotFoundError("gone")
        assert default_exc.to_dict()["recovery"] == "correctable"

        # Override to "terminal"
        overridden = AdCPNotFoundError("permanently gone", recovery="terminal")
        assert overridden.to_dict()["recovery"] == "terminal"

    def test_to_dict_roundtrip_preserves_all_fields(self):
        """Serialize to dict, reconstruct, verify recovery survives the roundtrip."""
        from src.core.exceptions import AdCPAdapterError

        original = AdCPAdapterError("GAM timeout", details={"retry_after": 30})
        d = original.to_dict()

        # Verify all fields present
        assert d == {
            "error_code": "SERVICE_UNAVAILABLE",
            "message": "GAM timeout",
            "recovery": "transient",
            "details": {"retry_after": 30},
        }


# ---------------------------------------------------------------------------
# Custom recovery override preservation through all boundaries
# ---------------------------------------------------------------------------


class TestCustomRecoveryOverrideMCPBoundary:
    """Custom recovery= override must propagate through MCP boundary (with_error_logging)."""

    def test_custom_recovery_propagates_through_mcp_boundary(self):
        """AdCPNotFoundError(recovery='transient') -> ToolError carries 'transient' not 'terminal'."""
        from fastmcp.exceptions import ToolError

        from src.core.exceptions import AdCPNotFoundError
        from src.core.tool_error_logging import with_error_logging

        def failing_tool():
            raise AdCPNotFoundError("temporarily missing", recovery="transient")

        wrapped = with_error_logging(failing_tool)

        with pytest.raises(ToolError) as exc_info:
            wrapped()

        # AdCPNotFoundError's NOT_FOUND code maps to INVALID_REQUEST at the wire
        # boundary so output is spec-compliant; custom recovery still propagates.
        assert_envelope_shape(
            exc_info.value,
            "INVALID_REQUEST",
            check_mcp_tool_error=True,
            recovery="transient",
            message_substr="temporarily missing",
        )

    def test_custom_recovery_in_extract_error_info(self):
        """extract_error_info returns overridden recovery, not class default."""
        from src.core.exceptions import AdCPValidationError
        from src.core.tool_error_logging import extract_error_info

        # Override correctable -> terminal
        exc = AdCPValidationError("fatal validation", recovery="terminal")
        code, message, recovery = extract_error_info(exc)
        assert code == "VALIDATION_ERROR"
        assert recovery == "terminal"  # Custom, not default "correctable"


class TestCustomRecoveryOverrideA2ABoundary:
    """Custom recovery= override must propagate through A2A boundary."""

    @pytest.mark.asyncio
    async def test_custom_recovery_propagates_through_a2a_boundary(self):
        """AdCPNotFoundError(recovery='transient') propagates with the override intact."""
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from src.core.exceptions import AdCPNotFoundError

        handler = AdCPRequestHandler()

        async def mock_skill(params, token):
            raise AdCPNotFoundError("temporarily missing", recovery="transient")

        with patch.object(handler, "_handle_get_products_skill", mock_skill):
            with pytest.raises(AdCPNotFoundError) as exc_info:
                await handler._handle_explicit_skill("get_products", {}, "token")
            assert exc_info.value.recovery == "transient"


class TestCustomRecoveryOverrideRESTBoundary:
    """Custom recovery= override must propagate through REST boundary (exception handler)."""

    def test_custom_recovery_propagates_through_rest_boundary(self):
        """AdCPAdapterError(recovery='terminal') -> REST JSON body has 'terminal'."""
        from starlette.testclient import TestClient

        from src.app import app
        from src.core.exceptions import AdCPAdapterError

        with patch(
            "src.core.tools.capabilities.get_adcp_capabilities_raw",
            side_effect=AdCPAdapterError("permanent failure", recovery="terminal"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/capabilities")
            assert response.status_code == 502
            assert_envelope_shape(response.json(), "SERVICE_UNAVAILABLE", recovery="terminal")


# ---------------------------------------------------------------------------
# Roundtrip: raise → catch at boundary → serialize → deserialize → check recovery
# ---------------------------------------------------------------------------


class TestRecoveryRoundtrip:
    """Full roundtrip through raise -> boundary catch -> serialize -> verify recovery."""

    def test_mcp_roundtrip_all_subclasses(self):
        """All 11 AdCPError subclasses: raise -> with_error_logging -> ToolError -> extract_error_info."""
        from src.core.exceptions import (
            AdCPAdapterError,
            AdCPBudgetExhaustedError,
            AdCPConflictError,
            AdCPError,
            AdCPGoneError,
            AdCPNotFoundError,
            AdCPRateLimitError,
            AdCPServiceUnavailableError,
            AdCPValidationError,
        )
        from src.core.tool_error_logging import extract_error_info, with_error_logging

        # AdCPError (INTERNAL_ERROR) and AdCPNotFoundError (NOT_FOUND) hold internal
        # codes; the boundary translates to STANDARD_ERROR_CODES (SERVICE_UNAVAILABLE
        # and INVALID_REQUEST respectively). Other subclasses already use STANDARD codes.
        cases = [
            # Recovery matches the pinned classification of the WIRE code
            # (salesagent-nr2q): SERVICE_UNAVAILABLE=transient, INVALID_REQUEST=correctable.
            (AdCPError, "internal", "SERVICE_UNAVAILABLE", "transient"),
            (AdCPValidationError, "bad", "VALIDATION_ERROR", "correctable"),
            (AdCPNotFoundError, "missing", "INVALID_REQUEST", "correctable"),
            (AdCPConflictError, "dup", "CONFLICT", "transient"),
            (AdCPGoneError, "expired", "INVALID_STATE", "correctable"),
            (AdCPBudgetExhaustedError, "broke", "BUDGET_EXHAUSTED", "terminal"),
            (AdCPRateLimitError, "slow", "RATE_LIMITED", "transient"),
            (AdCPAdapterError, "down", "SERVICE_UNAVAILABLE", "transient"),
            (AdCPServiceUnavailableError, "offline", "SERVICE_UNAVAILABLE", "transient"),
        ]

        for exc_class, msg, expected_code, expected_recovery in cases:

            def make_tool(klass=exc_class, message=msg):
                def failing():
                    raise klass(message)

                return failing

            from fastmcp.exceptions import ToolError

            wrapped = with_error_logging(make_tool())

            with pytest.raises(ToolError) as exc_info:
                wrapped()

            tool_error = exc_info.value

            # Step 1: ToolError carries the spec-compliant envelope
            assert_envelope_shape(tool_error, expected_code, check_mcp_tool_error=True, recovery=expected_recovery)

            # Step 2: extract_error_info can read it back
            code, message_out, recovery = extract_error_info(tool_error)
            assert code == expected_code, f"{exc_class.__name__}: roundtrip code mismatch"
            assert recovery == expected_recovery, f"{exc_class.__name__}: roundtrip recovery mismatch"

    @pytest.mark.asyncio
    async def test_a2a_handler_explicit_skill_reraises_all_subclasses(self):
        """All 11 AdCPError subclasses propagate verbatim from ``_handle_explicit_skill``.

        Scope: handler-internal re-raise. The outer ``on_message_send``
        boundary wraps the propagated exception into a Task artifact
        carrying the two-layer envelope; that wire-level coverage is in
        ``tests/integration/test_a2a_error_responses.py``.
        """
        from src.a2a_server.adcp_a2a_server import AdCPRequestHandler
        from src.core.exceptions import (
            AdCPAdapterError,
            AdCPBudgetExhaustedError,
            AdCPConflictError,
            AdCPError,
            AdCPGoneError,
            AdCPNotFoundError,
            AdCPRateLimitError,
            AdCPServiceUnavailableError,
            AdCPValidationError,
        )

        cases = [
            # Recovery matches the pinned classification of the WIRE code (salesagent-nr2q).
            (AdCPError, "internal", "transient"),
            (AdCPValidationError, "bad", "correctable"),
            (AdCPNotFoundError, "missing", "correctable"),
            (AdCPConflictError, "dup", "transient"),
            (AdCPGoneError, "expired", "correctable"),
            (AdCPBudgetExhaustedError, "broke", "terminal"),
            (AdCPRateLimitError, "slow", "transient"),
            (AdCPAdapterError, "down", "transient"),
            (AdCPServiceUnavailableError, "offline", "transient"),
        ]

        handler = AdCPRequestHandler()

        for exc_class, msg, expected_recovery in cases:

            async def mock_skill(params, token, klass=exc_class, message=msg):
                raise klass(message)

            with patch.object(handler, "_handle_get_products_skill", mock_skill):
                with pytest.raises(exc_class) as exc_info:
                    await handler._handle_explicit_skill("get_products", {}, "token")
                assert exc_info.value.recovery == expected_recovery

    def test_rest_roundtrip_all_subclasses(self):
        """All 11 AdCPError subclasses: raise -> REST handler -> JSON body -> verify recovery."""
        from starlette.testclient import TestClient

        from src.app import app
        from src.core.exceptions import (
            AdCPAdapterError,
            AdCPBudgetExhaustedError,
            AdCPConflictError,
            AdCPError,
            AdCPGoneError,
            AdCPNotFoundError,
            AdCPRateLimitError,
            AdCPServiceUnavailableError,
            AdCPValidationError,
        )

        # Same internal-code -> standard-code translation as the MCP/A2A roundtrip
        # tests above. HTTP status_code is preserved (it comes from the exception
        # class directly, not from the wire code translation).
        cases = [
            # Recovery matches the pinned classification of the WIRE code (salesagent-nr2q).
            (AdCPError, "internal", 500, "SERVICE_UNAVAILABLE", "transient"),
            (AdCPValidationError, "bad", 400, "VALIDATION_ERROR", "correctable"),
            (AdCPNotFoundError, "missing", 404, "INVALID_REQUEST", "correctable"),
            (AdCPConflictError, "dup", 409, "CONFLICT", "transient"),
            (AdCPGoneError, "expired", 410, "INVALID_STATE", "correctable"),
            (AdCPBudgetExhaustedError, "broke", 422, "BUDGET_EXHAUSTED", "terminal"),
            (AdCPRateLimitError, "slow", 429, "RATE_LIMITED", "transient"),
            (AdCPAdapterError, "down", 502, "SERVICE_UNAVAILABLE", "transient"),
            (AdCPServiceUnavailableError, "offline", 503, "SERVICE_UNAVAILABLE", "transient"),
        ]

        for exc_class, msg, expected_status, expected_code, expected_recovery in cases:
            with patch(
                "src.core.tools.capabilities.get_adcp_capabilities_raw",
                side_effect=exc_class(msg),
            ):
                client = TestClient(app, raise_server_exceptions=False)
                response = client.get("/api/v1/capabilities")
                status_msg = f"{exc_class.__name__}: status {response.status_code}, expected {expected_status}"
                assert response.status_code == expected_status, status_msg
                assert_envelope_shape(response.json(), expected_code, recovery=expected_recovery)
