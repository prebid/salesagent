"""AdCP exception hierarchy for typed error handling across transport layers.

Business logic raises these exceptions. Transport layers (A2A, MCP, REST)
translate them to their protocol's error format via registered handlers.

Exception classes define the error vocabulary — transport layers format them.
Each exception carries a recovery classification (transient/correctable/terminal)
to help buyer agents decide whether to retry, fix, or abandon a request.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from adcp.server.helpers import STANDARD_ERROR_CODES, adcp_error
from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Iterator

    from adcp.types import ContextObject

logger = logging.getLogger(__name__)

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
    # entry so the wire stays spec-compliant. Raise sites can later migrate to
    # specific subclasses; the mappings stay as a safety net.
    "NOT_FOUND": "INVALID_REQUEST",
    # Entity-specific not-found codes the SDK does not define as standard. The typed
    # subclasses (AdCPCreativeNotFoundError / AdCPFormatNotFoundError /
    # AdCPTaskNotFoundError) exist for recovery=correctable + guard-enforceability;
    # the buyer-visible wire code is INVALID_REQUEST. NOTE: CREATIVE_NOT_FOUND
    # (singular, a lookup miss) is distinct from the plural CREATIVES_NOT_FOUND →
    # CREATIVE_REJECTED bulk-sync code below — different keys, no overwrite.
    "CREATIVE_NOT_FOUND": "INVALID_REQUEST",
    "FORMAT_NOT_FOUND": "INVALID_REQUEST",
    "TASK_NOT_FOUND": "INVALID_REQUEST",
    "INTERNAL_ERROR": "SERVICE_UNAVAILABLE",
    "CONFIGURATION_ERROR": "SERVICE_UNAVAILABLE",
    # Authentication / authorisation
    # AUTH_TOKEN_INVALID is not mapped — it passes through directly as the
    # spec error code for invalid/missing tokens (per AdCP BDD feature files).
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
    "WORKFLOW_CREATION_FAILED": "SERVICE_UNAVAILABLE",
    "ACTIVATION_WORKFLOW_FAILED": "SERVICE_UNAVAILABLE",
    "LINE_ITEM_CREATION_FAILED": "SERVICE_UNAVAILABLE",
    "GAM_UPDATE_FAILED": "SERVICE_UNAVAILABLE",
    "CREATIVE_SYNC_FAILED": "SERVICE_UNAVAILABLE",
    "PARTIAL_FAILURE": "SERVICE_UNAVAILABLE",
    "PRODUCT_NOT_CONFIGURED": "PRODUCT_UNAVAILABLE",
    "INVENTORY_UNAVAILABLE": "PRODUCT_UNAVAILABLE",
    "CREATIVES_NOT_FOUND": "CREATIVE_REJECTED",
    "MEDIA_BUY_REJECTED": "POLICY_VIOLATION",
}

# Internal-only codes: never reach the buyer agent.  Each entry has a
# justification for why it is internal.
INTERNAL_CODES: frozenset[str] = frozenset(
    {
        "INTERNAL_ERROR",  # Base-class default; never instantiated for wire
        "NOT_FOUND",  # Base-class for entity-specific NotFound subclasses
        "CREATIVE_NOT_FOUND",  # AdCPCreativeNotFoundError; wire → INVALID_REQUEST
        "FORMAT_NOT_FOUND",  # AdCPFormatNotFoundError; wire → INVALID_REQUEST
        "TASK_NOT_FOUND",  # AdCPTaskNotFoundError; wire → INVALID_REQUEST
        "CONFIGURATION_ERROR",  # Server-side config; needs admin, not buyer
        "API_ERROR",  # Raw adapter API failure detail
        "WORKFLOW_CREATION_FAILED",  # GAM workflow orchestration detail
        "LINE_ITEM_CREATION_FAILED",  # GAM line-item creation detail
        "FLIGHT_NOT_FOUND",  # Kevel/Triton internal flight lookup
        "ACTIVATION_WORKFLOW_FAILED",  # GAM activation workflow detail
        "API_UPDATE_FAILED",  # Broadstreet API update detail
        "GAM_UPDATE_FAILED",  # GAM update API detail
        "PARTIAL_FAILURE",  # Bulk partial-failure taxonomy (AdCPBulkUpdateError)
        "MEDIA_BUY_REJECTED",  # Seller declined the buy; wire emits POLICY_VIOLATION
        "INVENTORY_UNAVAILABLE",  # Requested inventory absent; wire emits PRODUCT_UNAVAILABLE
    }
)

# Sanity check: every mapping target must be a standard code.
_NON_STANDARD_TARGETS = set(ERROR_CODE_MAPPING.values()) - set(STANDARD_ERROR_CODES)
assert not _NON_STANDARD_TARGETS, f"ERROR_CODE_MAPPING contains non-standard targets: {_NON_STANDARD_TARGETS}"


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
        - anything else → log a warning and return ``None``. This is reached
          from ``to_dict``/``to_adcp_error``/``build_two_layer_error_envelope``,
          all of which run inside exception handlers — raising here would shadow
          the original exception and the boundary translator would fail open
          with no envelope. A malformed context drops to ``None`` instead.
    """
    if context is None:
        return None
    if isinstance(context, dict):
        return dict(context)
    if not isinstance(context, BaseModel):
        logger.warning(
            "_serialize_context expected dict or BaseModel, got %s; dropping context", type(context).__name__
        )
        return None
    return context.model_dump(mode="json", exclude_none=True)


