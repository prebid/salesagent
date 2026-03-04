"""AdCP exception hierarchy for typed error handling across transport layers.

Business logic raises these exceptions. Transport layers (A2A, MCP, REST)
translate them to their protocol's error format via registered handlers.

Exception classes define the error vocabulary — transport layers format them.
"""

from __future__ import annotations

from typing import Any


class AdCPError(Exception):
    """Base exception for all AdCP errors.

    Attributes:
        message: Human-readable error description.
        status_code: HTTP status code for REST/FastAPI responses.
        error_code: Machine-readable error code string.
        details: Optional structured error details.
    """

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str = "",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        """Serialize to response body dict."""
        result: dict[str, Any] = {
            "error_code": self.error_code,
            "message": self.message,
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


class AdCPAuthenticationError(AdCPError):
    """Missing or invalid authentication credentials (401)."""

    status_code = 401
    error_code = "AUTHENTICATION_ERROR"


class AdCPAuthorizationError(AdCPError):
    """Authenticated but not authorized for this resource (403)."""

    status_code = 403
    error_code = "AUTHORIZATION_ERROR"


class AdCPNotFoundError(AdCPError):
    """Requested resource does not exist (404)."""

    status_code = 404
    error_code = "NOT_FOUND"


class AdCPRateLimitError(AdCPError):
    """Too many requests (429)."""

    status_code = 429
    error_code = "RATE_LIMIT_EXCEEDED"


class AdCPAdapterError(AdCPError):
    """External adapter (GAM, etc.) failure (502)."""

    status_code = 502
    error_code = "ADAPTER_ERROR"


# ---------------------------------------------------------------------------
# Transport mapping utilities
# ---------------------------------------------------------------------------

# A2A SDK JSON-RPC error codes
_A2A_ERROR_CODE_MAP: dict[type[AdCPError], int] = {
    AdCPValidationError: -32602,  # InvalidParamsError
    AdCPAuthenticationError: -32600,  # InvalidRequestError
    AdCPAuthorizationError: -32600,  # InvalidRequestError
    AdCPNotFoundError: -32001,  # TaskNotFoundError
    AdCPRateLimitError: -32603,  # InternalError
    AdCPAdapterError: -32603,  # InternalError
}


def to_a2a_error_code(exc: AdCPError) -> int:
    """Map an AdCP exception to its A2A SDK JSON-RPC error code."""
    return _A2A_ERROR_CODE_MAP.get(type(exc), -32603)
