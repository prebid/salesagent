"""Tests for AdCP exception hierarchy and FastAPI exception handlers.

Validates that:
- Exception classes exist with proper inheritance and attributes
- FastAPI handlers return correct HTTP status codes and response format
- Exception → ToolError format mapping exists
- Dead A2A error map is not present (real translation in adcp_a2a_server.py)

beads: salesagent-b61l.11
"""

import pytest
from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# Exception Hierarchy Tests
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    """Verify AdCP exception classes exist with correct attributes."""

    def test_base_exception_exists(self):
        """AdCPError base class must exist."""
        from src.core.exceptions import AdCPError

        exc = AdCPError("test error")
        assert str(exc) == "test error"
        assert isinstance(exc, Exception)

    def test_validation_error(self):
        """AdCPValidationError must have status_code=400."""
        from src.core.exceptions import AdCPError, AdCPValidationError

        exc = AdCPValidationError("invalid field")
        assert isinstance(exc, AdCPError)
        assert exc.status_code == 400
        assert exc.error_code == "VALIDATION_ERROR"

    def test_authentication_error(self):
        """AdCPAuthenticationError must have status_code=401."""
        from src.core.exceptions import AdCPAuthenticationError, AdCPError

        exc = AdCPAuthenticationError("bad token")
        assert isinstance(exc, AdCPError)
        assert exc.status_code == 401
        assert exc.error_code == "AUTH_TOKEN_INVALID"

    def test_authorization_error(self):
        """AdCPAuthorizationError must have status_code=403."""
        from src.core.exceptions import AdCPAuthorizationError, AdCPError

        exc = AdCPAuthorizationError("forbidden")
        assert isinstance(exc, AdCPError)
        assert exc.status_code == 403
        assert exc.error_code == "AUTH_REQUIRED"

    def test_not_found_error(self):
        """AdCPNotFoundError must have status_code=404."""
        from src.core.exceptions import AdCPError, AdCPNotFoundError

        exc = AdCPNotFoundError("resource missing")
        assert isinstance(exc, AdCPError)
        assert exc.status_code == 404
        assert exc.error_code == "NOT_FOUND"

    def test_rate_limit_error(self):
        """AdCPRateLimitError must have status_code=429."""
        from src.core.exceptions import AdCPError, AdCPRateLimitError

        exc = AdCPRateLimitError("too many requests")
        assert isinstance(exc, AdCPError)
        assert exc.status_code == 429
        assert exc.error_code == "RATE_LIMITED"

    def test_adapter_error(self):
        """AdCPAdapterError must have status_code=502."""
        from src.core.exceptions import AdCPAdapterError, AdCPError

        exc = AdCPAdapterError("GAM unavailable")
        assert isinstance(exc, AdCPError)
        assert exc.status_code == 502
        assert exc.error_code == "SERVICE_UNAVAILABLE"

    def test_conflict_error(self):
        """AdCPConflictError must have status_code=409."""
        from src.core.exceptions import AdCPConflictError, AdCPError

        exc = AdCPConflictError("duplicate idempotency key")
        assert isinstance(exc, AdCPError)
        assert exc.status_code == 409
        assert exc.error_code == "CONFLICT"

    def test_gone_error(self):
        """AdCPGoneError must have status_code=410."""
        from src.core.exceptions import AdCPError, AdCPGoneError

        exc = AdCPGoneError("proposal expired")
        assert isinstance(exc, AdCPError)
        assert exc.status_code == 410
        assert exc.error_code == "INVALID_STATE"

    def test_idempotency_conflict_error(self):
        """AdCPIdempotencyConflictError must be a 409 conflict with code IDEMPOTENCY_CONFLICT."""
        from src.core.exceptions import AdCPConflictError, AdCPError, AdCPIdempotencyConflictError

        exc = AdCPIdempotencyConflictError("idempotency_key reused with a different payload")
        assert isinstance(exc, AdCPConflictError)
        assert isinstance(exc, AdCPError)
        assert exc.status_code == 409
        assert exc.error_code == "IDEMPOTENCY_CONFLICT"

    def test_idempotency_conflict_wire_envelope(self):
        """The two-layer envelope carries IDEMPOTENCY_CONFLICT + correctable in both layers.

        Correctable per the AdCP 3.0.1 prose example and storyboard expectation:
        the buyer can resend the original bytes or mint a fresh key.
        """
        from src.core.exceptions import AdCPIdempotencyConflictError, build_two_layer_error_envelope

        env = build_two_layer_error_envelope(AdCPIdempotencyConflictError("dup key"))
        assert env["adcp_error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert env["adcp_error"]["recovery"] == "correctable"
        assert env["errors"][0]["code"] == "IDEMPOTENCY_CONFLICT"

    def test_idempotency_expired_error(self):
        """AdCPIdempotencyExpiredError must be a 409 conflict with code IDEMPOTENCY_EXPIRED."""
        from src.core.exceptions import AdCPConflictError, AdCPError, AdCPIdempotencyExpiredError

        exc = AdCPIdempotencyExpiredError("replay window has expired")
        assert isinstance(exc, AdCPConflictError)
        assert isinstance(exc, AdCPError)
        assert exc.status_code == 409
        assert exc.error_code == "IDEMPOTENCY_EXPIRED"

    def test_idempotency_expired_wire_envelope(self):
        """The two-layer envelope carries IDEMPOTENCY_EXPIRED + terminal in both layers."""
        from src.core.exceptions import AdCPIdempotencyExpiredError, build_two_layer_error_envelope

        env = build_two_layer_error_envelope(AdCPIdempotencyExpiredError("stale key"))
        assert env["adcp_error"]["code"] == "IDEMPOTENCY_EXPIRED"
        assert env["adcp_error"]["recovery"] == "terminal"
        assert env["errors"][0]["code"] == "IDEMPOTENCY_EXPIRED"

    def test_rate_limit_retry_after_rides_both_envelope_layers(self):
        """retry_after is a first-class Error field — both layers carry it."""
        from src.core.exceptions import AdCPRateLimitError, build_two_layer_error_envelope

        env = build_two_layer_error_envelope(AdCPRateLimitError("slow down", retry_after=42))
        assert env["adcp_error"]["code"] == "RATE_LIMITED"
        assert env["adcp_error"]["retry_after"] == 42
        assert env["errors"][0]["retry_after"] == 42

    def test_budget_exhausted_error(self):
        """AdCPBudgetExhaustedError must have status_code=422."""
        from src.core.exceptions import AdCPBudgetExhaustedError, AdCPError

        exc = AdCPBudgetExhaustedError("budget limit reached")
        assert isinstance(exc, AdCPError)
        assert exc.status_code == 422
        assert exc.error_code == "BUDGET_EXHAUSTED"

    def test_service_unavailable_error(self):
        """AdCPServiceUnavailableError must have status_code=503."""
        from src.core.exceptions import AdCPError, AdCPServiceUnavailableError

        exc = AdCPServiceUnavailableError("product temporarily unavailable")
        assert isinstance(exc, AdCPError)
        assert exc.status_code == 503
        assert exc.error_code == "SERVICE_UNAVAILABLE"

    def test_exception_carries_details(self):
        """Exceptions must support optional details dict."""
        from src.core.exceptions import AdCPValidationError

        details = {"field": "budget", "constraint": "must be positive"}
        exc = AdCPValidationError("invalid budget", details=details)
        assert exc.details == details

    def test_exception_to_dict(self):
        """Exceptions must be serializable to dict for response bodies."""
        from src.core.exceptions import AdCPValidationError

        exc = AdCPValidationError("bad field", details={"field": "name"})
        d = exc.to_dict()
        assert d["error_code"] == "VALIDATION_ERROR"
        assert d["message"] == "bad field"
        assert d["details"] == {"field": "name"}


# ---------------------------------------------------------------------------
# Recovery Classification Tests
# ---------------------------------------------------------------------------


class TestRecoveryClassification:
    """Verify recovery field on AdCPError and all subclasses."""

    def test_base_error_defaults_to_terminal(self):
        """AdCPError base class defaults to recovery='terminal'."""
        from src.core.exceptions import AdCPError

        exc = AdCPError("something broke")
        assert exc.recovery == "terminal"

    def test_validation_error_defaults_to_correctable(self):
        """AdCPValidationError defaults to recovery='correctable'."""
        from src.core.exceptions import AdCPValidationError

        exc = AdCPValidationError("invalid field")
        assert exc.recovery == "correctable"

    def test_authentication_error_defaults_to_terminal(self):
        """AdCPAuthenticationError defaults to recovery='terminal'."""
        from src.core.exceptions import AdCPAuthenticationError

        exc = AdCPAuthenticationError("bad token")
        assert exc.recovery == "terminal"

    def test_authorization_error_defaults_to_terminal(self):
        """AdCPAuthorizationError defaults to recovery='terminal'."""
        from src.core.exceptions import AdCPAuthorizationError

        exc = AdCPAuthorizationError("forbidden")
        assert exc.recovery == "terminal"

    def test_not_found_error_defaults_to_terminal(self):
        """AdCPNotFoundError (the *base*) defaults to recovery='terminal'.

        Specific typed subclasses (``AdCPMediaBuyNotFoundError``,
        ``AdCPPackageNotFoundError``) override to ``correctable`` because the
        buyer holds the lever — they can re-issue with the right id. The base
        keeps ``terminal`` for genuinely-gone resources without a known
        recovery path.
        """
        from src.core.exceptions import AdCPNotFoundError

        exc = AdCPNotFoundError("resource missing")
        assert exc.recovery == "terminal"

    def test_media_buy_not_found_error_defaults_to_correctable(self):
        """AdCPMediaBuyNotFoundError overrides base to recovery='correctable'."""
        from src.core.exceptions import AdCPMediaBuyNotFoundError

        exc = AdCPMediaBuyNotFoundError("media buy mb_xyz not found")
        assert exc.recovery == "correctable"

    def test_package_not_found_error_defaults_to_correctable(self):
        """AdCPPackageNotFoundError overrides base to recovery='correctable'."""
        from src.core.exceptions import AdCPPackageNotFoundError

        exc = AdCPPackageNotFoundError("package pkg_xyz not found")
        assert exc.recovery == "correctable"

    def test_context_not_found_error_wire_contract(self):
        """AdCPContextNotFoundError → 404, SESSION_NOT_FOUND, correctable, passthrough wire code."""
        from src.core.exceptions import AdCPContextNotFoundError, translate_error_code

        exc = AdCPContextNotFoundError("Context not found: ctx_x", field="context_id")
        assert exc.status_code == 404
        assert exc.error_code == "SESSION_NOT_FOUND"
        assert exc.recovery == "correctable"
        # SESSION_NOT_FOUND is a standard SDK code → passes through untranslated to the wire.
        assert translate_error_code(exc.error_code) == "SESSION_NOT_FOUND"
        assert exc.field == "context_id"

    def test_rate_limit_error_defaults_to_transient(self):
        """AdCPRateLimitError defaults to recovery='transient'."""
        from src.core.exceptions import AdCPRateLimitError

        exc = AdCPRateLimitError("too many requests")
        assert exc.recovery == "transient"

    def test_adapter_error_defaults_to_transient(self):
        """AdCPAdapterError defaults to recovery='transient'."""
        from src.core.exceptions import AdCPAdapterError

        exc = AdCPAdapterError("GAM unavailable")
        assert exc.recovery == "transient"

    def test_conflict_error_defaults_to_correctable(self):
        """AdCPConflictError defaults to recovery='correctable'."""
        from src.core.exceptions import AdCPConflictError

        exc = AdCPConflictError("duplicate idempotency key")
        assert exc.recovery == "correctable"

    def test_idempotency_conflict_defaults_to_correctable(self):
        """AdCPIdempotencyConflictError is recovery='correctable'.

        The buyer can fix the conflict — resend the original bytes under the
        same key, or mint a fresh key for the new payload (AdCP 3.0.1 prose
        example + storyboard expectation).
        """
        from src.core.exceptions import AdCPIdempotencyConflictError

        exc = AdCPIdempotencyConflictError("dup key, different payload")
        assert exc.recovery == "correctable"

    def test_gone_error_defaults_to_correctable(self):
        """AdCPGoneError defaults to recovery='correctable'.

        Resource is gone, but the buyer can recover by referencing a fresh
        resource (new proposal, new media buy) and re-issuing the request.
        """
        from src.core.exceptions import AdCPGoneError

        exc = AdCPGoneError("proposal expired")
        assert exc.recovery == "correctable"

    def test_account_payment_required_error_defaults_to_terminal(self):
        """AdCPAccountPaymentRequiredError defaults to recovery='terminal'.

        From the sales agent's perspective there is no in-band remediation —
        the buyer must settle the outstanding balance externally before
        resubmitting. Matches the BDD storyboard contract for UC-002
        account-reference partition/boundary rows.
        """
        from src.core.exceptions import AdCPAccountPaymentRequiredError

        exc = AdCPAccountPaymentRequiredError("invoice overdue")
        assert exc.recovery == "terminal"

    def test_budget_exhausted_error_defaults_to_correctable(self):
        """AdCPBudgetExhaustedError defaults to recovery='correctable'.

        Buyer can fix by increasing budget or adjusting spend caps.
        Covers: salesagent-u60m (PR #1083 review)
        """
        from src.core.exceptions import AdCPBudgetExhaustedError

        exc = AdCPBudgetExhaustedError("budget limit reached")
        assert exc.recovery == "correctable"

    def test_service_unavailable_error_defaults_to_transient(self):
        """AdCPServiceUnavailableError defaults to recovery='transient'."""
        from src.core.exceptions import AdCPServiceUnavailableError

        exc = AdCPServiceUnavailableError("product temporarily unavailable")
        assert exc.recovery == "transient"

    def test_recovery_can_be_overridden_per_instance(self):
        """Callers can override recovery for specific raise sites."""
        from src.core.exceptions import AdCPValidationError

        exc = AdCPValidationError("permanent schema mismatch", recovery="terminal")
        assert exc.recovery == "terminal"

    def test_to_dict_includes_recovery(self):
        """to_dict() must include recovery field in serialized output."""
        from src.core.exceptions import AdCPValidationError

        exc = AdCPValidationError("bad field", details={"field": "name"})
        d = exc.to_dict()
        assert "recovery" in d
        assert d["recovery"] == "correctable"

    def test_to_dict_includes_overridden_recovery(self):
        """to_dict() must serialize overridden recovery value."""
        from src.core.exceptions import AdCPAdapterError

        exc = AdCPAdapterError("permanent config error", recovery="terminal")
        d = exc.to_dict()
        assert d["recovery"] == "terminal"


# ---------------------------------------------------------------------------
# FastAPI Exception Handler Tests
# ---------------------------------------------------------------------------


from tests.helpers import assert_envelope_shape  # noqa: E402


@pytest.fixture(scope="module")
def exc_handler_test_app():
    """Minimal isolated FastAPI app with AdCPError exception handler.

    Uses a dedicated app (not the global production app) so tests are
    ordering-independent: with pytest-randomly the global app may already have
    admin catch-all mounts installed via lifespan, which would swallow routes
    added dynamically after startup.
    """
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from starlette.requests import Request

    from src.core.exceptions import (
        AdCPAdapterError,
        AdCPAuthenticationError,
        AdCPBudgetExhaustedError,
        AdCPConflictError,
        AdCPContextNotFoundError,
        AdCPCreativeNotFoundError,
        AdCPError,
        AdCPFormatNotFoundError,
        AdCPGoneError,
        AdCPNotFoundError,
        AdCPServiceUnavailableError,
        AdCPTaskNotFoundError,
        AdCPValidationError,
        build_two_layer_error_envelope,
    )

    _app = FastAPI()

    @_app.exception_handler(AdCPError)
    async def adcp_error_handler(request: Request, exc: AdCPError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=build_two_layer_error_envelope(exc),
        )

    @_app.get("/test-exc/validation")
    def raise_validation():
        raise AdCPValidationError("test validation error")

    @_app.get("/test-exc/auth")
    def raise_auth():
        raise AdCPAuthenticationError("bad token")

    @_app.get("/test-exc/notfound")
    def raise_not_found():
        raise AdCPNotFoundError("resource gone")

    @_app.get("/test-exc/context-not-found")
    def raise_context_not_found():
        raise AdCPContextNotFoundError("Context not found: ctx_x")

    @_app.get("/test-exc/creative-not-found")
    def raise_creative_not_found():
        raise AdCPCreativeNotFoundError("Creative not found: cr_x")

    @_app.get("/test-exc/format-not-found")
    def raise_format_not_found():
        raise AdCPFormatNotFoundError("Unknown format_id 'display_300x250'")

    @_app.get("/test-exc/task-not-found")
    def raise_task_not_found():
        raise AdCPTaskNotFoundError("Task nonexistent not found")

    @_app.get("/test-exc/adapter")
    def raise_adapter():
        raise AdCPAdapterError("GAM down")

    @_app.get("/test-exc/conflict")
    def raise_conflict():
        raise AdCPConflictError("duplicate key")

    @_app.get("/test-exc/gone")
    def raise_gone():
        raise AdCPGoneError("proposal expired")

    @_app.get("/test-exc/budget")
    def raise_budget():
        raise AdCPBudgetExhaustedError("budget limit reached")

    @_app.get("/test-exc/unavailable")
    def raise_unavailable():
        raise AdCPServiceUnavailableError("product temporarily unavailable")

    @_app.get("/test-exc/envelope")
    def raise_with_details():
        raise AdCPValidationError("bad", details={"field": "x"})

    @_app.get("/test-exc/with-context")
    def raise_with_context():
        from adcp.types import ContextObject

        raise AdCPValidationError("bad", context=ContextObject(correlation_id="trace-xyz"))

    return _app


class TestFastAPIExceptionHandlers:
    """Verify FastAPI exception handlers return correct HTTP responses.

    The body is the AdCP spec-compliant two-layer envelope::

        {
            "adcp_error": {"code": "...", "message": "...", "recovery": "..."},
            "errors": [{"code": "...", "message": "...", "recovery": "..."}],
            "context": {...},   # optional, present when raised with context
        }
    """

    def test_validation_error_returns_400(self, exc_handler_test_app):
        """AdCPValidationError raised in a route must return 400."""
        client = TestClient(exc_handler_test_app, raise_server_exceptions=False)
        response = client.get("/test-exc/validation")
        assert response.status_code == 400
        assert_envelope_shape(
            response.json(), "VALIDATION_ERROR", recovery="correctable", message_substr="test validation error"
        )

    def test_authentication_error_returns_401(self, exc_handler_test_app):
        """AdCPAuthenticationError raised in a route must return 401."""
        client = TestClient(exc_handler_test_app, raise_server_exceptions=False)
        response = client.get("/test-exc/auth")
        assert response.status_code == 401
        # AdCPAuthenticationError.error_code = "AUTH_TOKEN_INVALID" (spec STANDARD code,
        # passthrough — not in ERROR_CODE_MAPPING). Wire emits AUTH_TOKEN_INVALID, not
        # AUTH_REQUIRED (which is for AdCPAuthorizationError).
        assert_envelope_shape(response.json(), "AUTH_TOKEN_INVALID", recovery="terminal")

    def test_not_found_error_returns_404(self, exc_handler_test_app):
        """AdCPNotFoundError raised in a route must return 404 with INVALID_REQUEST wire code.

        The base ``AdCPNotFoundError`` carries the internal ``NOT_FOUND`` code,
        which the boundary translates to ``INVALID_REQUEST`` (STANDARD) so the
        wire stays spec-compliant. Status 404 is preserved. Production code
        should prefer specific subclasses (AdCPMediaBuyNotFoundError, etc.).
        """
        client = TestClient(exc_handler_test_app, raise_server_exceptions=False)
        response = client.get("/test-exc/notfound")
        assert response.status_code == 404
        assert_envelope_shape(response.json(), "INVALID_REQUEST", recovery="terminal")

    def test_context_not_found_error_returns_404(self, exc_handler_test_app):
        """AdCPContextNotFoundError raised in a route must return 404 with SESSION_NOT_FOUND.

        SESSION_NOT_FOUND is a standard SDK code (passthrough, not in
        ERROR_CODE_MAPPING). recovery=correctable: the buyer can supply a valid
        context_id or omit it for a fresh context.
        """
        client = TestClient(exc_handler_test_app, raise_server_exceptions=False)
        response = client.get("/test-exc/context-not-found")
        assert response.status_code == 404
        assert_envelope_shape(response.json(), "SESSION_NOT_FOUND", recovery="correctable")

    def test_creative_not_found_error_returns_404(self, exc_handler_test_app):
        """AdCPCreativeNotFoundError → 404, wire INVALID_REQUEST, correctable.

        The internal CREATIVE_NOT_FOUND code translates to INVALID_REQUEST at the
        boundary (ERROR_CODE_MAPPING). recovery=correctable distinguishes it from
        the base AdCPNotFoundError (terminal) — that override is the regression this pins.
        """
        client = TestClient(exc_handler_test_app, raise_server_exceptions=False)
        response = client.get("/test-exc/creative-not-found")
        assert response.status_code == 404
        assert_envelope_shape(response.json(), "INVALID_REQUEST", recovery="correctable")

    def test_format_not_found_error_returns_404(self, exc_handler_test_app):
        """AdCPFormatNotFoundError → 404, wire INVALID_REQUEST, correctable.

        FORMAT_NOT_FOUND translates to INVALID_REQUEST at the boundary;
        recovery=correctable distinguishes it from the base (terminal).
        """
        client = TestClient(exc_handler_test_app, raise_server_exceptions=False)
        response = client.get("/test-exc/format-not-found")
        assert response.status_code == 404
        assert_envelope_shape(response.json(), "INVALID_REQUEST", recovery="correctable")

    def test_task_not_found_error_returns_404(self, exc_handler_test_app):
        """AdCPTaskNotFoundError → 404, wire INVALID_REQUEST, correctable.

        TASK_NOT_FOUND translates to INVALID_REQUEST at the boundary;
        recovery=correctable distinguishes it from the base (terminal).
        """
        client = TestClient(exc_handler_test_app, raise_server_exceptions=False)
        response = client.get("/test-exc/task-not-found")
        assert response.status_code == 404
        assert_envelope_shape(response.json(), "INVALID_REQUEST", recovery="correctable")

    def test_adapter_error_returns_502(self, exc_handler_test_app):
        """AdCPAdapterError raised in a route must return 502."""
        client = TestClient(exc_handler_test_app, raise_server_exceptions=False)
        response = client.get("/test-exc/adapter")
        assert response.status_code == 502
        assert_envelope_shape(response.json(), "SERVICE_UNAVAILABLE", recovery="transient")

    def test_conflict_error_returns_409(self, exc_handler_test_app):
        """AdCPConflictError raised in a route must return 409."""
        client = TestClient(exc_handler_test_app, raise_server_exceptions=False)
        response = client.get("/test-exc/conflict")
        assert response.status_code == 409
        assert_envelope_shape(response.json(), "CONFLICT", recovery="correctable")

    def test_gone_error_returns_410(self, exc_handler_test_app):
        """AdCPGoneError raised in a route must return 410."""
        client = TestClient(exc_handler_test_app, raise_server_exceptions=False)
        response = client.get("/test-exc/gone")
        assert response.status_code == 410
        assert_envelope_shape(response.json(), "INVALID_STATE", recovery="correctable")

    def test_budget_exhausted_error_returns_422(self, exc_handler_test_app):
        """AdCPBudgetExhaustedError raised in a route must return 422."""
        client = TestClient(exc_handler_test_app, raise_server_exceptions=False)
        response = client.get("/test-exc/budget")
        assert response.status_code == 422
        assert_envelope_shape(response.json(), "BUDGET_EXHAUSTED", recovery="correctable")

    def test_service_unavailable_error_returns_503(self, exc_handler_test_app):
        """AdCPServiceUnavailableError raised in a route must return 503."""
        client = TestClient(exc_handler_test_app, raise_server_exceptions=False)
        response = client.get("/test-exc/unavailable")
        assert response.status_code == 503
        assert_envelope_shape(response.json(), "SERVICE_UNAVAILABLE", recovery="transient")

    def test_error_response_has_two_layer_envelope(self, exc_handler_test_app):
        """Error responses use the spec-compliant two-layer envelope shape."""
        client = TestClient(exc_handler_test_app, raise_server_exceptions=False)
        response = client.get("/test-exc/envelope")
        body = response.json()
        assert_envelope_shape(body, "VALIDATION_ERROR", recovery="correctable")
        # Both layers carry recovery
        assert body["adcp_error"]["recovery"] == "correctable"
        assert body["errors"][0]["recovery"] == "correctable"
        # Details propagate into both layers
        assert body["adcp_error"]["details"] == {"field": "x"}
        assert body["errors"][0]["details"] == {"field": "x"}

    def test_error_response_echoes_context(self, exc_handler_test_app):
        """When raised with context, the envelope echoes it (spec 3.0.0)."""
        client = TestClient(exc_handler_test_app, raise_server_exceptions=False)
        response = client.get("/test-exc/with-context")
        body = response.json()
        assert body["context"] == {"correlation_id": "trace-xyz"}
        # The two-layer envelope contains recovery inside each layer.
        assert body["errors"][0]["recovery"] == "correctable"


# ---------------------------------------------------------------------------
# A2A Error Mapping Tests
# ---------------------------------------------------------------------------


class TestNoDeadA2AMap:
    """Dead A2A error map must not exist in exceptions module (PR #1083 review)."""

    def test_no_a2a_error_code_map_in_exceptions(self):
        """_A2A_ERROR_CODE_MAP was dead code — real translation is in adcp_a2a_server.py."""
        import src.core.exceptions as exc_module

        msg = (
            "_A2A_ERROR_CODE_MAP is dead code — A2A translation lives in _build_error_envelope() in adcp_a2a_server.py"
        )
        assert not hasattr(exc_module, "_A2A_ERROR_CODE_MAP"), msg

    def test_no_to_a2a_error_code_in_exceptions(self):
        """to_a2a_error_code() was dead code — real translation is in adcp_a2a_server.py."""
        import src.core.exceptions as exc_module

        msg = (
            "to_a2a_error_code() is dead code — A2A translation lives in _build_error_envelope() in adcp_a2a_server.py"
        )
        assert not hasattr(exc_module, "to_a2a_error_code"), msg


# ---------------------------------------------------------------------------
# Wire-format error code translation (ERROR_CODE_MAPPING)
# ---------------------------------------------------------------------------


class TestErrorCodeWireTranslation:
    """ERROR_CODE_MAPPING translation must apply at every transport boundary.

    Architecture: model layer (``to_dict``, ``to_adcp_error``) preserves the
    raw ``error_code``. Transport boundaries (FastAPI handler, MCP wrapper,
    A2A wrapper) call ``wire_error_code`` / ``translate_error_code()`` to
    emit spec-compliant codes. This keeps the model honest while ensuring
    wire output is always compliant.

    Tests use existing AdCPError instances with a temporarily-overridden
    ``error_code`` instance attribute (no new subclasses — that would trip
    ``test_adcp_error_subclass_codes_are_compliant``).
    """

    def test_translate_mapped_code(self):
        from src.core.exceptions import translate_error_code

        # AUTH_TOKEN_INVALID passes through (spec error code for auth)
        assert translate_error_code("AUTH_TOKEN_INVALID") == "AUTH_TOKEN_INVALID"
        # BUDGET_CEILING_EXCEEDED is mapped to BUDGET_EXCEEDED
        assert translate_error_code("BUDGET_CEILING_EXCEEDED") == "BUDGET_EXCEEDED"
        # RATE_LIMIT_EXCEEDED is mapped to RATE_LIMITED
        assert translate_error_code("RATE_LIMIT_EXCEEDED") == "RATE_LIMITED"

    def test_translate_unmapped_code_passes_through(self):
        from src.core.exceptions import translate_error_code

        assert translate_error_code("VALIDATION_ERROR") == "VALIDATION_ERROR"
        assert translate_error_code("MEDIA_BUY_NOT_FOUND") == "MEDIA_BUY_NOT_FOUND"
        # Genuinely-unmapped codes pass through; INTERNAL_CODES that used to pass
        # through (NOT_FOUND, CONFIGURATION_ERROR, INTERNAL_ERROR) are now
        # explicitly mapped to STANDARD_ERROR_CODES targets — see
        # test_internal_codes_translated_to_wire_safe_codes below.
        assert translate_error_code("SOME_UNKNOWN_CODE_THAT_IS_NOT_MAPPED") == "SOME_UNKNOWN_CODE_THAT_IS_NOT_MAPPED"

    def test_internal_codes_translated_to_wire_safe_codes(self):
        """Base-class codes that should never reach the wire are translated to STANDARD targets.

        Catches accidental leaks: AdCPError, AdCPNotFoundError, AdCPConfigurationError
        instances that escape to the boundary now produce STANDARD_ERROR_CODES output
        instead of the previously-leaking internal codes.
        """
        from adcp.server.helpers import STANDARD_ERROR_CODES

        from src.core.exceptions import INTERNAL_CODES, translate_error_code

        # Every INTERNAL_CODES entry that could plausibly reach a buyer either:
        #   (a) has an explicit translation to a STANDARD code, OR
        #   (b) is documented as adapter-internal (never raised at the boundary).
        wire_safe = {
            "NOT_FOUND": "INVALID_REQUEST",
            "INTERNAL_ERROR": "SERVICE_UNAVAILABLE",
            "CONFIGURATION_ERROR": "SERVICE_UNAVAILABLE",
        }
        for internal, expected_wire in wire_safe.items():
            assert internal in INTERNAL_CODES, f"{internal} should be in INTERNAL_CODES"
            assert translate_error_code(internal) == expected_wire
            assert expected_wire in STANDARD_ERROR_CODES

    def test_wire_error_code_property_translates(self):
        """``wire_error_code`` exposes the translated code on an instance."""
        from src.core.exceptions import AdCPError

        # Override on an instance — does NOT create a new subclass, so this
        # avoids tripping the AdCPError subclass compliance guard.
        exc = AdCPError("over budget")
        exc.error_code = "BUDGET_CEILING_EXCEEDED"
        assert exc.wire_error_code == "BUDGET_EXCEEDED"

    def test_to_dict_preserves_raw_error_code(self):
        """Model serialization preserves the raw ``error_code`` (translation at boundary)."""
        from src.core.exceptions import AdCPError

        exc = AdCPError("over budget")
        exc.error_code = "BUDGET_CEILING_EXCEEDED"
        assert exc.to_dict()["error_code"] == "BUDGET_CEILING_EXCEEDED"

    def test_to_adcp_error_preserves_raw_error_code(self):
        """Model envelope preserves the raw ``error_code`` (translation at boundary)."""
        from src.core.exceptions import AdCPError

        exc = AdCPError("slow down")
        exc.error_code = "RATE_LIMIT_EXCEEDED"
        assert exc.to_adcp_error()["errors"][0]["code"] == "RATE_LIMIT_EXCEEDED"


class TestIterConcreteSubclasses:
    """Lock the contract of AdCPError.iter_concrete_subclasses().

    The wire-code -> HTTP-status table (_build_error_code_to_status) and the
    error-code compliance tests depend on this walk visiting every transitive
    subclass exactly once. The two consumer tests iterate it but never pin the
    transitivity / dedup / self-exclusion behavior, so a regression there would
    go unnoticed.
    """

    def test_yields_transitive_descendants_once_excluding_cls(self):
        """Generic walk: transitive, deduplicated across diamonds, never yields cls."""
        from src.core.exceptions import AdCPError

        # Exercise the underlying function with a local root so AdCPError's real
        # subclass tree stays untouched — subclassing AdCPError here would leak
        # these throwaway classes into every other test that enumerates it.
        walk = AdCPError.iter_concrete_subclasses.__func__

        class _Base: ...

        class _Mid(_Base): ...

        class _Leaf(_Mid): ...

        class _Other(_Base): ...

        class _Diamond(_Mid, _Other): ...  # reachable via both _Mid and _Other

        result = list(walk(_Base))

        # Transitive: the grandchild (_Leaf) and the diamond are reached, not
        # just the direct children.
        assert set(result) == {_Mid, _Leaf, _Other, _Diamond}
        # Deduplicated despite two parent paths to _Diamond.
        assert result.count(_Diamond) == 1
        # Never yields the class it was called on.
        assert _Base not in result

    def test_real_tree_is_transitive_and_excludes_base(self):
        """On the real hierarchy: a two-level-deep subclass is yielded, the base is not."""
        from src.core.exceptions import AdCPError, AdCPProductNotFoundError

        concrete = set(AdCPError.iter_concrete_subclasses())

        # AdCPError -> AdCPNotFoundError -> AdCPProductNotFoundError (transitive).
        assert AdCPProductNotFoundError in concrete
        assert AdCPError not in concrete

    def test_skips_abstract_bases_yields_concrete_descendants(self):
        """Abstract bases are walked through but not yielded — the 'concrete' promise."""
        import abc

        from src.core.exceptions import AdCPError

        walk = AdCPError.iter_concrete_subclasses.__func__

        class _Root: ...

        class _AbstractMid(_Root, abc.ABC):
            @abc.abstractmethod
            def handle(self) -> None: ...

        class _Concrete(_AbstractMid):
            def handle(self) -> None: ...

        result = list(walk(_Root))

        assert _Concrete in result  # concrete descendant of an abstract base is yielded
        assert _AbstractMid not in result  # the abstract base itself is skipped