class AdCPError(Exception):
    """Base exception for all AdCP errors.

    Class-level identity (``_default_error_code``, ``_default_status_code``,
    ``_default_recovery``) is declared with ``ClassVar`` per PEP 526 — each
    typed subclass overrides the ``_default_*`` slot, not the public name.
    The public ``error_code``/``status_code``/``recovery`` are instance
    attributes set in ``__init__`` from the class-level default unless the
    caller overrides via kwargs (only ``synthesize()`` is sanctioned).

    Code that needs class-level identity (e.g. ``_build_error_code_to_status``
    walking ``__subclasses__()`` to build the wire-code → HTTP-status table)
    reads ``cls._default_error_code`` / ``cls._default_status_code`` directly.
    Instance code reads ``self.error_code`` etc. as before.

    Attributes:
        message: Human-readable error description.
        status_code: HTTP status code for REST/FastAPI responses (instance).
        error_code: Machine-readable error code string (instance).
        recovery: Recovery classification for buyer agents (instance).
        details: Optional structured error details.
        field: Optional field name that caused the error.
        suggestion: Optional correction hint for buyer agents.
        context: Optional AdCP ContextObject (or dict) echoed in the
            envelope so buyer agents can correlate failures to the
            request that produced them (spec 3.0.0 normative).
    """

    # Class-level identity defaults. Subclasses override these.
    _default_status_code: ClassVar[int] = 500
    _default_error_code: ClassVar[str] = "INTERNAL_ERROR"
    _default_recovery: ClassVar[RecoveryHint] = "terminal"

    # Instance attributes — set in __init__ from _default_* unless overridden.
    error_code: str
    status_code: int
    recovery: RecoveryHint

    def __init__(
        self,
        message: str = "",
        *,
        error_code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
        recovery: RecoveryHint | None = None,
        field: str | None = None,
        suggestion: str | None = None,
        context: ContextObject | dict[str, Any] | None = None,
    ) -> None:
        # ``error_code`` and ``status_code`` kwargs are only used by the
        # sanctioned ``synthesize()`` classmethod for boundary fallback paths
        # that need a wire code the typed class hierarchy doesn't model.
        # Direct raises use a typed subclass and inherit its ``_default_*``.
        super().__init__(message)
        self.message = message
        self.details = details
        self.field = field
        self.suggestion = suggestion
        self.context = context
        self.error_code = error_code if error_code is not None else type(self)._default_error_code
        self.status_code = status_code if status_code is not None else type(self)._default_status_code
        self.recovery = recovery if recovery is not None else type(self)._default_recovery

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

    @classmethod
    def synthesize(
        cls,
        message: str,
        *,
        error_code: str,
        status_code: int | None = None,
        recovery: RecoveryHint | None = None,
        details: dict[str, Any] | None = None,
        field: str | None = None,
        suggestion: str | None = None,
        context: ContextObject | dict[str, Any] | None = None,
    ) -> AdCPError:
        """Sanctioned entry point for synthesizing an AdCPError with overridden code/status.

        Typed subclasses (``AdCPValidationError``, etc.) carry
        ``error_code``/``status_code`` as class attributes. Two boundary
        callers — ``handle_tool_error``'s plain-``ToolError`` fallback and
        ``ContextManager.audit_workflow_step_failure``'s wire-code
        sanitization — need to construct an ``AdCPError`` with a code/status
        the typed class hierarchy doesn't model.

        Prefer this classmethod over passing ``error_code=``/``status_code=``
        kwargs to ``__init__`` directly. Constructor kwargs that mutate class
        attributes are a footgun the public API should not invite; this method
        documents the synthesis intent explicitly so reviewers can audit
        every site that bypasses the typed class hierarchy.
        """
        return cls(
            message,
            error_code=error_code,
            status_code=status_code,
            recovery=recovery,
            details=details,
            field=field,
            suggestion=suggestion,
            context=context,
        )

    @classmethod
    def iter_concrete_subclasses(cls) -> Iterator[type[AdCPError]]:
        """Yield every transitive *concrete* subclass of ``cls`` exactly once.

        Single source of truth for the subclass walk that builds the
        wire-code -> HTTP-status table (``_build_error_code_to_status``) and
        backs the error-code compliance tests. Yields descendants only — not
        ``cls`` itself — deduplicates so a class reachable by more than one
        path is visited once, and skips abstract bases (their descendants are
        still walked) so the name's "concrete" promise holds.
        """
        import inspect

        seen: set[type] = set()
        stack: list[type] = list(cls.__subclasses__())
        while stack:
            sub = stack.pop()
            if sub in seen:
                continue
            seen.add(sub)
            stack.extend(sub.__subclasses__())
            if not inspect.isabstract(sub):
                yield sub

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

    _default_status_code: ClassVar[int] = 400
    _default_error_code: ClassVar[str] = "VALIDATION_ERROR"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


