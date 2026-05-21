"""AdCP exception hierarchy for typed error handling across transport layers.

Business logic raises these exceptions. Transport layers (A2A, MCP, REST)
translate them to their protocol's error format via registered handlers.

Exception classes define the error vocabulary — transport layers format them.
Each exception carries a recovery classification (transient/correctable/terminal)
to help buyer agents decide whether to retry, fix, or abandon a request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from adcp.server.helpers import STANDARD_ERROR_CODES, adcp_error

if TYPE_CHECKING:
    from adcp.types import ContextObject

RecoveryHint = Literal["transient", "correctable", "terminal"]

# ---------------------------------------------------------------------------
# Error-code compliance: mapping non-standard codes to SDK equivalents
# ---------------------------------------------------------------------------
# Every code that reaches the wire (buyer agent) MUST be in
# STANDARD_ERROR_CODES.  Codes in ERROR_CODE_MAPPING are translated at the
# transport boundary; codes in INTERNAL_CODES never leave the server.

ERROR_CODE_MAPPING: dict[str, str] = {
    # Internal-only codes that occasionally leak to the wire when a raise site
    # uses a base class (AdCPError / AdCPNotFoundError / AdCPConfigurationError)
    # instead of a specific subclass. Mapped to the closest STANDARD_ERROR_CODES
    # entry so the wire stays spec-compliant. PR 2 cleanup migrates the raise
    # sites to specific subclasses; the mappings stay as a safety net.
    "NOT_FOUND": "INVALID_REQUEST",
    "INTERNAL_ERROR": "SERVICE_UNAVAILABLE",
    "CONFIGURATION_ERROR": "SERVICE_UNAVAILABLE",
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


def _serialize_context(
    context: ContextObject | dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Serialize an AdCP ContextObject (or dict) into a JSON-safe dict.

    Single source of truth for context serialization — used by ``to_dict``,
    ``to_adcp_error``, and ``build_two_layer_error_envelope`` so all three
    paths emit byte-identical context payloads.

    Behavior:
        - ``None`` → ``None`` (caller decides whether to omit the key).
        - ``dict`` → shallow copy. Prevents aliasing footguns when one
          serialization layer mutates its copy and accidentally mutates
          the source context still held on the exception.
        - ``ContextObject`` → ``model_dump(mode="json", exclude_none=True)``.
          ``mode="json"`` coerces datetimes/UUIDs/etc. to JSON-serializable
          primitives; ``exclude_none=True`` matches the spec's emit-only-
          populated-fields norm.
    """
    if context is None:
        return None
    if isinstance(context, dict):
        return dict(context)
    return context.model_dump(mode="json", exclude_none=True)


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
        context: Optional AdCP ContextObject (or dict) echoed in the
            envelope so buyer agents can correlate failures to the
            request that produced them (spec 3.0.6 normative).
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
        context: ContextObject | dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        self.field = field
        self.suggestion = suggestion
        self.context = context
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

        Includes ``context`` when present so callers building advisory
        payloads (audit logging, retry-loop diagnostics) have the same
        request-correlation envelope key the two-layer wire shape exposes.
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
        serialized_context = _serialize_context(self.context)
        if serialized_context is not None:
            result["context"] = serialized_context
        return result

    def to_adcp_error(self) -> dict[str, Any]:
        """Serialize to AdCP spec-compliant ``{"errors": [...]}`` format.

        Uses ``adcp_error()`` from the SDK to produce the canonical error
        envelope. Translation to ``STANDARD_ERROR_CODES`` happens at transport
        boundaries via ``translate_error_code()`` — this method preserves the
        raw ``error_code`` so internal callers retain the source classification.

        ``context`` flows into ``details["context"]`` so the SDK helper
        doesn't drop request-correlation data on the floor.

        .. deprecated::
            Effectively legacy now that ``build_two_layer_error_envelope()``
            is the single source of truth for the wire envelope. Prefer the
            envelope builder for any new code path. This method intentionally
            differs in shape — ``context`` is nested under ``details`` here
            but appears at the top level in the two-layer envelope — and is
            retained only for non-envelope callers (audit logging, SDK
            interop) that still want the flat ``{"errors": [...]}`` payload.
        """
        merged_details = dict(self.details) if self.details else {}
        serialized_context = _serialize_context(self.context)
        if serialized_context is not None:
            merged_details.setdefault("context", serialized_context)
        return adcp_error(
            self.error_code,
            self.message,
            recovery=self.recovery,
            field=self.field,
            suggestion=self.suggestion,
            details=merged_details or None,
        )


