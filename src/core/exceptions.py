"""AdCP exception hierarchy for typed error handling across transport layers.

Business logic raises these exceptions. Transport layers (A2A, MCP, REST)
translate them to their protocol's error format via registered handlers.

Exception classes define the error vocabulary — transport layers format them.
Each exception carries a recovery classification (transient/correctable/terminal)
to help buyer agents decide whether to retry, fix, or abandon a request.
"""

from __future__ import annotations

from typing import Any, Literal

from adcp.server.helpers import STANDARD_ERROR_CODES, adcp_error

RecoveryHint = Literal["transient", "correctable", "terminal"]

# ---------------------------------------------------------------------------
# Error-code compliance: mapping non-standard codes to SDK equivalents
# ---------------------------------------------------------------------------
# Every code that reaches the wire (buyer agent) MUST be in
# STANDARD_ERROR_CODES.  Codes in ERROR_CODE_MAPPING are translated at the
# transport boundary; codes in INTERNAL_CODES never leave the server.

ERROR_CODE_MAPPING: dict[str, str] = {
    # Authentication / authorisation
    "AUTH_TOKEN_INVALID": "AUTH_REQUIRED",
    "AUTHORIZATION_ERROR": "AUTH_REQUIRED",
    "PRINCIPAL_ID_MISSING": "AUTH_REQUIRED",
    "PRINCIPAL_NOT_FOUND": "AUTH_REQUIRED",
    "INSUFFICIENT_PRIVILEGES": "AUTH_REQUIRED",
    # Validation (field-level)
    "INVALID_DATE_RANGE": "VALIDATION_ERROR",
    "INVALID_DATETIME": "VALIDATION_ERROR",
    "INVALID_CONFIGURATION": "VALIDATION_ERROR",
    "INVALID_BUDGET": "VALIDATION_ERROR",
    "INVALID_PLACEMENT_IDS": "VALIDATION_ERROR",
    "INVALID_DOMAIN": "VALIDATION_ERROR",
    "MISSING_PACKAGE_ID": "VALIDATION_ERROR",
    "MISSING_BUDGET": "VALIDATION_ERROR",
    "MISSING_IMPRESSIONS": "VALIDATION_ERROR",
    "MISSING_PLATFORM_ID": "VALIDATION_ERROR",
    "NO_ZONES_CONFIGURED": "VALIDATION_ERROR",
    "APPROVAL_REQUIRED": "VALIDATION_ERROR",
    # Budget
    "BUDGET_CEILING_EXCEEDED": "BUDGET_EXCEEDED",
    "BUDGET_BELOW_DELIVERY": "BUDGET_EXCEEDED",
    # Feature support
    "CURRENCY_NOT_SUPPORTED": "UNSUPPORTED_FEATURE",
    "UNSUPPORTED_PRICING_MODEL": "UNSUPPORTED_FEATURE",
    "UNSUPPORTED_TARGETING": "UNSUPPORTED_FEATURE",
    "PLACEMENT_TARGETING_NOT_SUPPORTED": "UNSUPPORTED_FEATURE",
    "UNSUPPORTED_ACTION": "UNSUPPORTED_FEATURE",
    "BILLING_NOT_SUPPORTED": "UNSUPPORTED_FEATURE",
    # Resource lookup
    "NO_PACKAGES_FOUND": "PACKAGE_NOT_FOUND",
    # Resource state
    "GONE": "INVALID_STATE",
    # Availability / adapter
    "RATE_LIMIT_EXCEEDED": "RATE_LIMITED",
    "ADAPTER_ERROR": "SERVICE_UNAVAILABLE",
    "ACTIVATION_ERROR": "SERVICE_UNAVAILABLE",
    "ACTIVATION_FAILED": "SERVICE_UNAVAILABLE",
    "CREATIVE_SYNC_FAILED": "SERVICE_UNAVAILABLE",
    "PARTIAL_FAILURE": "SERVICE_UNAVAILABLE",
    "PRODUCT_NOT_CONFIGURED": "PRODUCT_UNAVAILABLE",
    "CREATIVES_NOT_FOUND": "CREATIVE_REJECTED",
}

# Internal-only codes: never reach the buyer agent.  Each entry has a
# justification for why it is internal.
INTERNAL_CODES: frozenset[str] = frozenset(
    {
        "INTERNAL_ERROR",  # Base-class default; never instantiated for wire
        "NOT_FOUND",  # Base-class for entity-specific NotFound subclasses
        "CONFIGURATION_ERROR",  # Server-side config; needs admin, not buyer
        "API_ERROR",  # Raw adapter API failure detail
        "WORKFLOW_CREATION_FAILED",  # GAM workflow orchestration detail
        "LINE_ITEM_CREATION_FAILED",  # GAM line-item creation detail
        "FLIGHT_NOT_FOUND",  # Kevel/Triton internal flight lookup
        "ACTIVATION_WORKFLOW_FAILED",  # GAM activation workflow detail
        "API_UPDATE_FAILED",  # Broadstreet API update detail
        "GAM_UPDATE_FAILED",  # GAM update API detail
    }
)