class AdCPInvalidRequestError(AdCPValidationError):
    """A request value is well-formed but semantically invalid (400 → INVALID_REQUEST).

    Distinct from the schema-level VALIDATION_ERROR: the value passes type/shape
    validation but is invalid in context (e.g. start_time in the past, end_time
    before start_time). Carries the INVALID_REQUEST standard wire code as class
    identity; inherits 400 + correctable from AdCPValidationError.
    """

    _default_error_code: ClassVar[str] = "INVALID_REQUEST"


class AdCPAuthenticationError(AdCPError):
    """Missing or invalid authentication credentials (401).

    Default error_code is AUTH_TOKEN_INVALID per AdCP spec; the wire passes
    it through unchanged (AUTH_TOKEN_INVALID is a standard SDK code).

    Recovery defaults to ``terminal`` to match
    ``STANDARD_ERROR_CODES["AUTH_REQUIRED"]["recovery"]`` in adcp 4.3 (the SDK
    we run). That SDK table diverges from the spec's ``error-code.json``, which
    classifies AUTH_REQUIRED as ``correctable`` (re-auth recovers); we keep
    ``terminal`` so wire output matches the installed SDK's validator.
    """

    _default_status_code: ClassVar[int] = 401
    _default_error_code: ClassVar[str] = "AUTH_TOKEN_INVALID"


class AdCPAuthRequiredError(AdCPAuthenticationError):
    """No authentication context present (401, AUTH_TOKEN_INVALID).

    Raised when the request contains no auth token at all.
    Uses same error_code as parent (AUTH_TOKEN_INVALID) per spec.
    """

    _default_error_code: ClassVar[str] = "AUTH_TOKEN_INVALID"


class AdCPAuthorizationError(AdCPError):
    """Authenticated but not authorized for this resource (403).

    Same ``terminal`` default as ``AdCPAuthenticationError`` for the same
    SDK-vs-spec mismatch reason — see that class's docstring.
    """

    _default_status_code: ClassVar[int] = 403
    _default_error_code: ClassVar[str] = "AUTH_REQUIRED"


