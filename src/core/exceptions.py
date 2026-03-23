"""AdCP exception hierarchy for typed error handling across transport layers.

Business logic raises these exceptions. Transport layers (A2A, MCP, REST)
translate them to their protocol's error format via registered handlers.

Exception classes define the error vocabulary — transport layers format them.
Each exception carries a recovery classification (transient/correctable/terminal)
to help buyer agents decide whether to retry, fix, or abandon a request.
"""

from __future__ import annotations

from typing import Any, Literal

RecoveryHint = Literal["transient", "correctable", "terminal"]


class AdCPError(Exception):
    """Base exception for all AdCP errors.

    Attributes:
        message: Human-readable error description.
        status_code: HTTP status code for REST/FastAPI responses.
        error_code: Machine-readable error code string.
        recovery: Recovery classification for buyer agents.
        details: Optional structured error details.
    """

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"
    recovery: RecoveryHint = "terminal"

    def __init__(
        self,
        message: str = "",
        *,
        details: dict[str, Any] | None = None,
        recovery: RecoveryHint | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        if recovery is not None:
            self.recovery = recovery

    def to_dict(self) -> dict[str, Any]:
        """Serialize to response body dict."""
        result: dict[str, Any] = {
            "error_code": self.error_code,
            "message": self.message,
            "recovery": self.recovery,
        }
        if self.details is not None:
            result["details"] = self.details
        else:
            result["details"] = None
        return result


class AdCPValidationError(AdCPError):
    """Invalid parameters or request data (400)."""

    status_code = 400
    error_code = "VALIDATION_ERROR"
    recovery: RecoveryHint = "correctable"


class AdCPAuthenticationError(AdCPError):
    """Missing or invalid authentication credentials (401)."""

    status_code = 401
    error_code = "AUTH_TOKEN_INVALID"


class AdCPAuthorizationError(AdCPError):
    """Authenticated but not authorized for this resource (403)."""

    status_code = 403
    error_code = "AUTHORIZATION_ERROR"


class AdCPNotFoundError(AdCPError):
    """Requested resource does not exist (404)."""

    status_code = 404
    error_code = "NOT_FOUND"


class AdCPAccountNotFoundError(AdCPNotFoundError):
    """Account not found by ID or natural key (404, ACCOUNT_NOT_FOUND)."""

    error_code = "ACCOUNT_NOT_FOUND"


class AdCPAccountSetupRequiredError(AdCPError):
    """Account exists but requires setup before use (422, ACCOUNT_SETUP_REQUIRED)."""

    status_code = 422
    error_code = "ACCOUNT_SETUP_REQUIRED"
    recovery: RecoveryHint = "correctable"


class AdCPAccountSuspendedError(AdCPError):
    """Account is suspended and cannot be used (403, ACCOUNT_SUSPENDED)."""

    status_code = 403
    error_code = "ACCOUNT_SUSPENDED"


class AdCPAccountPaymentRequiredError(AdCPError):
    """Account has outstanding payment requirements (402, ACCOUNT_PAYMENT_REQUIRED)."""

    status_code = 402
    error_code = "ACCOUNT_PAYMENT_REQUIRED"


class AdCPConflictError(AdCPError):
    """Resource conflict, e.g. duplicate idempotency key (409)."""

    status_code = 409
    error_code = "CONFLICT"
    recovery: RecoveryHint = "correctable"


class AdCPAccountAmbiguousError(AdCPConflictError):
    """Natural key matches multiple accounts (409, ACCOUNT_AMBIGUOUS)."""

    error_code = "ACCOUNT_AMBIGUOUS"


class AdCPGoneError(AdCPError):
    """Resource previously existed but is no longer available (410)."""

    status_code = 410
    error_code = "GONE"


class AdCPBudgetExhaustedError(AdCPError):
    """Budget or spend limit has been reached (422)."""

    status_code = 422
    error_code = "BUDGET_EXHAUSTED"
    recovery: RecoveryHint = "correctable"


class AdCPRateLimitError(AdCPError):
    """Too many requests (429)."""

    status_code = 429
    error_code = "RATE_LIMIT_EXCEEDED"
    recovery: RecoveryHint = "transient"


class AdCPAdapterError(AdCPError):
    """External adapter (GAM, etc.) failure (502)."""

    status_code = 502
    error_code = "ADAPTER_ERROR"
    recovery: RecoveryHint = "transient"


class AdCPServiceUnavailableError(AdCPError):
    """Service or product temporarily unavailable (503)."""

    status_code = 503
    error_code = "SERVICE_UNAVAILABLE"
    recovery: RecoveryHint = "transient"
