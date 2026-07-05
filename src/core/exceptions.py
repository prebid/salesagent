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

import httpx
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

# Spec-required codes the SDK's STANDARD_ERROR_CODES table does not yet carry (the
# published-error-enum-vs-SDK drift). Unlike INTERNAL_CODES, these REACH the wire
# unchanged — valid AdCP codes, merely absent from the vendored SDK helper. Single
# source for the guards that enumerate acceptable wire codes.
SPEC_CODES: frozenset[str] = frozenset(
    {
        "AUTH_TOKEN_INVALID",  # BR-UC-011: invalid/missing auth token
        "BILLING_NOT_SUPPORTED",  # BR-UC-011 BR-RULE-059: unsupported billing model
        "IDEMPOTENCY_IN_FLIGHT",  # BR-UC-002/016/020/023/028: rule-9 reject-and-redirect (3.1.0-beta.0)
    }
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
        retry_after: int | None = None,
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
        self.retry_after = retry_after
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
        if self.retry_after is not None:
            result["retry_after"] = self.retry_after
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
            retry_after=self.retry_after,
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

    Default error_code is ``AUTH_TOKEN_INVALID``. This code is project-specific:
    it is in neither the AdCP 3.1 error-code enum nor adcp 5.7
    ``STANDARD_ERROR_CODES`` (both define only ``AUTH_REQUIRED``). It reaches the
    wire by passthrough — it is deliberately absent from ``ERROR_CODE_MAPPING``,
    so ``wire_error_code`` returns it unchanged on the sync transports
    (REST/MCP/A2A). The async webhook path additionally enforces
    ``STANDARD_ERROR_CODES`` and would downgrade it to ``SERVICE_UNAVAILABLE``.

    Recovery defaults to ``terminal`` (inherited from ``AdCPError``; this is a
    hardcoded ``_default_recovery`` ClassVar, not read from
    ``STANDARD_ERROR_CODES``). We keep ``terminal`` deliberately: the AdCP 3.1
    storyboards grade the wire *error code*, not the recovery class, so the
    recovery hint is ours to set. This intentionally diverges from how adcp 5.7
    classifies its nearest neighbour ``AUTH_REQUIRED`` (``correctable``).
    """

    _default_status_code: ClassVar[int] = 401
    _default_error_code: ClassVar[str] = "AUTH_TOKEN_INVALID"


class AdCPAuthRequiredError(AdCPAuthenticationError):
    """No authentication context present (401, AUTH_TOKEN_INVALID).

    Raised when the request contains no auth token at all.
    Uses same error_code as parent (AUTH_TOKEN_INVALID) — a project-specific
    code; see parent docstring.
    """

    _default_error_code: ClassVar[str] = "AUTH_TOKEN_INVALID"


class AdCPAuthorizationError(AdCPError):
    """Authenticated but not authorized for this resource (403).

    Same ``terminal`` default as ``AdCPAuthenticationError``, for the same
    reason: recovery is intentionally terminal because the AdCP 3.1 storyboards
    grade the wire error code, not the recovery class — see that class's
    docstring.
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

    Recovery is ``correctable``, matching the spec: AdCP 3.1.0-beta.3
    ``error-handling.mdx`` classifies ``UNSUPPORTED_FEATURE`` as ``correctable``
    ("Check ``get_adcp_capabilities`` and remove unsupported fields"). The buyer
    holds the recovery lever — they fix the request by dropping the unsupported
    feature (e.g. removing ``property_list`` targeting against an adapter that
    doesn't compile it) — so ``terminal`` (give up / escalate to a human) would be
    the wrong instruction for a buyer-resolvable condition.
    """

    _default_status_code: ClassVar[int] = 422
    _default_error_code: ClassVar[str] = "UNSUPPORTED_FEATURE"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


class AdCPIdempotencyConflictError(AdCPConflictError):
    """idempotency_key reused with a different request payload (409, IDEMPOTENCY_CONFLICT).

    Recovery=correctable: the buyer can fix this and resend — either replay the
    ORIGINAL bytes under the same key, or mint a fresh idempotency_key for the
    new payload. This matches the AdCP 3.0.1 prose example envelope and the
    conformance storyboard's stated expectation. The SDK's
    ``STANDARD_ERROR_CODES`` table classifies the code ``terminal``, but that
    table is only a default applied when no recovery is supplied — an explicit
    recovery always wins, and nothing in the SDK or the storyboard's machine
    validations grades the value.
    """

    _default_error_code: ClassVar[str] = "IDEMPOTENCY_CONFLICT"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


class AdCPIdempotencyExpiredError(AdCPConflictError):
    """idempotency_key seen before, but its replay window has expired (409, IDEMPOTENCY_EXPIRED).

    Raised when a same-key buy exists but outlived the advertised replay TTL
    (``get_adcp_capabilities.adcp.idempotency.replay_ttl_seconds``): per
    security.mdx#idempotency rule 6, a request arriving after eviction with a
    key the seller has seen SHOULD be rejected with ``IDEMPOTENCY_EXPIRED``
    rather than silently treated as new or answered with another buy's data.

    Recovery=correctable, matching the sibling ``IDEMPOTENCY_CONFLICT``: the
    buyer agent recovers autonomously — a natural-key existence check (e.g.
    ``get_media_buys`` by ``context.internal_campaign_id``) to learn whether the
    original request succeeded, then either accept that result or mint a fresh
    idempotency_key for a new attempt. The 3.0.1 ``error-code.json`` enum
    description classifies the code ``correctable`` (that buyer-recovery path),
    and the recovery taxonomy reserves ``terminal`` for conditions requiring
    HUMAN action (account suspended, payment required) — not an agent-resolvable
    retry. The SDK's ``STANDARD_ERROR_CODES`` default table lists it ``terminal``,
    but that default applies only when no recovery is supplied; an explicit
    recovery wins, exactly as for ``IDEMPOTENCY_CONFLICT``.
    """

    _default_error_code: ClassVar[str] = "IDEMPOTENCY_EXPIRED"
    _default_recovery: ClassVar[RecoveryHint] = "correctable"


class AdCPIdempotencyInFlightError(AdCPError):
    """A concurrent same-key request is still in flight (503, IDEMPOTENCY_IN_FLIGHT).

    Raised on the reject-and-redirect path: a request arrives while another request
    carrying the same idempotency_key is mid-execution (the winner has not yet
    committed its verbatim response). Per security.mdx#idempotency rule 9, the seller
    returns ``IDEMPOTENCY_IN_FLIGHT`` with ``retry_after``; the buyer MUST retry with
    the SAME idempotency_key once the hint elapses and MUST NOT mint a fresh key —
    minting a new key turns a safe retry into the exact double-execution race the key
    exists to prevent.

    Recovery=transient — NOT correctable, unlike the sibling CONFLICT/EXPIRED. The buyer
    recovers by retrying the UNCHANGED request after a delay, exactly as for the
    ``SERVICE_UNAVAILABLE`` this replaces on the in-flight path: the request is correct,
    the seller is merely not done committing. Rule 9 entered at AdCP 3.1.0-beta.0. The
    code is not yet in the SDK's ``STANDARD_ERROR_CODES`` (the published-enum-vs-SDK
    drift), so it is registered in the compliance guard's ``_SPEC_CODES`` passthrough
    set and reaches the wire unchanged.
    """

    _default_status_code: ClassVar[int] = 503
    _default_error_code: ClassVar[str] = "IDEMPOTENCY_IN_FLIGHT"
    _default_recovery: ClassVar[RecoveryHint] = "transient"


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
# HTTP status -> AdCP recovery, single-sourced per boundary.
# ---------------------------------------------------------------------------
# An outbound HTTP failure maps to an AdCP recovery class, and the verdict has
# exactly one home per boundary so consumers cannot drift (the failure mode that
# let an error type hand-copy the table and then mis-set a 403). Two boundaries,
# two tables, because the buyer's lever differs:
#
#   * GENERAL / buyer-facing (a buyer's referenced resource — e.g. the buyer's
#     property-list service fetched in ``property_list_resolver``): a 4xx is the
#     buyer's reference/token, so "fix the request and resend" -> correctable.
#   * AD-SERVER (the tenant operator's ad server — Kevel/Xandr/Triton/Broadstreet
#     writes + the Kevel site read): a 403 is the operator's credential being
#     denied, which the buyer has no lever to fix, so per the spec recovery
#     taxonomy ("terminal: requires human action") it is terminal — matching the
#     application-level credential-rejection raise sites (e.g. Xandr _authenticate).
#
# Each table is keyed off a single class selector, so the (error_code, recovery)
# pair is read from the chosen subclass's ``_default_*`` — one source, no copy.


def _adcp_error_class_for_http_status(status: int) -> type[AdCPError]:
    """The AdCPError subclass a GENERAL (buyer-facing) HTTP status maps to.

    - ``429`` -> ``AdCPRateLimitError`` (RATE_LIMITED / transient)
    - other ``4xx`` -> ``AdCPValidationError`` (VALIDATION_ERROR / correctable)
    - ``5xx`` and any non-4xx -> ``AdCPAdapterError`` (SERVICE_UNAVAILABLE / transient)
    """
    if status == 429:
        return AdCPRateLimitError
    if 400 <= status < 500:
        return AdCPValidationError
    return AdCPAdapterError


def _instantiate_status_error(
    cls: type[AdCPError], message: str, *, field: str | None, suggestion: str | None
) -> AdCPError:
    """Instantiate the selected status->error class, threading ``field``/``suggestion`` only for the validation class.

    ``field``/``suggestion`` enrich the correctable (4xx validation) case; a
    transient 429/5xx is not fixed by editing the request, so they are ignored
    there. Shared by the general and ad-server status factories so the
    class-instantiation rule lives in exactly one place.
    """
    if cls is AdCPValidationError:
        return cls(message, field=field, suggestion=suggestion)
    return cls(message)


def adcp_error_for_http_status(
    status: int, message: str, *, field: str | None = None, suggestion: str | None = None
) -> AdCPError:
    """Map a GENERAL (buyer-facing) outbound HTTP status to its typed AdCP error.

    The boundary where a 4xx is the buyer's referenced resource (the property-list
    fetch in ``property_list_resolver``): a 4xx is correctable (fix the reference and
    resend), 429/5xx are transient (AdCP 3.1.0-beta.3 recovery taxonomy). Ad-server
    writes/reads use ``adcp_adapter_error_for_http_status`` instead — a 403 there is
    the tenant operator's credential, which is terminal, not buyer-correctable.

    ``field``/``suggestion`` enrich the correctable (4xx) case; they are ignored for the
    transient classes (a 429/5xx is not fixed by editing the request).
    """
    return _instantiate_status_error(
        _adcp_error_class_for_http_status(status), message, field=field, suggestion=suggestion
    )


def _adcp_adapter_error_class_for_http_status(status: int) -> type[AdCPError]:
    """The AdCPError subclass an AD-SERVER HTTP status maps to.

    The general selector refined for the one status whose buyer recovery differs at
    an ad-server boundary: a ``403`` is the tenant operator's credential being denied
    -> ``AdCPConfigurationError`` (CONFIGURATION_ERROR / terminal), matching the
    application-level credential-rejection raise sites. All other statuses share the
    general selector.
    """
    if status == 403:
        return AdCPConfigurationError
    return _adcp_error_class_for_http_status(status)


def ad_server_error_attrs(status: int) -> tuple[str, RecoveryHint, int]:
    """The ``(error_code, recovery, status_code)`` an AD-SERVER HTTP status maps to.

    The pure-mapping form for a consumer that configures its error attributes in
    ``__init__`` (``BroadstreetAPIError``) and so cannot consume a factory that hands
    back a *different* object. Reads the selected class's ``_default_*`` so it produces
    the SAME triple as ``adcp_adapter_error_for_http_status``'s factory path for the
    same status — INCLUDING the buyer-facing HTTP ``status_code``, so one ad-server
    event yields one buyer-facing status regardless of which adapter the tenant runs
    (the upstream ad-server status stays in the error message / ``response_body``, not
    the wire status line). A 403 is terminal (operator credential denied); all other
    statuses share the general table.
    """
    cls = _adcp_adapter_error_class_for_http_status(status)
    return (cls._default_error_code, cls._default_recovery, cls._default_status_code)


def adcp_adapter_error_for_http_status(
    status: int, message: str, *, field: str | None = None, suggestion: str | None = None
) -> AdCPError:
    """Map an AD-SERVER outbound HTTP status to its typed AdCP error.

    The ad-server dual of ``adcp_error_for_http_status`` (``wrap_request_errors`` for
    the ``requests``-based ad-server writes; ``adcp_error_for_httpx_exc`` for the
    ``httpx``-based Kevel site read). A 403 (operator ``access_token``/credential
    denied) is a terminal ``AdCPConfigurationError`` (wire ``SERVICE_UNAVAILABLE`` /
    recovery ``terminal``): the buyer has no lever to fix the tenant's ad-server
    credential, so "fix and resend" (correctable) would loop them wrongly. All other
    statuses share the general factory (429/5xx -> transient, other 4xx -> correctable).
    """
    return _instantiate_status_error(
        _adcp_adapter_error_class_for_http_status(status), message, field=field, suggestion=suggestion
    )


def adcp_error_for_httpx_exc(
    exc: httpx.HTTPError, message: str, *, field: str | None = None, suggestion: str | None = None
) -> AdCPError:
    """Map an ``httpx`` transport failure to its typed AdCP error — the httpx dual of ``wrap_request_errors``.

    Used by the Kevel site read (``kevel_site_resolver``), an AD-SERVER call authenticated with
    the tenant operator's API key, so a response-bearing ``httpx.HTTPStatusError`` routes through
    the ad-server table (``adcp_adapter_error_for_http_status``): a 403 is the operator's credential
    -> terminal, other 4xx -> correctable, 429/5xx -> transient. A response-less failure
    (``TimeoutException``/``RequestError`` — timeout, connection reset) has no status and is a
    transient ``AdCPAdapterError``. Without this seam an httpx handler that re-wraps every failure as
    ``AdCPAdapterError`` reports a status-bearing failure as transient (retry forever).

    ``field``/``suggestion`` enrich the correctable (4xx) case; they are ignored for the
    transient/terminal classes. ``property_list_resolver._raise_fetch_error`` is the buyer-facing
    sibling (a 4xx there is the buyer's reference -> correctable) and uses the GENERAL table directly,
    distinguishing timeout from connect in its message, so it does not route through here.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return adcp_adapter_error_for_http_status(exc.response.status_code, message, field=field, suggestion=suggestion)
    return AdCPAdapterError(message)


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
        retry_after=exc.retry_after,
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