class AdCPPolicyViolationError(AdCPAuthorizationError):
    """Request content blocked by an advertising/content policy (403, POLICY_VIOLATION).

    Refines ``AdCPAuthorizationError`` (still a 403, still ``isinstance`` of it):
    the caller is permitted to call the tool, but the *content* of the request
    (brief, brand, targeting) violates a publisher policy. Carries the distinct
    ``POLICY_VIOLATION`` wire code, and the buyer can revise and retry, so
    recovery is ``correctable`` rather than the parent's ``terminal``.
    """

    _default_error_code: ClassVar[str] = "POLICY_VIOLATION"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


class AdCPNotFoundError(AdCPError):
    """Requested resource does not exist (404)."""

    _default_status_code: ClassVar[int] = 404
    _default_error_code: ClassVar[str] = "NOT_FOUND"


class AdCPAccountNotFoundError(AdCPNotFoundError):
    """Account not found by ID or natural key (404, ACCOUNT_NOT_FOUND)."""

    _default_error_code: ClassVar[str] = "ACCOUNT_NOT_FOUND"


class AdCPAccountSetupRequiredError(AdCPError):
    """Account exists but requires setup before use (422, ACCOUNT_SETUP_REQUIRED)."""

    _default_status_code: ClassVar[int] = 422
    _default_error_code: ClassVar[str] = "ACCOUNT_SETUP_REQUIRED"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


class AdCPAccountSuspendedError(AdCPError):
    """Account is suspended and cannot be used (403, ACCOUNT_SUSPENDED)."""

    _default_status_code: ClassVar[int] = 403
    _default_error_code: ClassVar[str] = "ACCOUNT_SUSPENDED"


class AdCPAccountPaymentRequiredError(AdCPError):
    """Account has outstanding payment requirements (402, ACCOUNT_PAYMENT_REQUIRED).

    Recovery=terminal (inherited): from the sales agent's perspective there is
    no in-band remediation — the buyer must settle the outstanding balance
    externally before resubmitting. Matches the BDD storyboard contract for
    UC-002 account-reference partition/boundary rows.
    """

    _default_status_code: ClassVar[int] = 402
    _default_error_code: ClassVar[str] = "ACCOUNT_PAYMENT_REQUIRED"


class AdCPConflictError(AdCPError):
    """Resource conflict, e.g. duplicate idempotency key (409)."""

    _default_status_code: ClassVar[int] = 409
    _default_error_code: ClassVar[str] = "CONFLICT"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


class AdCPAccountAmbiguousError(AdCPConflictError):
    """Natural key matches multiple accounts (409, ACCOUNT_AMBIGUOUS)."""

    _default_error_code: ClassVar[str] = "ACCOUNT_AMBIGUOUS"


class AdCPGoneError(AdCPError):
    """Resource previously existed but is no longer available (410).

    Recovery=correctable: the resource itself is gone, but the buyer can
    recover by referencing a different resource (a fresh proposal, a new
    media buy) and re-issuing the request.
    """

    _default_status_code: ClassVar[int] = 410
    _default_error_code: ClassVar[str] = "INVALID_STATE"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


class AdCPBudgetExhaustedError(AdCPError):
    """Budget or spend limit has been reached (422)."""

    _default_status_code: ClassVar[int] = 422
    _default_error_code: ClassVar[str] = "BUDGET_EXHAUSTED"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


class AdCPRateLimitError(AdCPError):
    """Too many requests (429)."""

    _default_status_code: ClassVar[int] = 429
    _default_error_code: ClassVar[str] = "RATE_LIMITED"
    _default_recovery: ClassVar[RecoveryHint] = "transient"


class AdCPAdapterError(AdCPError):
    """External adapter (GAM, etc.) failure (502)."""

    _default_status_code: ClassVar[int] = 502
    _default_error_code: ClassVar[str] = "SERVICE_UNAVAILABLE"
    _default_recovery: ClassVar[RecoveryHint] = "transient"