class AdCPValidationError(AdCPError):
    """Invalid parameters or request data (400)."""

    status_code = 400
    error_code = "VALIDATION_ERROR"
    recovery: RecoveryHint = "correctable"


class AdCPAuthenticationError(AdCPError):
    """Missing or invalid authentication credentials (401).

    Recovery defaults to ``terminal`` to match
    ``STANDARD_ERROR_CODES["AUTH_REQUIRED"]["recovery"]`` in adcp 4.3 (the SDK
    we run). AdCP spec 3.0.4 (CHANGELOG ``78b1dc4``) reclassified AUTH_REQUIRED
    to ``correctable`` (re-auth recovers); pending the SDK upgrade we keep
    ``terminal`` so wire output matches the installed SDK's expectation.
    """

    status_code = 401
    error_code = "AUTH_REQUIRED"


class AdCPAuthorizationError(AdCPError):
    """Authenticated but not authorized for this resource (403).

    Same ``terminal`` default as ``AdCPAuthenticationError`` for the same
    SDK-vs-spec mismatch reason — see that class's docstring.
    """

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
    """Account has outstanding payment requirements (402, ACCOUNT_PAYMENT_REQUIRED).

    Recovery=correctable: the buyer can resolve by settling the outstanding
    balance (or the seller can re-activate the account) and retry.
    """

    status_code = 402
    error_code = "ACCOUNT_PAYMENT_REQUIRED"
    recovery: RecoveryHint = "correctable"


class AdCPConflictError(AdCPError):
    """Resource conflict, e.g. duplicate idempotency key (409)."""

    status_code = 409
    error_code = "CONFLICT"
    recovery: RecoveryHint = "correctable"


class AdCPAccountAmbiguousError(AdCPConflictError):
    """Natural key matches multiple accounts (409, ACCOUNT_AMBIGUOUS)."""

    error_code = "ACCOUNT_AMBIGUOUS"


class AdCPGoneError(AdCPError):
    """Resource previously existed but is no longer available (410).

    Recovery=correctable: the resource itself is gone, but the buyer can
    recover by referencing a different resource (a fresh proposal, a new
    media buy) and re-issuing the request.
    """

    status_code = 410
    error_code = "INVALID_STATE"
    recovery: RecoveryHint = "correctable"


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


# ---------------------------------------------------------------------------
# Typed subclasses for spec-compliant error codes.
# ---------------------------------------------------------------------------
# Each subclass pins its wire error_code to a STANDARD_ERROR_CODES entry, so
# raise sites can use semantic names (AdCPMediaBuyNotFoundError) instead of
# constructing Error(code="MEDIA_BUY_NOT_FOUND") inline. The boundary
# translator runs build_two_layer_error_envelope() on the raised exception.


class AdCPMediaBuyNotFoundError(AdCPNotFoundError):
    """Media buy lookup failed (404, MEDIA_BUY_NOT_FOUND).

    Recovery=correctable: the buyer can correct by supplying the right
    media_buy_id (typo, wrong tenant, stale reference). Overrides the
    ``AdCPNotFoundError`` ``terminal`` default — for this specific not-found
    case the buyer's own request is the lever for recovery.
    """

    error_code = "MEDIA_BUY_NOT_FOUND"
    recovery: RecoveryHint = "correctable"


class AdCPPackageNotFoundError(AdCPNotFoundError):
    """Package lookup failed within a media buy (404, PACKAGE_NOT_FOUND).

    Recovery=correctable: the buyer can correct by supplying the right
    package_id. Overrides the ``AdCPNotFoundError`` ``terminal`` default for
    the same reason as ``AdCPMediaBuyNotFoundError``.
    """

    error_code = "PACKAGE_NOT_FOUND"
    recovery: RecoveryHint = "correctable"


class AdCPCreativeRejectedError(AdCPError):
    """Creative failed policy or technical validation (422, CREATIVE_REJECTED)."""

    status_code = 422
    error_code = "CREATIVE_REJECTED"
    recovery: RecoveryHint = "correctable"


