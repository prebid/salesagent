"""AdCP exception hierarchy for typed error handling across transport layers.

Business logic raises these exceptions. Transport layers (A2A, MCP, REST)
translate them to their protocol's error format via registered handlers.

Exception classes define the error vocabulary — transport layers format them.
Each exception carries a recovery classification (transient/correctable/terminal)
to help buyer agents decide whether to retry, fix, or abandon a request.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any, ClassVar, Literal, get_args

from adcp.server.helpers import STANDARD_ERROR_CODES, adcp_error
from adcp.types import Error as LibraryError
from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from adcp.types import ContextObject

logger = logging.getLogger(__name__)

RecoveryHint = Literal["transient", "correctable", "terminal"]

# ---------------------------------------------------------------------------
# Error-code compliance: mapping non-standard codes to SDK equivalents
# ---------------------------------------------------------------------------
# Every code that reaches the wire (buyer agent) MUST be in
# WIRE_STANDARD_CODES.  Codes in ERROR_CODE_MAPPING are translated at the
# transport boundary; codes in INTERNAL_CODES never leave the server.
#
# Two tables, one job each, so neither answers a question the other owns:
#   * WIRE_STANDARD_CODES  — which code NAMES may reach a buyer. Membership only;
#     its values are empty by construction (see below).
#   * RECOVERY_BY_WIRE_CODE — what each of those codes means for retry. Loaded
#     from the pinned spec at import; the only recovery classification either
#     TABLE in this module carries.
# Recovery is DERIVED, not stated. ``AdCPError.recovery`` is a read-only property
# that looks its wire code up in RECOVERY_BY_WIRE_CODE, so the only way to say
# "this is terminal" is to raise a class whose code the pinned enumMetadata
# classifies terminal. There is no ``recovery=`` kwarg on ``__init__`` or on
# ``synthesize`` to disagree with the pin through, and no raise site in ``src/``
# carries a hand-typed recovery literal.
#
# One hand-typed surface remains, and it is graded: the per-class
# ``_default_recovery`` literals below. They are the fallback for a code the pin
# does not define, and
# tests/unit/test_architecture_error_recovery_enum_conformance.py checks every
# class whose code the pin DOES define against this same table — so a literal
# that contradicts the pin fails a test rather than reaching a buyer.


def _load_pinned_recovery() -> dict[str, RecoveryHint]:
    """Read the normative ``recovery`` classification for every pinned wire code.

    Source: the installed SDK's own plain schema tree, ``adcp/_schemas/<major.minor>/
    enums/error-code.json`` → ``enumMetadata``. That block is normative — its
    ``$comment`` states "SDKs MUST consume this block instead of parsing
    'Recovery: X' from enumDescriptions prose" — so it is machine-read here rather
    than transcribed. A pin bump moves this table with zero edits to this file.

    Deliberately NOT sourced from ``adcp.server.helpers.STANDARD_ERROR_CODES``:
    probed at the 6.6.0 pin, the helper's own ``recovery`` values contradict the
    schema on 7 of its 38 codes (UNSUPPORTED_FEATURE, AUTHORIZATION_REQUIRED,
    IDEMPOTENCY_CONFLICT and IDEMPOTENCY_EXPIRED terminal-vs-correctable;
    ACCOUNT_PAYMENT_REQUIRED and BUDGET_EXHAUSTED correctable-vs-terminal;
    CONFLICT correctable-vs-transient). The SDK is a cross-check, not the
    authority (CLAUDE.md spec-grounding gate).

    Mirrors ``_pinned_recovery_by_code`` in
    tests/unit/test_architecture_error_recovery_enum_conformance.py, which keeps
    its OWN independent load — src cannot import from tests, and the duplicated
    path derivation is what lets that oracle grade this loader instead of
    agreeing with it.
    """
    import json
    from pathlib import Path

    import adcp

    major, minor = adcp.get_adcp_spec_version().split(".")[:2]
    schema_path = Path(adcp.__file__).parent / "_schemas" / f"{major}.{minor}" / "enums" / "error-code.json"
    metadata = json.loads(schema_path.read_text())["enumMetadata"]
    table = {
        code: entry["recovery"] for code, entry in metadata.items() if isinstance(entry, dict) and "recovery" in entry
    }

    # The file-shape invariants live HERE, in the seam that reads the file, so a
    # partial table can never be RETURNED. Checked at module scope they would be
    # checked on a partial table that already exists and has already been handed
    # to every caller. `raise`, not `assert`: -O deletes an assert, and a pin
    # whose enforcement vanishes under an interpreter flag is not enforcement.
    if len(table) < 90:
        raise RuntimeError(
            f"RECOVERY_BY_WIRE_CODE loaded only {len(table)} codes from the pinned enumMetadata; "
            f"the 3.1 pin defines 92. The loader is reading the wrong file or shape."
        )
    bad_values = {v for v in table.values() if v not in get_args(RecoveryHint)}
    if bad_values:
        raise RuntimeError(f"Pinned enumMetadata carries recovery value(s) outside RecoveryHint: {bad_values}")

    return table


# The recovery classification for every code the pinned spec defines (92 at the
# 3.1 pin). Read-only vocabulary: consumers look a code up, never assign one.
RECOVERY_BY_WIRE_CODE: dict[str, RecoveryHint] = _load_pinned_recovery()

# Spec codes the SDK helper table has not caught up to. The pinned 3.1 enum
# (enums/error-code.json, shipped inside the installed adcp SDK) defines these
# as real wire codes; the SDK's ``STANDARD_ERROR_CODES`` predates them, and the
# SDK is a cross-check, not the authority. CREATIVE_NOT_FOUND per the enum:
# "Sellers MUST return this code uniformly for any creative_id not owned by the
# calling account" (#1430 review). CONFIGURATION_ERROR per the enum: "the buyer
# cannot resolve a seller-side deployment misconfiguration and MUST NOT
# auto-retry" (#1430 review). Their recovery classifications are NOT repeated
# here — this is a set of code NAMES; RECOVERY_BY_WIRE_CODE answers what they
# mean. The remaining demoted spec code (BILLING_NOT_SUPPORTED) is tracked for
# the same treatment in #1602.
_SPEC_SUPPLEMENT_CODES: frozenset[str] = frozenset({"CREATIVE_NOT_FOUND", "CONFIGURATION_ERROR", "REFERENCE_NOT_FOUND"})

# Codes the SDK helper ships that the PINNED spec does not define. The pin is the
# authority and the helper is a cross-check (CLAUDE.md spec-grounding gate), so a
# helper-only code is not a wire code -- it has no normative recovery
# classification, and admitting it would leave the recovery table partial and
# every lookup falling back to an authored default, which is exactly what
# invariant I6 ("recovery is derived, never authored") forbids.
#
# NOT_SUPPORTED is the single code by which adcp.server.helpers.STANDARD_ERROR_CODES
# (38 entries) exceeds the pinned 3.1 enum (92 entries): absent from `enum` and
# from `enumMetadata`, and absent from the whole of dist/ at v3.1.1. The SDK even
# assigns it recovery="terminal", which _load_pinned_recovery's docstring above
# already explains is not a value to trust. UNSUPPORTED_FEATURE, which IS pinned,
# is the code this seller emits instead; nothing in src/ produces the bare one.
_SPEC_DEMOTED_CODES: frozenset[str] = frozenset({"NOT_SUPPORTED"})

# The authoritative wire-code table: SDK code-name baseline + pinned-spec
# supplement. Values are empty on purpose and every consumer is a membership
# check — carrying the SDK's own recovery values here would leave 7 codes
# answering a recovery question with a value the pin contradicts, one line below
# the table that reads the pin. An accidental value read is a loud KeyError for
# every code instead of a silently wrong classification for some. Look a
# classification up in RECOVERY_BY_WIRE_CODE, never here.
WIRE_STANDARD_CODES: dict[str, dict[str, str]] = {
    code: {} for code in (*STANDARD_ERROR_CODES, *sorted(_SPEC_SUPPLEMENT_CODES)) if code not in _SPEC_DEMOTED_CODES
}

# The wire set is TOTAL over the recovery table, by construction rather than by
# inspection. Deleting one helper-only code would only remove today's instance:
# the set is DERIVED from the SDK helper, so the next helper code the SDK ships
# ahead of the pin would re-create the partial table verbatim and silently. This
# raise turns that drift into an import failure that NAMES the offending code and
# states the two ways to resolve it, so nobody has to rediscover which is right.
_UNPINNED_WIRE_CODES = set(WIRE_STANDARD_CODES) - set(RECOVERY_BY_WIRE_CODE)
if _UNPINNED_WIRE_CODES:
    raise RuntimeError(
        f"Wire code(s) with no pinned recovery classification: {sorted(_UNPINNED_WIRE_CODES)}. "
        f"The SDK helper ships a code the pinned enumMetadata does not classify. Either the pin "
        f"moved and _SPEC_SUPPLEMENT_CODES should carry it, or the helper is ahead of the spec and "
        f"_SPEC_DEMOTED_CODES should -- with a reason. It may not simply enter the wire set: an "
        f"unclassified code makes RECOVERY_BY_WIRE_CODE partial and sends every lookup to an "
        f"authored default."
    )

ERROR_CODE_MAPPING: dict[str, str] = {
    # Internal-only codes that occasionally leak to the wire when a raise site
    # uses a base class (AdCPError / AdCPNotFoundError / AdCPConfigurationError)
    # instead of a specific subclass. Mapped to the closest WIRE_STANDARD_CODES
    # entry so the wire stays spec-compliant. Raise sites can later migrate to
    # specific subclasses; the mappings stay as a safety net.
    "NOT_FOUND": "INVALID_REQUEST",
    # Entity-specific not-found codes the pinned spec enum does NOT define
    # (unlike CREATIVE_NOT_FOUND, which the enum defines and therefore passes
    # through untranslated). The typed subclasses exist for
    # recovery=correctable + guard-enforceability; both FORMAT_NOT_FOUND and
    # TASK_NOT_FOUND map to REFERENCE_NOT_FOUND — a missing referenced id is
    # AdCP 3.1.1's generic inaccessible-reference code, and MUST NOT mint a
    # custom `*_NOT_FOUND` (not INVALID_REQUEST — that is for malformed
    # requests).
    "FORMAT_NOT_FOUND": "REFERENCE_NOT_FOUND",
    "TASK_NOT_FOUND": "REFERENCE_NOT_FOUND",
    "INTERNAL_ERROR": "SERVICE_UNAVAILABLE",
    # Authentication / authorisation
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
        "FORMAT_NOT_FOUND",  # AdCPFormatNotFoundError; wire → REFERENCE_NOT_FOUND
        "TASK_NOT_FOUND",  # AdCPTaskNotFoundError; wire → REFERENCE_NOT_FOUND
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

# Every mapping target must be a standard code. A `raise`, not an `assert`:
# `python -O` deletes an assert, and an invariant with an off switch is not one.
# It stays at module scope rather than moving into the loader because it reads
# ERROR_CODE_MAPPING, which is defined below the loader's own return.
_NON_STANDARD_TARGETS = set(ERROR_CODE_MAPPING.values()) - set(WIRE_STANDARD_CODES)
if _NON_STANDARD_TARGETS:
    raise RuntimeError(f"ERROR_CODE_MAPPING contains non-standard targets: {_NON_STANDARD_TARGETS}")

# `ERROR_CODE_MAPPING` targets must also be CLASSIFIED, not merely standard. This
# used to be a separate check against RECOVERY_BY_WIRE_CODE; it is now implied by
# the drift raise above (wire <= recovery, by construction) plus
# _NON_STANDARD_TARGETS (targets <= wire), so keeping it would be a detector for a
# state two other invariants already make unreachable.

# Nothing may enter the spec supplement that the PIN does not classify. Distinct
# from the drift raise: that one asks whether the wire set is total, this one asks
# whether the supplement was populated from the pin rather than from wishful
# thinking. Stays at module scope — it reads _SPEC_SUPPLEMENT_CODES, defined below
# the loader.
_UNCLASSIFIED_SUPPLEMENT = _SPEC_SUPPLEMENT_CODES - set(RECOVERY_BY_WIRE_CODE)
if _UNCLASSIFIED_SUPPLEMENT:
    raise RuntimeError(
        f"Spec-supplement code(s) absent from the pinned enumMetadata: {_UNCLASSIFIED_SUPPLEMENT}. "
        f"The supplement exists because the SDK helper lags the pin — a code the PIN lacks does not "
        f"belong in it."
    )


def translate_error_code(code: str) -> str:
    """Translate a server-side error code to its wire-compliant equivalent.

    Codes listed in ERROR_CODE_MAPPING are translated to their standard SDK
    counterpart. All other codes pass through unchanged — codes are only
    rewritten when there is an explicit mapping entry. Compliance is
    enforced separately by the architecture guard.
    """
    return ERROR_CODE_MAPPING.get(code, code)


def to_wire_error_code(code: str) -> str:
    """Normalize a hand-built advisory code to a guaranteed-standard wire code.

    Like ``translate_error_code`` but, unlike it, GUARANTEES the result is in
    ``WIRE_STANDARD_CODES`` (the SDK's ``STANDARD_ERROR_CODES`` plus the
    pinned-spec supplement): an internal-only code that has no mapping
    entry (e.g. ``API_ERROR``, ``FLIGHT_NOT_FOUND``) would otherwise pass through
    ``translate_error_code`` verbatim and leak. Use this for ``errors[]``
    advisories, which serialize verbatim and never pass through the boundary
    translator that handles raised ``AdCPError``s. Anything still non-standard
    after translation collapses to ``SERVICE_UNAVAILABLE`` (the generic
    server-side advisory), so no internal code can reach the buyer.
    """
    translated = translate_error_code(code)
    return translated if translated in WIRE_STANDARD_CODES else "SERVICE_UNAVAILABLE"


def wire_advisory(
    code: str,
    message: str,
    *,
    field: str | None = None,
    suggestion: str | None = None,
) -> LibraryError:
    """Build an ``errors[]`` advisory entry with the recovery the PIN assigns its code.

    The ONE constructor for a per-item advisory. ``recovery`` is DERIVED, never
    chosen: pass the code that describes what happened and the buyer-facing retry
    semantics follow from ``RECOVERY_BY_WIRE_CODE``. That is the whole point —
    ``adcp.types.Error`` types ``code`` as a bare ``str`` and leaves ``recovery``
    free, so a hand-built advisory could pair a code with a recovery the pinned
    enumMetadata contradicts, and nothing downstream would catch it: advisories
    ride inside a SUCCESS response and never pass the boundary translator, so the
    pair here IS the wire contract.

    The code is normalized through ``to_wire_error_code`` first, so an
    internal-only code (``ADAPTER_ERROR``, ``API_ERROR``) can neither leak to the
    buyer nor carry a foreign classification.

    Populating ``recovery`` on every advisory is what the pin asks for:
    ``Error.recovery``'s own description says senders SHOULD populate it on every
    error from 3.1 onward, because a receiver that does not recognize the code
    MUST still be able to classify from ``recovery``.

    ``suggestion`` has no caller yet: the advisory sites migrated so far carry a
    message and a field, not a suggestion. It is here so the sites that DO carry
    one arrive through this constructor rather than around it.
    """
    wire_code = to_wire_error_code(code)
    return LibraryError(
        code=wire_code,
        message=message,
        # A subscript, not ``.get``: ``to_wire_error_code`` guarantees membership in
        # WIRE_STANDARD_CODES, and the drift raise above guarantees
        # WIRE_STANDARD_CODES <= RECOVERY_BY_WIRE_CODE. There is no unclassified
        # wire code left for a fallback to answer, so an authored default here
        # would be unreachable code asserting the opposite.
        recovery=RECOVERY_BY_WIRE_CODE[wire_code],
        field=field,
        suggestion=suggestion,
    )


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


# The spec Error model bounds retry_after to [1, 3600] seconds (clients clamp
# anyway); never emit more even when the underlying wait is longer. A spec
# constant, not an operational knob — deliberately not env-tunable.
RETRY_AFTER_MAX = 3600


def clamp_retry_after(seconds: float) -> int:
    """Clamp a raw retry_after to the spec Error model's [1, RETRY_AFTER_MAX] bound.

    The single home for the floor/ceiling every emitter shares — the idempotency
    policy's rejection branches and the egress seam's Retry-After passthrough.
    Callers layer any context-specific cap (e.g. an insert-rate window) on top.

    It lives here rather than beside either caller because this module already
    owns ``AdCPError.retry_after`` and the spec Error shape, so neither emitter
    ends up importing the other.
    """
    return min(max(1, math.ceil(seconds)), RETRY_AFTER_MAX)


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
    # Recovery follows the WIRE code, not the internal taxonomy: the base
    # INTERNAL_ERROR maps to SERVICE_UNAVAILABLE on the wire, whose pinned
    # enumMetadata classification is transient (#1430 review). Subclasses
    # whose wire code is pinned terminal declare terminal explicitly.
    _default_status_code: ClassVar[int] = 500
    _default_error_code: ClassVar[str] = "INTERNAL_ERROR"
    _default_recovery: ClassVar[RecoveryHint] = "transient"
    # Optional class-level suggestion default (#1417 round-8 review item 4): a subclass
    # whose every rejection shares one buyer fix hint (e.g. AUTH_REQUIRED →
    # "provide valid credentials") sets this so no raise site can forget the
    # graded top-level ``suggestion``. Per-raise ``suggestion=`` overrides.
    _default_suggestion: ClassVar[str | None] = None

    # Instance attributes — set in __init__ from _default_* unless overridden.
    # ``recovery`` is NOT among them: it is a read-only property, derived below.
    error_code: str
    status_code: int

    def __init__(
        self,
        message: str = "",
        *,
        error_code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
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
        self.suggestion = suggestion if suggestion is not None else type(self)._default_suggestion
        self.retry_after = retry_after
        self.context = context
        self.error_code = error_code if error_code is not None else type(self)._default_error_code
        self.status_code = status_code if status_code is not None else type(self)._default_status_code

    @property
    def recovery(self) -> RecoveryHint:
        """The buyer-facing retry classification for this error's WIRE code.

        Read-only and DERIVED — there is no way to say "I want terminal" except by
        raising a class whose wire code the pinned enumMetadata classifies terminal.
        Possession of the class is the proof. Before this, ``recovery=`` was a free
        constructor kwarg, so a raise site could pair any code with any
        classification and nothing in the system disagreed: the wire carried
        SERVICE_UNAVAILABLE + terminal (retry forever / do not retry) and a green
        test graded it.

        Derivation follows ``wire_error_code``, NOT ``error_code``: the buyer reads
        the translated code, so the classification must be the one the pin assigns
        to what they actually receive.

        This one keeps its ``.get``, unlike :func:`wire_advisory`'s subscript, and
        the difference is the DOMAIN. ``wire_advisory`` reads
        ``to_wire_error_code``'s output, which is guaranteed to be in
        WIRE_STANDARD_CODES. This reads ``ERROR_CODE_MAPPING.get(code, code)`` --
        a pass-through -- so its domain is whatever any raise site put in
        ``error_code``, which is literally unbounded: ``synthesize`` accepts an
        arbitrary string, and ``tool_error_logging`` passes
        ``type(error).__name__``, so ``"ValueError"`` can arrive here. Making this
        a subscript would turn the boundary error handler into a ``KeyError``
        raised while already handling a failure.
        """
        return RECOVERY_BY_WIRE_CODE.get(self.wire_error_code, type(self)._default_recovery)

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

        ``recovery`` is deliberately NOT a parameter here either. A synthesized
        error derives its classification from the code it was given, exactly like
        a typed raise: the code is the choice, the retry semantics follow.
        """
        return cls(
            message,
            error_code=error_code,
            status_code=status_code,
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
        envelope. Translation to ``WIRE_STANDARD_CODES`` happens at transport
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


class AdCPBlockedUrlError(AdCPValidationError):
    """Raised when a URL is refused by SSRF egress policy."""

    _default_message: ClassVar[str] = "URL resolves to a restricted range."

    def __init__(
        self,
        *,
        field: str | None = None,
        suggestion: str | None = None,
        context: ContextObject | dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            type(self)._default_message,
            field=field,
            suggestion=suggestion,
            context=context,
        )


class AdCPInvalidRequestError(AdCPValidationError):
    """A structurally invalid request graded as INVALID_REQUEST by the storyboard (400).

    Distinct from operation-level VALIDATION_ERROR failures. The AdCP storyboard
    defines the code per operation, so callers must use the exception class graded
    for that scenario rather than inferring the code from validation phase alone.
    Inherits 400 + correctable from AdCPValidationError.
    """

    _default_error_code: ClassVar[str] = "INVALID_REQUEST"


AUTH_REQUIRED_SUGGESTION = "Provide valid credentials (x-adcp-auth token)."


class AdCPAuthenticationError(AdCPError):
    """Missing or invalid authentication credentials (401).

    Emits the standard ``AUTH_REQUIRED`` wire code — the sole authentication
    error code in the AdCP 3.1 error-code enum and adcp 5.7
    ``STANDARD_ERROR_CODES``. Its enum description explicitly covers both
    "credentials missing" and "credentials presented but rejected", so it is
    the canonical code for every authentication failure.

    Recovery is ``correctable`` per the pinned AdCP error-code enum
    (``AUTH_REQUIRED.recovery == "correctable"``; released 3.1.0 agrees) —
    not the ``terminal`` base default. The enum carries operationally distinct
    sub-cases (missing credentials → retry; presented-but-rejected → escalate),
    but its single canonical ``recovery`` classification is ``correctable``,
    and the wire contract is graded against that enum (#1417,
    superseding the earlier "storyboards grade only the code" judgment).
    """

    _default_status_code: ClassVar[int] = 401
    _default_error_code: ClassVar[str] = "AUTH_REQUIRED"
    # Every authentication rejection shares one buyer fix hint, so the graded
    # top-level suggestion (error.json) can never be forgotten at a raise site
    # (#1417 round-8 review item 4: 11 of 12 raise sites emitted an empty suggestion).
    _default_suggestion: ClassVar[str | None] = AUTH_REQUIRED_SUGGESTION


class AdCPAuthRequiredError(AdCPAuthenticationError):
    """No authentication context present (401, AUTH_REQUIRED).

    Raised when the request contains no auth token at all. Inherits the
    standard ``AUTH_REQUIRED`` wire code from its parent.
    """


class AdCPAuthorizationError(AdCPError):
    """Authenticated but not authorized for this resource (403).

    Emits ``AUTH_REQUIRED`` with ``correctable`` recovery, matching the pinned
    AdCP error-code enum and ``AdCPAuthenticationError`` (#1417).
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


class AdCPNotFoundError(AdCPError):
    """Requested resource does not exist (404).

    Recovery=correctable: the wire code is INVALID_REQUEST (via
    ERROR_CODE_MAPPING), whose pinned enumMetadata classification is
    correctable — recovery follows the wire code (#1430 review).
    """

    _default_status_code: ClassVar[int] = 404
    _default_error_code: ClassVar[str] = "NOT_FOUND"


class AdCPAccountNotFoundError(AdCPNotFoundError):
    """Account not found by ID or natural key (404, ACCOUNT_NOT_FOUND).

    Recovery=terminal per the pinned enumMetadata for ACCOUNT_NOT_FOUND —
    declared explicitly (the AdCPNotFoundError parent is correctable to
    match its INVALID_REQUEST wire code).
    """

    _default_error_code: ClassVar[str] = "ACCOUNT_NOT_FOUND"


class AdCPAccountSetupRequiredError(AdCPError):
    """Account exists but requires setup before use (422, ACCOUNT_SETUP_REQUIRED)."""

    _default_status_code: ClassVar[int] = 422
    _default_error_code: ClassVar[str] = "ACCOUNT_SETUP_REQUIRED"


class AdCPAccountSuspendedError(AdCPError):
    """Account is suspended and cannot be used (403, ACCOUNT_SUSPENDED).

    Recovery=terminal per the pinned enumMetadata — declared explicitly
    (the base default is transient to match its SERVICE_UNAVAILABLE wire code).
    """

    _default_status_code: ClassVar[int] = 403
    _default_error_code: ClassVar[str] = "ACCOUNT_SUSPENDED"


class AdCPAccountPaymentRequiredError(AdCPError):
    """Account has outstanding payment requirements (402, ACCOUNT_PAYMENT_REQUIRED).

    Recovery=terminal: from the sales agent's perspective there is
    no in-band remediation — the buyer must settle the outstanding balance
    externally before resubmitting. Matches the BDD storyboard contract for
    UC-002 account-reference partition/boundary rows. Declared explicitly
    (the base default is transient to match its SERVICE_UNAVAILABLE wire code).
    """

    _default_status_code: ClassVar[int] = 402
    _default_error_code: ClassVar[str] = "ACCOUNT_PAYMENT_REQUIRED"


class AdCPConflictError(AdCPError):
    """Resource conflict, e.g. duplicate idempotency key (409).

    Recovery=transient per the pinned error-code.json enumMetadata (CONFLICT):
    a generic resource conflict (e.g. concurrent modification) is resolved by
    retrying with backoff. Subclasses whose specific code the enum classifies as
    correctable (ACCOUNT_AMBIGUOUS, IDEMPOTENCY_CONFLICT, IDEMPOTENCY_EXPIRED)
    override this (#1417).
    """

    _default_status_code: ClassVar[int] = 409
    _default_error_code: ClassVar[str] = "CONFLICT"


class AdCPAccountAmbiguousError(AdCPConflictError):
    """Natural key matches multiple accounts (409, ACCOUNT_AMBIGUOUS)."""

    _default_error_code: ClassVar[str] = "ACCOUNT_AMBIGUOUS"
    # ACCOUNT_AMBIGUOUS is correctable per the enum (the buyer disambiguates with
    # an explicit account_id) — override the transient CONFLICT parent (#1417).


class AdCPGoneError(AdCPError):
    """Resource previously existed but is no longer available (410).

    Recovery=correctable: the resource itself is gone, but the buyer can
    recover by referencing a different resource (a fresh proposal, a new
    media buy) and re-issuing the request.
    """

    _default_status_code: ClassVar[int] = 410
    _default_error_code: ClassVar[str] = "INVALID_STATE"


class AdCPBudgetExhaustedError(AdCPError):
    """Budget or spend limit has been reached (422).

    Recovery=terminal per the pinned error-code.json enumMetadata (BUDGET_EXHAUSTED):
    an exhausted budget cannot be recovered autonomously — an operator must add
    budget — so the buyer agent must not retry (#1417).
    """

    _default_status_code: ClassVar[int] = 422
    _default_error_code: ClassVar[str] = "BUDGET_EXHAUSTED"


class AdCPRateLimitError(AdCPError):
    """Too many requests (429)."""

    _default_status_code: ClassVar[int] = 429
    _default_error_code: ClassVar[str] = "RATE_LIMITED"


class AdCPAdapterError(AdCPError):
    """External adapter (GAM, etc.) failure (502)."""

    _default_status_code: ClassVar[int] = 502
    _default_error_code: ClassVar[str] = "SERVICE_UNAVAILABLE"


class AdCPConfigurationError(AdCPError):
    """Server-side configuration is broken (500).

    Two families of raise site, one meaning — this deployment is pointed at
    something wrong, and only an operator can repoint it:

    * local config: encrypted secrets that cannot be decrypted (key rotation,
      corruption, missing ENCRYPTION_KEY), a missing API key.
    * a REMOTE endpoint that is operator configuration — a registered creative
      or signals agent — refusing us, rejecting us with a terminal 4xx, or
      answering with something unparseable. The address came from this
      deployment, not from the buyer, so the buyer has no lever either way.

    Callers should NOT silently fall back. Recovery is ``terminal``: per the
    pinned enum the buyer "cannot resolve a seller-side deployment
    misconfiguration and MUST NOT auto-retry". Choosing this class IS how a
    raise site says terminal — do not hand-type ``recovery="terminal"`` onto a
    code the pin classifies otherwise. CONFIGURATION_ERROR is a
    _SPEC_SUPPLEMENT_CODES pass-through — it reaches the wire untranslated
    (#1430 review).

    NOT for a buyer-supplied URL: that is ``AdCPBlockedUrlError``. Telling a
    buyer the SELLER is misconfigured about an address they chose inverts the
    provenance.
    """

    _default_status_code: ClassVar[int] = 500
    _default_error_code: ClassVar[str] = "CONFIGURATION_ERROR"


class AdCPPersistedStateError(AdCPConfigurationError):
    """A persisted value is outside the vocabulary its column may hold (500).

    Raised wherever a persisted value cannot be published or stored: the ``status``
    write door refuses a value that would enter the column, the ``status`` read door
    refuses a value already in it, and the ``revision`` read door refuses an integer
    below the pinned minimum. The last is a bound rather than a vocabulary, and it
    reaches the buyer in both envelope layers — the pinned ``CONFIGURATION_ERROR``
    metadata permits that payload, so the contract is wider than "two doors of
    status" and this docstring says so rather than describing the narrower case it
    was written for.
    All are SELLER-side store defects — the buyer neither supplied the value nor can
    correct it — so this inherits ``CONFIGURATION_ERROR`` / ``terminal`` from
    ``AdCPConfigurationError`` rather than restating them. That is also what the
    pinned 3.1.1 ``enums/error-code.json`` metadata selects: ``VALIDATION_ERROR`` is
    ``correctable`` and advises "review error details and fix field values", advice
    the buyer cannot act on for data it does not own, and an invitation to retry a
    call that will fail identically.

    The message names the buy, the column and the legal member set, because that is
    what makes the defect actionable for the seller's operator, who is the only party
    who can fix it.

    It does NOT say the buyer never sees it — this docstring said that twelve lines
    above its own statement that the message reaches the buyer in both envelope
    layers. Both are true of the same string: it is written for the operator and it is
    delivered to the buyer, which is exactly why it names a column rather than a
    stack frame.
    """


class AdCPServiceUnavailableError(AdCPError):
    """Service or product temporarily unavailable (503).

    503 indicates a temporary outage in a downstream service the sales
    agent depends on. Recovery=transient so buyer agents retry rather
    than mutate the request.
    """

    _default_status_code: ClassVar[int] = 503
    _default_error_code: ClassVar[str] = "SERVICE_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Typed subclasses for spec-compliant error codes.
# ---------------------------------------------------------------------------
# Each subclass pins its wire error_code to a WIRE_STANDARD_CODES entry (SDK
# STANDARD_ERROR_CODES plus the pinned-spec supplement), so
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


class AdCPPackageNotFoundError(AdCPNotFoundError):
    """Package lookup failed within a media buy (404, PACKAGE_NOT_FOUND).

    Recovery=correctable: the buyer can correct by supplying the right
    package_id. Overrides the ``AdCPNotFoundError`` ``terminal`` default for
    the same reason as ``AdCPMediaBuyNotFoundError``.
    """

    _default_error_code: ClassVar[str] = "PACKAGE_NOT_FOUND"


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


class AdCPCreativeNotFoundError(AdCPNotFoundError):
    """Requested creative does not exist (404, wire CREATIVE_NOT_FOUND).

    ``CREATIVE_NOT_FOUND`` is a pinned-spec wire code (enums/error-code.json @
    04f59d2d5): correctable, and MANDATED uniformly for any creative_id not
    owned by the calling account — never distinguish "exists under another
    principal/tenant" from "does not exist" (anti-enumeration). It reaches the
    wire untranslated via the WIRE_STANDARD_CODES spec supplement.

    Recovery=correctable: the buyer can correct by supplying a valid creative_id
    (discoverable via list_creatives / sync_creatives).
    """

    _default_error_code: ClassVar[str] = "CREATIVE_NOT_FOUND"


class AdCPFormatNotFoundError(AdCPNotFoundError):
    """Requested creative format does not exist on the agent (404, wire → REFERENCE_NOT_FOUND).

    No standard ``FORMAT_NOT_FOUND`` SDK code exists, so the raw code is internal
    and translated to ``REFERENCE_NOT_FOUND`` at the wire boundary (AdCP 3.1.1
    ``error-code.json``: referenced identifier that does not exist or is not
    accessible — MUST not mint a custom ``*_NOT_FOUND``, the same rule
    ``AdCPTaskNotFoundError`` follows below). The gain over the bare
    ``AdCPNotFoundError`` is recovery=correctable + a typed identity.

    Recovery=correctable: the buyer can correct by supplying a valid format_id
    (discoverable via list_creative_formats).
    """

    _default_error_code: ClassVar[str] = "FORMAT_NOT_FOUND"


class AdCPTaskNotFoundError(AdCPNotFoundError):
    """Requested workflow task/step does not exist (404, wire → REFERENCE_NOT_FOUND).

    No standard ``TASK_NOT_FOUND`` SDK code exists, so the raw code is internal
    and translated to ``REFERENCE_NOT_FOUND`` at the wire boundary (AdCP 3.1.1
    ``error-code.json``: referenced identifier that does not exist or is not
    accessible — MUST not mint a custom ``*_NOT_FOUND``). The gain over the
    bare ``AdCPNotFoundError`` is recovery=correctable + a typed identity.

    Recovery=correctable: the buyer can correct by supplying a valid task_id
    (discoverable via list_tasks).
    """

    _default_error_code: ClassVar[str] = "TASK_NOT_FOUND"


class AdCPBudgetTooLowError(AdCPError):
    """Requested budget falls below product minimum (422, BUDGET_TOO_LOW)."""

    _default_status_code: ClassVar[int] = 422
    _default_error_code: ClassVar[str] = "BUDGET_TOO_LOW"


class AdCPCapabilityNotSupportedError(AdCPError):
    """Requested capability is not supported by this seller (422, UNSUPPORTED_FEATURE).

    .. note::
        **Spec-conformant.** The pinned AdCP error-code enum classifies
        ``UNSUPPORTED_FEATURE`` as ``correctable`` ("check
        get_adcp_capabilities and remove unsupported fields"), and we emit
        ``correctable`` — so this matches the spec, it is not a divergence.
        The buyer holds the recovery lever: they can fix the request by
        dropping the unsupported feature (e.g. removing ``property_list``
        targeting against an adapter that doesn't compile it).

        Only the adcp SDK's ``STANDARD_ERROR_CODES`` table classifies it
        ``terminal``; the SDK is not authoritative (the pinned spec enum is),
        so its table diverges from the spec here. If the SDK runtime ever
        starts enforcing ``terminal`` at the wire (rejecting our spec-correct
        ``correctable`` hint), reconcile with the SDK then.
    """

    _default_status_code: ClassVar[int] = 422
    _default_error_code: ClassVar[str] = "UNSUPPORTED_FEATURE"


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


class AdCPCreativeRejectedError(AdCPError):
    """Creative failed policy or technical validation (422, CREATIVE_REJECTED)."""

    _default_status_code: ClassVar[int] = 422
    _default_error_code: ClassVar[str] = "CREATIVE_REJECTED"


class AdCPBudgetExceededError(AdCPError):
    """Requested budget exceeds tenant or product ceiling (422, BUDGET_EXCEEDED)."""

    _default_status_code: ClassVar[int] = 422
    _default_error_code: ClassVar[str] = "BUDGET_EXCEEDED"


class AdCPProductUnavailableError(AdCPError):
    """Product is offline, deactivated, or otherwise unavailable (422, PRODUCT_UNAVAILABLE)."""

    _default_status_code: ClassVar[int] = 422
    _default_error_code: ClassVar[str] = "PRODUCT_UNAVAILABLE"


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


class AdCPInventoryUnavailableError(AdCPError):
    """Requested inventory is not available (422 → PRODUCT_UNAVAILABLE).

    recovery=correctable: the buyer can select different inventory. Carries the
    INVENTORY_UNAVAILABLE taxonomy as the class identity; the wire code is the
    standard PRODUCT_UNAVAILABLE.
    """

    _default_status_code: ClassVar[int] = 422
    _default_error_code: ClassVar[str] = "INVENTORY_UNAVAILABLE"


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
    so they always land in ``WIRE_STANDARD_CODES`` (the SDK's
    ``STANDARD_ERROR_CODES`` plus the pinned-spec supplement, e.g.
    ``CREATIVE_NOT_FOUND``).
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


# Canonical buyer-facing suggestions from error-code.json enumMetadata (AdCP 3.1.1):
# each code carries its own default hint, so a VALIDATION_ERROR must not borrow
# INVALID_REQUEST's text.
INVALID_REQUEST_SUGGESTION = "check request parameters and fix"
VALIDATION_ERROR_SUGGESTION = "review error details and fix field values"


def first_validation_error_field(validation_error: ValidationError) -> str | None:
    """Return the bracket-notation path of the first Pydantic error, or ``None``.

    Lets a transport boundary attach a structured ``field`` to the
    ``AdCPValidationError`` it raises, so the wire envelope carries the offending
    field path instead of only the rendered message. List indices render as
    ``[i]`` so boundary-derived paths such as ``packages[0].budget`` align with
    the ``packages[].budget`` field strings raised by the implementation layer.
    """
    errors = validation_error.errors()
    if not errors:
        return None
    parts: list[str] = []
    for loc in errors[0]["loc"]:
        if isinstance(loc, int):
            parts.append(f"[{loc}]")
        elif parts:
            parts.append(f".{loc}")
        else:
            parts.append(str(loc))
    return "".join(parts)


def build_validation_error_details(errors: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Project Pydantic errors into the buyer-safe structured detail shape."""
    return {
        "validation_errors": [
            {
                "loc": list(error.get("loc", ())),
                "msg": error.get("msg"),
                "type": error.get("type"),
            }
            for error in errors
        ]
    }


def normalize_to_adcp_error(exc: Exception) -> AdCPError:
    """Normalize untyped exceptions to typed AdCPError subclasses.

    Single source of truth for the wrapping applied at all three transport
    boundaries (MCP, A2A, REST). Already-typed ``AdCPError`` passes through
    unchanged. Pydantic ``ValidationError`` maps to a structured, sanitized
    ``AdCPValidationError``; other ``ValueError`` instances map to the plain
    validation error, ``PermissionError`` to ``AdCPAuthorizationError``, and
    anything else wraps in base ``AdCPError`` (INTERNAL_ERROR).
    """
    if isinstance(exc, AdCPError):
        return exc
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        return AdCPValidationError(
            errors[0].get("msg") if errors else "Request failed schema validation",
            field=first_validation_error_field(exc),
            suggestion=VALIDATION_ERROR_SUGGESTION,
            details=build_validation_error_details(errors),
        )
    if isinstance(exc, ValueError):
        return AdCPValidationError(str(exc))
    if isinstance(exc, PermissionError):
        return AdCPAuthorizationError(str(exc))
    return AdCPError(str(exc) or type(exc).__name__)