# Sanity check: every mapping target must be a standard code.
assert all(v in STANDARD_ERROR_CODES for v in ERROR_CODE_MAPPING.values()), (
    "ERROR_CODE_MAPPING contains non-standard target codes"
)


def translate_error_code(code: str) -> str:
    """Translate a server-side error code to its wire-compliant equivalent.

    Codes listed in ERROR_CODE_MAPPING are translated to their standard SDK
    counterpart. All other codes pass through unchanged — codes are only
    rewritten when there is an explicit mapping entry. Compliance is
    enforced separately by the architecture guard.
    """
    return ERROR_CODE_MAPPING.get(code, code)


class AdCPError(Exception):
    """Base exception for all AdCP errors.

    Attributes:
        message: Human-readable error description.
        status_code: HTTP status code for REST/FastAPI responses.
        error_code: Machine-readable error code string.
        recovery: Recovery classification for buyer agents.
        details: Optional structured error details.
        field: Optional field name that caused the error.
        suggestion: Optional correction hint for buyer agents.
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
        field: str | None = None,
        suggestion: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        self.field = field
        self.suggestion = suggestion
        if recovery is not None:
            self.recovery = recovery

    @property
    def wire_error_code(self) -> str:
        """Wire-safe error code (translated through ERROR_CODE_MAPPING).

        Used by transport-layer code that serializes errors to the wire.
        Model methods (``to_dict``, ``to_adcp_error``) preserve the original
        ``error_code`` so internal callers see the raw source code; transport
        boundaries are responsible for calling this property when emitting
        a response.
        """
        return translate_error_code(self.error_code)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to flat response body dict (legacy format).

        Returns a flat dict with the raw ``error_code``. Transport boundary
        handlers (FastAPI exception handler, MCP wrapper, A2A wrapper) are
        responsible for translating to wire-compliant codes via
        ``translate_error_code()`` or ``wire_error_code``.
        """
        result: dict[str, Any] = {
            "error_code": self.error_code,
            "message": self.message,
            "recovery": self.recovery,
            "details": self.details,
        }
        if self.field is not None:
            result["field"] = self.field
        if self.suggestion is not None:
            result["suggestion"] = self.suggestion
        return result

    def to_adcp_error(self) -> dict[str, Any]:
        """Serialize to AdCP spec-compliant ``{"errors": [...]}`` format.

        Uses ``adcp_error()`` from the SDK to produce the canonical error
        envelope. Translation to ``STANDARD_ERROR_CODES`` happens at transport
        boundaries via ``translate_error_code()`` — this method preserves the
        raw ``error_code`` so internal callers retain the source classification.
        """
        return adcp_error(
            self.error_code,
            self.message,
            recovery=self.recovery,
            field=self.field,
            suggestion=self.suggestion,
            details=self.details,
        )


class AdCPValidationError(AdCPError):
    """Invalid parameters or request data (400)."""

    status_code = 400
    error_code = "VALIDATION_ERROR"
    recovery: RecoveryHint = "correctable"


class AdCPAuthenticationError(AdCPError):
    """Missing or invalid authentication credentials (401)."""

    status_code = 401
    error_code = "AUTH_REQUIRED"


class AdCPAuthorizationError(AdCPError):
    """Authenticated but not authorized for this resource (403)."""

    status_code = 403
    error_code = "AUTH_REQUIRED"


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
    error_code = "INVALID_STATE"


class AdCPBudgetExhaustedError(AdCPError):
    """Budget or spend limit has been reached (422)."""

    status_code = 422
    error_code = "BUDGET_EXHAUSTED"
    recovery: RecoveryHint = "correctable"


class AdCPRateLimitError(AdCPError):
    """Too many requests (429)."""

    status_code = 429
    error_code = "RATE_LIMITED"
    recovery: RecoveryHint = "transient"


class AdCPAdapterError(AdCPError):
    """External adapter (GAM, etc.) failure (502)."""

    status_code = 502
    error_code = "SERVICE_UNAVAILABLE"
    recovery: RecoveryHint = "transient"


class AdCPConfigurationError(AdCPError):
    """Server-side configuration is broken (500).

    Raised when encrypted secrets cannot be decrypted (key rotation,
    corruption, missing ENCRYPTION_KEY). Callers should NOT silently
    fall back — the configuration needs admin intervention.
    """

    status_code = 500
    error_code = "CONFIGURATION_ERROR"
    recovery: RecoveryHint = "correctable"


class AdCPServiceUnavailableError(AdCPError):
    """Service or product temporarily unavailable (503)."""

    status_code = 503
    error_code = "SERVICE_UNAVAILABLE"
    recovery: RecoveryHint = "transient"