class AdCPConfigurationError(AdCPError):
    """Server-side configuration is broken (500).

    Raised when encrypted secrets cannot be decrypted (key rotation,
    corruption, missing ENCRYPTION_KEY). Callers should NOT silently
    fall back — the configuration needs admin intervention, so recovery is
    ``terminal`` (inherited): the buyer has no lever to fix server config.
    """

    _default_status_code: ClassVar[int] = 500
    _default_error_code: ClassVar[str] = "CONFIGURATION_ERROR"


class AdCPServiceUnavailableError(AdCPError):
    """Service or product temporarily unavailable (503).

    503 indicates a temporary outage in a downstream service the sales
    agent depends on. Recovery=transient so buyer agents retry rather
    than mutate the request.
    """

    _default_status_code: ClassVar[int] = 503
    _default_error_code: ClassVar[str] = "SERVICE_UNAVAILABLE"
    _default_recovery: ClassVar[RecoveryHint] = "transient"


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

    _default_error_code: ClassVar[str] = "MEDIA_BUY_NOT_FOUND"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


class AdCPPackageNotFoundError(AdCPNotFoundError):
    """Package lookup failed within a media buy (404, PACKAGE_NOT_FOUND).

    Recovery=correctable: the buyer can correct by supplying the right
    package_id. Overrides the ``AdCPNotFoundError`` ``terminal`` default for
    the same reason as ``AdCPMediaBuyNotFoundError``.
    """

    _default_error_code: ClassVar[str] = "PACKAGE_NOT_FOUND"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


class AdCPProductNotFoundError(AdCPNotFoundError):
    """Requested product does not exist (404, PRODUCT_NOT_FOUND).

    Recovery=correctable: the buyer can correct by supplying a valid
    product_id (discoverable via get_products). Overrides the
    ``AdCPNotFoundError`` ``terminal`` default for the same reason as
    ``AdCPMediaBuyNotFoundError`` — the buyer's own request is the lever
    for recovery. PRODUCT_NOT_FOUND is a standard SDK code (passthrough,
    not in ERROR_CODE_MAPPING).
    """

    _default_error_code: ClassVar[str] = "PRODUCT_NOT_FOUND"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


class AdCPContextNotFoundError(AdCPNotFoundError):
    """Buyer-supplied context_id does not resolve (404, SESSION_NOT_FOUND).

    A ``context_id`` that does not map to a persistent context is a not-found
    condition, not a gone/expired one: ``Context`` rows have no TTL, expiry, or
    delete path anywhere in ``src/``, so a non-resolving id never existed. That
    rules out ``AdCPGoneError`` (``INVALID_STATE``) — the correct wire code is
    ``SESSION_NOT_FOUND``, the standard SDK code for an unresolvable
    session/context (passthrough, not in ERROR_CODE_MAPPING).

    Recovery=correctable: the buyer can correct by supplying a valid context_id
    or omitting it to start a fresh context. Overrides the ``AdCPNotFoundError``
    ``terminal`` default for the same reason as ``AdCPMediaBuyNotFoundError``.
    """

    _default_error_code: ClassVar[str] = "SESSION_NOT_FOUND"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


class AdCPCreativeNotFoundError(AdCPNotFoundError):
    """Requested creative does not exist (404, wire → INVALID_REQUEST).

    The SDK has no ``CREATIVE_NOT_FOUND`` standard code, so the raw code is
    internal and translated to ``INVALID_REQUEST`` at the wire boundary (see
    ERROR_CODE_MAPPING). The buyer-visible gain over the bare
    ``AdCPNotFoundError`` is recovery=correctable + a typed identity callers and
    guards can pin — not a distinct wire code.

    Recovery=correctable: the buyer can correct by supplying a valid creative_id
    (discoverable via list_creatives / sync_creatives).
    """

    _default_error_code: ClassVar[str] = "CREATIVE_NOT_FOUND"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