class AdCPBudgetExceededError(AdCPError):
    """Requested budget exceeds tenant or product ceiling (422, BUDGET_EXCEEDED)."""

    status_code = 422
    error_code = "BUDGET_EXCEEDED"
    recovery: RecoveryHint = "correctable"


class AdCPBudgetTooLowError(AdCPError):
    """Requested budget falls below product minimum (422, BUDGET_TOO_LOW)."""

    status_code = 422
    error_code = "BUDGET_TOO_LOW"
    recovery: RecoveryHint = "correctable"


class AdCPCapabilityNotSupportedError(AdCPError):
    """Requested capability is not supported by this seller (422, UNSUPPORTED_FEATURE).

    .. note::
        **Intentional spec divergence.** The AdCP spec classifies
        ``UNSUPPORTED_FEATURE`` as ``terminal``; we emit ``correctable``.
        The salesagent raises this exception only when the buyer holds the
        recovery lever — they can fix the request by dropping the
        unsupported feature (e.g. removing ``property_list`` targeting
        against an adapter that doesn't compile it). Classifying it
        ``terminal`` would tell the buyer agent to give up on a recoverable
        condition.

        **Revisit condition:** if the SDK runtime starts enforcing the
        spec's ``terminal`` classification at the wire (rejecting our
        ``correctable`` recovery hint), drop this override and update
        affected raise-site call sites to either select a different code or
        accept the ``terminal`` retry semantics. Until then this is the
        documented, expected behavior — not a TODO.

        FIXME(salesagent-unsupported-feature-recovery): grep tag for the
        revisit condition above. Remove when the SDK enforces terminal.
    """

    status_code = 422
    error_code = "UNSUPPORTED_FEATURE"
    recovery: RecoveryHint = "correctable"


class AdCPProductUnavailableError(AdCPError):
    """Product is offline, deactivated, or otherwise unavailable (422, PRODUCT_UNAVAILABLE)."""

    status_code = 422
    error_code = "PRODUCT_UNAVAILABLE"
    recovery: RecoveryHint = "correctable"


# ---------------------------------------------------------------------------
# Two-layer envelope serializer — single source of truth for wire shape.
# ---------------------------------------------------------------------------
# Boundary translators (MCP, A2A, REST) AND ContextManager.fail_step both
# call this so wire responses and persisted workflow_step.response_data are
# byte-identical by construction. _impl functions never build wire shape;
# they raise AdCPError subclasses and the boundary translator runs this.
#
# Spec: two-layer model is normative since AdCP 3.0.6 (CHANGELOG 91b6e2c).
# Storyboard runners (@adcp/sdk 6.11.0+) check errors[0].code (when
# success===false) AND adcp_error.code; missing either layer causes the
# runner to synthesize "MCP_ERROR" and erase the real code.


def build_two_layer_error_envelope(exc: AdCPError) -> dict[str, Any]:
    """Build the AdCP spec-compliant two-layer error envelope from an exception.

    Wraps the stable ``adcp_error()`` SDK helper for the payload half
    (``errors[]``), then mirrors the single error object at envelope level
    as ``adcp_error`` so the storyboard runner can read either path. Echoes
    ``exc.context`` when present.

    Returns:
        Plain dict with shape::

            {
                "adcp_error": {"code": "...", "message": "...", "recovery": "...", ...},
                "errors": [{"code": "...", "message": "...", "recovery": "...", ...}],
                "context": {...},     # only when exc.context is set
            }

    Both codes pass through ``ERROR_CODE_MAPPING`` via ``exc.wire_error_code``
    so they always land in ``STANDARD_ERROR_CODES``.
    """
    payload = adcp_error(
        exc.wire_error_code,
        exc.message,
        recovery=exc.recovery,
        field=exc.field,
        suggestion=exc.suggestion,
        details=exc.details,
    )
    # Copy errors[0] for the envelope-level mirror so callers that mutate one
    # layer don't accidentally mutate the other (aliasing footgun before PR 3
    # async/submitted work starts touching both).
    envelope: dict[str, Any] = {
        "adcp_error": dict(payload["errors"][0]),
        "errors": payload["errors"],
    }
    serialized_context = _serialize_context(exc.context)
    if serialized_context is not None:
        envelope["context"] = serialized_context
    return envelope