class AdCPFormatNotFoundError(AdCPNotFoundError):
    """Requested creative format does not exist on the agent (404, wire → INVALID_REQUEST).

    No standard ``FORMAT_NOT_FOUND`` SDK code exists, so the raw code is internal
    and translated to ``INVALID_REQUEST`` at the wire boundary. The gain over the
    bare ``AdCPNotFoundError`` is recovery=correctable + a typed identity.

    Recovery=correctable: the buyer can correct by supplying a valid format_id
    (discoverable via list_creative_formats).
    """

    _default_error_code: ClassVar[str] = "FORMAT_NOT_FOUND"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


class AdCPTaskNotFoundError(AdCPNotFoundError):
    """Requested workflow task/step does not exist (404, wire → INVALID_REQUEST).

    No standard ``TASK_NOT_FOUND`` SDK code exists, so the raw code is internal
    and translated to ``INVALID_REQUEST`` at the wire boundary. The gain over the
    bare ``AdCPNotFoundError`` is recovery=correctable + a typed identity.

    Recovery=correctable: the buyer can correct by supplying a valid task_id
    (discoverable via list_tasks).
    """

    _default_error_code: ClassVar[str] = "TASK_NOT_FOUND"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


class AdCPBudgetTooLowError(AdCPError):
    """Requested budget falls below product minimum (422, BUDGET_TOO_LOW)."""

    _default_status_code: ClassVar[int] = 422
    _default_error_code: ClassVar[str] = "BUDGET_TOO_LOW"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


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

    _default_status_code: ClassVar[int] = 422
    _default_error_code: ClassVar[str] = "UNSUPPORTED_FEATURE"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


class AdCPCreativeRejectedError(AdCPError):
    """Creative failed policy or technical validation (422, CREATIVE_REJECTED)."""

    _default_status_code: ClassVar[int] = 422
    _default_error_code: ClassVar[str] = "CREATIVE_REJECTED"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


class AdCPBudgetExceededError(AdCPError):
    """Requested budget exceeds tenant or product ceiling (422, BUDGET_EXCEEDED)."""

    _default_status_code: ClassVar[int] = 422
    _default_error_code: ClassVar[str] = "BUDGET_EXCEEDED"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


class AdCPProductUnavailableError(AdCPError):
    """Product is offline, deactivated, or otherwise unavailable (422, PRODUCT_UNAVAILABLE)."""

    _default_status_code: ClassVar[int] = 422
    _default_error_code: ClassVar[str] = "PRODUCT_UNAVAILABLE"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


# ---------------------------------------------------------------------------
# Adapter-taxonomy subclasses (502 → SERVICE_UNAVAILABLE).
# ---------------------------------------------------------------------------
# These extend AdCPAdapterError to carry an internal failure taxonomy as the
# class identity instead of smuggling it through ``details["internal_code"]``
# (which is buyer-visible). The raw ``error_code`` stays in INTERNAL_CODES for
# server-side logs/audit; ``wire_error_code`` translates it to
# SERVICE_UNAVAILABLE via ERROR_CODE_MAPPING so the buyer sees a standard code.


class AdCPWorkflowError(AdCPAdapterError):
    """Workflow-step orchestration failed inside an adapter (502 → SERVICE_UNAVAILABLE).

    Carries the WORKFLOW_CREATION_FAILED taxonomy as the class identity so
    logs/audit retain the specific failure mode while the wire shows the
    standard SERVICE_UNAVAILABLE. Recovery=transient (inherited): the
    workflow subsystem may succeed on retry.
    """

    _default_error_code: ClassVar[str] = "WORKFLOW_CREATION_FAILED"


class AdCPLineItemError(AdCPAdapterError):
    """Adapter line-item creation failed (502 → SERVICE_UNAVAILABLE).

    Carries the LINE_ITEM_CREATION_FAILED taxonomy as the class identity;
    same rationale as ``AdCPWorkflowError``.
    """

    _default_error_code: ClassVar[str] = "LINE_ITEM_CREATION_FAILED"


class AdCPBulkUpdateError(AdCPAdapterError):
    """A bulk update partially failed — N operations attempted, M failed (502 → SERVICE_UNAVAILABLE).

    Unifies the cross-adapter partial-failure event under one class and one
    status (502) so REST clients filtering on HTTP status don't fork by
    adapter (previously broadstreet raised 502, GAM raised 503 for the same
    semantic event). Carries the PARTIAL_FAILURE taxonomy as the class
    identity; per-operation detail (failed IDs, counts) belongs in ``details``
    as data. Recovery=transient (inherited): failed operations may succeed
    on retry.
    """

    _default_error_code: ClassVar[str] = "PARTIAL_FAILURE"


class AdCPActivationWorkflowError(AdCPAdapterError):
    """Adapter order/line-item activation workflow failed (502 → SERVICE_UNAVAILABLE).

    Distinct from ``AdCPWorkflowError`` (creation): this is the activation step
    of an existing order. Carries the ACTIVATION_WORKFLOW_FAILED taxonomy as the
    class identity; same wire mapping as the other adapter-workflow failures.
    """

    _default_error_code: ClassVar[str] = "ACTIVATION_WORKFLOW_FAILED"


class AdCPGamUpdateError(AdCPAdapterError):
    """A GAM line-item update API call failed (502 → SERVICE_UNAVAILABLE).

    Carries the GAM_UPDATE_FAILED taxonomy as the class identity; per-operation
    detail (package_id, line_item_id) belongs in ``details`` as data.
    """

    _default_error_code: ClassVar[str] = "GAM_UPDATE_FAILED"


class AdCPMediaBuyRejectedError(AdCPError):
    """The seller declined the media buy (422 → POLICY_VIOLATION).

    A business rejection, not a server failure: recovery=correctable so the
    buyer can adjust the request and resubmit. Carries the MEDIA_BUY_REJECTED
    taxonomy as the class identity; the wire code is the standard POLICY_VIOLATION.
    """

    _default_status_code: ClassVar[int] = 422
    _default_error_code: ClassVar[str] = "MEDIA_BUY_REJECTED"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


class AdCPInventoryUnavailableError(AdCPError):
    """Requested inventory is not available (422 → PRODUCT_UNAVAILABLE).

    recovery=correctable: the buyer can select different inventory. Carries the
    INVENTORY_UNAVAILABLE taxonomy as the class identity; the wire code is the
    standard PRODUCT_UNAVAILABLE.
    """

    _default_status_code: ClassVar[int] = 422
    _default_error_code: ClassVar[str] = "INVENTORY_UNAVAILABLE"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


# ---------------------------------------------------------------------------
# Two-layer envelope serializer — single source of truth for wire shape.
# ---------------------------------------------------------------------------
# All three boundary translators (MCP, A2A, REST) and
# ContextManager.audit_workflow_step_failure call this so wire
# responses and persisted workflow_step.response_data share the same
# two-layer shape. _impl functions never build wire shape; they raise
# AdCPError subclasses and the boundary translator runs this.
#
# Spec: two-layer model is normative since AdCP 3.0.0 (``error-handling.mdx``).
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
    # layer don't accidentally mutate the other (aliasing footgun once both
    # layers may be mutated independently).
    envelope: dict[str, Any] = {
        "adcp_error": dict(payload["errors"][0]),
        "errors": payload["errors"],
    }
    serialized_context = _serialize_context(exc.context)
    if serialized_context is not None:
        envelope["context"] = serialized_context
    return envelope


def normalize_to_adcp_error(exc: Exception) -> AdCPError:
    """Normalize untyped exceptions to typed AdCPError subclasses.

    Single source of truth for the wrapping applied at all three transport
    boundaries (MCP, A2A, REST).  Already-typed ``AdCPError`` passes through
    unchanged.  ``ValueError`` maps to ``AdCPValidationError``,
    ``PermissionError`` to ``AdCPAuthorizationError``, and anything else
    wraps in base ``AdCPError`` (INTERNAL_ERROR).
    """
    if isinstance(exc, AdCPError):
        return exc
    if isinstance(exc, ValueError):
        return AdCPValidationError(str(exc))
    if isinstance(exc, PermissionError):
        return AdCPAuthorizationError(str(exc))
    return AdCPError(str(exc) or type(exc).__name__)
