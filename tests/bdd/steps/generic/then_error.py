"""Then steps for error assertions (failure, error codes, messages, suggestions).

These steps assert on ``ctx["error"]`` which is populated by When steps when
an operation fails. Errors are real exceptions from production code:
    - AdCPSalesAgentError subclasses (have .error_code, .message)
    - pydantic.ValidationError (mapped to VALIDATION_ERROR)
    - Other exceptions
"""

from __future__ import annotations

from pytest_bdd import parsers, then

from tests.bdd.steps._outcome_helpers import (
    payload_or_none,
    wire_entry_errors,
    wire_error_dict,
    wire_error_envelope_or_none,
    wire_field,
)

# ── Helpers ─────────────────────────────────────────────────────────


def _wire_code(ctx: dict) -> str | None:
    """Return the authoritative wire error code when a wire envelope was captured.

    ``dispatch_request`` stores the normalized ``TransportResult`` on
    ``ctx['result']`` and exposes the real two-layer envelope on
    ``wire_error_envelope`` (REST/A2A/MCP). The wire code is the buyer-facing
    contract; prefer it over the lossy reconstructed ``ctx['error']`` (which
    collapses distinct wire codes onto one exception class — e.g. yields
    ``RuntimeError`` for an unmapped code). Returns ``None`` on IMPL / no-wire
    scenarios so callers fall back to the reconstructed exception (#1417).

    Parsing lives in the harness (``TransportResult.wire_error_code``), not here:
    a step module that hand-rolls ``(envelope.get("errors") or [{}])[0]`` — or the
    envelope-level ``adcp_error`` mirror — is the disease this delegation removes.
    It carries origin/main's rule (a step module never reaches into the envelope
    through a hand-rolled ``getattr``) one layer further than the guarded accessor
    ``wire_error_envelope_or_none`` could: the accessor still hands the step a raw
    dict to index, while the harness reader resolves through the one locator that
    decides the code lives on the payload-layer ``errors[0]`` object. The ``| None``
    tolerance stays because callers branch on it (:348, :857, :896 and the
    uc019/uc006 sites); retiring that tolerance belongs to the typed-Then cluster
    (#1880).
    """
    result = ctx.get("result")
    return result.wire_error_code() if result is not None else None


def _wire_suggestion(ctx: dict) -> str | None:
    """Return the buyer-facing ``suggestion`` from the captured wire envelope.

    Mirrors ``_wire_code``: when the scenario dispatched through a wire transport
    (REST/A2A/MCP), the ``suggestion`` is the buyer-facing contract and must be
    read from the real envelope (via ``wire_error_envelope_or_none``, not a
    hand-rolled ``getattr``), not the lossy reconstructed
    ``ctx['error']``. STRICT error.json conformance: only the top-level
    ``suggestion`` on the error object (``errors[0]`` or ``adcp_error`` layer)
    counts — a suggestion buried in ``details`` is a conformance bug the
    harness surfaces, not masks (#1417). Same canonical lookup as
    ``TransportResult.assert_wire_error``. Returns ``None`` on IMPL / no-wire
    scenarios so callers fall back to the reconstructed exception
    (#1417).
    """
    from tests.harness.transport import extract_wire_suggestion

    envelope = wire_error_envelope_or_none(ctx)
    return extract_wire_suggestion(envelope)


def _wire_error_object(ctx: dict) -> dict | None:
    """Return the buyer-facing error object from the captured wire envelope.

    Mirrors ``_wire_code`` / ``_wire_suggestion``: when the scenario dispatched
    through a wire transport (REST/A2A/MCP), field-presence checks must read the
    real envelope's error object, not the lossy reconstructed ``ctx['error']``.
    Reads the ``errors[0]`` layer — the protocol position for per-error fields
    (``field``, ``details``, ``suggestion``) — through the harness reader
    ``TransportResult.wire_error_object``, so reader and assertion can never
    disagree about where the spec puts a field. That reader supersedes the
    ``wire_error_envelope_or_none`` + ``errors[0] or adcp_error`` pair origin/main
    used here: same "no hand-rolled ``getattr``" rule, but the layer choice is made
    once in ``locate_envelope_error`` instead of a second time in this module, and
    the ``adcp_error`` fallback is dropped deliberately — error.json defines the
    per-error fields on the payload-layer object, so reading the envelope-level
    mirror for them would grade the wrong region. Returns ``None`` on IMPL /
    no-wire scenarios so callers fall back to the reconstructed exception
    (#1417).
    """
    result = ctx.get("result")
    return result.wire_error_object() if result is not None else None


def _wire_of(error: object) -> dict | None:
    """``errors[0]`` from a WireError's envelope, or ``None`` if this is not one.

    A failed wire dispatch now raises ``tests.harness._base.WireError``, which carries
    the envelope the buyer received VERBATIM instead of a production error class
    rebuilt from those bytes. So the wire is reachable straight
    off the error object, and the extractors below read it without needing ``ctx``
    threaded through their forty call sites.

    Returns ``None`` for anything else, which is how the extractors keep serving their
    OTHER, legitimate population: an ``adcp.types.Error`` from a partial-success
    ``response.errors``, and genuine in-process exceptions (a pydantic ValidationError
    from a request that failed to build). Those are not reconstructions.
    """
    from tests.helpers import locate_envelope_error

    envelope = getattr(error, "envelope", None)
    if not isinstance(envelope, dict):
        return None
    # Resolved through THE locator, never by indexing errors[0] here: that function
    # is where "the payload-layer error object lives at errors[0]" is decided, and a
    # second place that knows it is how a reader and its assertion drift apart.
    return locate_envelope_error(envelope) or {}


def _get_error_code(error: object) -> str:
    """The error code the BUYER received: off the wire, or off an errors[] advisory.

    Two sources, both real; the three reconstructions this had are refusals now, for the
    same reason as in ``_get_error_dict`` above. ``AdCPSalesAgentError.error_code`` is the
    code of an exception the HARNESS caught, so reading it asserts that the object the test
    holds is the object the test holds. A bare ``pydantic.ValidationError`` mapped to
    "VALIDATION_ERROR" is worse: that exception is raised while BUILDING the request, so the
    payload never left the test process and production never ran. ``type(error).__name__``
    is not a protocol code at all -- with it, a scenario asserting a pinned code could only
    fail, but a scenario asserting "some error happened" passed on any exception whatsoever.
    """
    wire = _wire_of(error)
    if wire is not None:
        return str(wire.get("code") or "")
    from src.core.exceptions import AdCPSalesAgentError

    # adcp.types.Error model (from partial success response.errors) — payload data.
    if hasattr(error, "code") and not isinstance(error, Exception):
        return str(error.code)
    raise AssertionError(
        "RECONSTRUCTED-CODE READ: this step was about to take a code off "
        f"{type(error).__name__}, which the harness itself is holding -- no wire envelope was "
        "captured and this is not an errors[] advisory. "
        + (
            "An AdCPSalesAgentError's error_code is the code of a caught exception, not the one the buyer received. "
            if isinstance(error, AdCPSalesAgentError)
            else "A request-construction failure means production never ran. "
        )
        + "Assert on the wire."
    )


def _get_error_message(error: object) -> str:
    """Extract human-readable message from an exception or Error model."""
    wire = _wire_of(error)
    if wire is not None:
        return str(wire.get("message") or "")
    from src.core.exceptions import AdCPSalesAgentError

    if isinstance(error, AdCPSalesAgentError):
        return error.message
    # adcp.types.Error model
    if hasattr(error, "message") and not isinstance(error, Exception):
        return error.message
    return str(error)


def _get_error_dict(error: object) -> dict:
    """The error object a Then step reads, from the WIRE or from the payload — never rebuilt.

    Ten suggestion/field steps read through here, which is why the reconstruction lived in
    ONE place and could be removed in one place. Two of the four branches this had were
    reconstructions of an exception the harness caught, and both are now refusals:

    * an ``AdCPSalesAgentError`` re-serialized through ``envelope_for`` -- a second
      envelope built test-side from the exception, which derives ``message``,
      ``suggestion`` and ``recovery`` from ``CODE_TABLE`` and so can only ever AGREE WITH
      ITSELF. A scenario that reached no wire passed on it;
    * a bare ``{"code": ..., "message": ...}`` scraped off any object with those
      attributes, which is the same agreement with an even looser subject.

    ``then_error_code`` and ``then_error_recovery`` had the identical fallback and it was
    measured across the whole suite to have ZERO consumers, so both were deleted; there is
    no reason to expect a different answer here, and the refusal MEASURES it rather than
    assuming it. The two remaining branches are the two real sources: the wire envelope the
    dispatcher captured, and an ``adcp.types.Error`` taken out of a SUCCESSFUL response's
    ``errors[]`` -- the per-item advisory channel the pin declares, which is payload data
    and not a reconstruction.
    """
    wire = _wire_of(error)
    if wire is not None:
        return dict(wire)
    from src.core.exceptions import AdCPSalesAgentError

    if isinstance(error, AdCPSalesAgentError):
        raise AssertionError(
            "RECONSTRUCTED-ERROR READ: this step was about to grade an envelope rebuilt "
            f"test-side from {type(error).__name__}, because the dispatch captured no wire "
            "envelope. message/suggestion/recovery are all derived from CODE_TABLE, so such "
            "a read can only agree with itself and would pass for a request the seller never "
            "answered. Assert on the wire, or -- if this scenario grades a PER-ITEM advisory "
            "inside a successful response -- read it out of response.errors[] instead."
        )
    # adcp.types.Error model (from partial success response.errors) — has code,
    # message, suggestion, recovery, field as direct attributes. PAYLOAD data, not a
    # reconstruction: the pin declares errors[] as the per-item advisory channel.
    if hasattr(error, "code") and not isinstance(error, Exception):
        d: dict = {"code": error.code, "message": getattr(error, "message", "")}
        suggestion = getattr(error, "suggestion", None)
        if suggestion:
            d["suggestion"] = suggestion
        recovery = getattr(error, "recovery", None)
        if recovery is not None:
            d["recovery"] = recovery.value if hasattr(recovery, "value") else str(recovery)
        field = getattr(error, "field", None)
        if field:
            d["field"] = field
        return d
    raise AssertionError(
        "RECONSTRUCTED-ERROR READ (loose branch): this step was about to grade a "
        f"hand-built {{code, message}} scraped off {type(error).__name__}, which reached no "
        "wire and is not an errors[] advisory either. There is nothing here the buyer "
        "received. Assert on the wire."
    )


# ── Shared validation ───────────────────────────────────────────────


def _assert_meaningful_error(error: object) -> None:
    """Assert the error object carries meaningful error information.

    Validates that the error is either:
    - An AdCPSalesAgentError with a non-empty error_code string, OR
    - An adcp Error model with a non-empty string .code attribute, OR
    - Another Exception with a non-empty string representation.

    This rejects empty/placeholder errors that would make any
    "operation should fail" assertion tautological.
    """
    wire = _wire_of(error)
    if wire is not None:
        code = wire.get("code")
        assert isinstance(code, str) and code, f"wire error object has empty or non-string code: {code!r}"
        return
    from src.core.exceptions import AdCPSalesAgentError

    if isinstance(error, AdCPSalesAgentError):
        assert isinstance(error.error_code, str) and error.error_code, (
            f"AdCPSalesAgentError has empty or non-string error_code: {error.error_code!r}"
        )
        return

    # adcp.types.Error model (from partial success response.errors)
    code = getattr(error, "code", None)
    if code is not None and not isinstance(error, Exception):
        assert isinstance(code, str) and code, f"Error model has empty or non-string code: {code!r}"
        return

    if isinstance(error, (Exception, BaseException)):
        assert str(error), f"Exception has no message: {type(error).__name__}"
        return

    raise AssertionError(f"ctx['error'] is not an Exception or Error model: {type(error).__name__} = {error!r}")


# ── Wire error envelope (Error Verification Policy) ─────────────────


# ══════════════════════════════════════════════════════════════════════
# THE THREE ERROR PRIMITIVES — one assertion each, composed by the scenario
# ══════════════════════════════════════════════════════════════════════
#
#     Then the response arrives
#     And the response contains error code <code>
#     And the response error field is <field>
#
# One property per step, so a scenario composes the subset it cares about and
# nothing needs a second spelling when it wants a different combination. That is
# precisely how the two bundled steps below came to exist: "the wire error
# envelope should carry code X" grades arrived+code, and "the request is rejected
# with X naming field Y" grades arrived+code+field, so the second is a superset of
# the first and the pair must be kept in step by hand. Three orthogonal primitives
# cover the whole space; two bundles cover two points in it.
#
# The MECHANISM is unchanged and deliberately so — every one of these reads the
# real two-layer wire envelope through the harness's own ``assert_wire_error`` /
# the guarded ``_outcome_helpers`` accessors. Only the decomposition is new.
#
# The success-path twin of the third is "the response field <field> equals
# <value>"; between them, with <code>/<field>/<value> coming from the Examples
# column, they express nearly every assertion this suite needs.


@then("the response arrives")
def then_response_arrives(ctx: dict) -> None:
    """A response came back AT ALL — not nothing, and not a client-side exception.

    THE ANTI-VACUUM PRIMITIVE, and the reason it must be able to fail on its own.
    Every other assertion in this file is conditional on a response existing; if
    none does, they either skip their real check or grade a reconstructed
    exception. This step makes "the payload never reached the seller" a FAILURE
    that a scenario states explicitly, rather than a silent precondition.

    It fails in exactly the two ways a dispatch can produce no response:

    * nothing was dispatched -- no ``ctx['result']`` at all, so no transport was
      reached and there is nothing the buyer received;
    * the dispatch raised in the TEST PROCESS -- ``ctx['error']`` holds an
      ordinary exception rather than the ``WireError`` carrier a wire rejection
      produces. That is the defect removed at 53 sites: a
      pydantic error raised while BUILDING the request looks like a rejection to
      any assertion that only checks "is there an error", but production was
      never executed.

    Deliberately does NOT require a specific outcome: a success and a rejection
    both ARRIVE. Composing this with nothing else asserts only that the seller
    answered, which is a real and sometimes sufficient claim.
    """
    from tests.harness._base import WireError

    result = ctx.get("result")
    error = ctx.get("error")
    assert result is not None, (
        "No response arrived: nothing was dispatched, so no transport was reached and there is "
        f"nothing the buyer received to assert on. ctx['error']={error!r}"
    )
    if error is not None and not isinstance(error, WireError):
        raise AssertionError(
            "No response arrived: the dispatch raised in the TEST PROCESS rather than returning "
            f"something the buyer received -- {type(error).__name__}: {error}. A client-side "
            "exception is not a response; dispatch the raw payload so the seller answers."
        )


@then(parsers.re(r"the response contains error code (?P<code>[A-Z_0-9]+)$"))
def then_response_error_code(ctx: dict, code: str) -> None:
    """The response carries AdCP error ``code`` on the wire. One property only.

    Graded through ``TransportResult.assert_wire_error`` -- the single sanctioned
    error surface, which hard-asserts a real captured envelope before comparing
    and defaults ``recovery`` from the pinned CODE_TABLE, so the assertion is
    non-vacuous without the scenario restating retry semantics.

    Does NOT also assert the field: that is :func:`then_response_error_field`, and
    keeping them apart is the point of the decomposition.
    """
    ctx["result"].assert_wire_error(code)


@then(parsers.re(r"the response error field is (?P<field>[\w.\[\]]+)$"))
def then_response_error_field(ctx: dict, field: str) -> None:
    """The response's error detail names ``field``. One property only.

    ``field`` is the error.json pointer identifying WHICH request member was
    rejected -- the thing that makes a rejection actionable rather than sending
    the buyer to search its own payload. Read from the payload-layer error object
    through the one locator, and checked on BOTH layers by
    ``assert_envelope_shape`` (the ``adcp_error`` mirror must agree).

    Separate from the code assertion so a scenario that pins only the code does
    not have to pass a field it does not care about, and a scenario that pins the
    field does not need a second, wider step to exist.
    """
    from tests.helpers.envelope_assertions import assert_envelope_shape, locate_envelope_error

    envelope = wire_error_dict(ctx)
    error = locate_envelope_error(envelope)
    assert error is not None, f"Response carries no payload-layer error object to read a field from: {envelope!r}"
    code = error.get("code")
    assert isinstance(code, str), f"Response error object carries no code: {error!r}"
    assert_envelope_shape(envelope, code, recovery=error.get("recovery"), field=field)


# DELETED — the three bundled steps that used to live here
# (``the wire error envelope should carry code "<CODE>"``, its ``with recovery``
# variant, and ``the request is rejected with <CODE> naming field "<FIELD>"``).
#
# They graded arrived+code, arrived+code+recovery and arrived+code+field, so the
# third was a superset of the first and the set had to be kept in step by hand —
# the same duplication this epic keeps finding, expressed in Gherkin instead of
# Python. Replaced by the three orthogonal primitives above, which a scenario
# composes into whichever subset it needs; all 23 call sites were converted.
#
# The ``with recovery`` variant lost nothing in the conversion: ``assert_wire_error``
# already defaults recovery to the PINNED CODE_TABLE classification for the code,
# so naming it in the scenario restated what the code already determines.


@then(parsers.re(r"the response error issues include keyword (?P<keyword>\w+) for field (?P<field>[\w.\[\]]+)$"))
def then_response_error_issue(ctx: dict, keyword: str, field: str) -> None:
    """The rejection carries a STRUCTURED schema issue: *keyword* against *field*.

     The fourth primitive, and like the other three it grades exactly one property:
     that the seller's rejection came out of real schema validation and says which
     rule the value broke.

     WHY THIS IS THE DIRECT GRADING OF BR-RULE-209 INV-1, not a concession to it.
     INV-1 (BR-UC-001-discover-available-inventory.feature:1420) reads "inputs
     validated same as production" -- it says nothing about an exception object.
     The assertion this replaces required a ``pydantic.ValidationError`` INSTANCE,
     which was a PROXY: "an exception of the right class was constructed" standing
     in for "validation really ran". The proxy stopped being satisfiable once the
     rejection moved to the transport boundary, even though the property itself
     became MORE true -- the payload is now rejected by production's own schema
     boundary rather than by a model built in the test process
    .

     An ``issues[]`` entry carrying the JSON-Schema ``keyword`` that failed and a
     ``pointer`` at the offending member IS production's validation output. It is
     also STRICTER than the type check it replaces: a sandbox-simulated or
     hand-wrapped error would not carry that structure, whereas any
     ``ValidationError`` -- including one synthesised in the test process --
     satisfied an isinstance check.

     *field* is matched against the issue POINTER by trailing segment, so
     ``billing`` matches ``/accounts/0/Accounts/billing`` without the scenario
     having to spell out pydantic's union-branch naming.
    """
    from tests.helpers.envelope_assertions import locate_envelope_error

    envelope = wire_error_dict(ctx)
    error = locate_envelope_error(envelope)
    assert error is not None, f"Response carries no payload-layer error object: {envelope!r}"
    issues = error.get("issues") or []
    assert issues, (
        f"Response carries no issues[], so it does not say WHICH schema rule the value broke "
        f"-- there is no evidence real validation ran. Error object: {error!r}"
    )
    want = [c for c in field.split(".") if c]
    for issue in issues:
        if issue.get("keyword") != keyword:
            continue
        have = [c for c in str(issue.get("pointer", "")).replace("/", ".").split(".") if c]
        if len(have) >= len(want) and have[len(have) - len(want) :] == want:
            return
    raise AssertionError(
        f"No issue with keyword {keyword!r} pointing at {field!r}. Issues carried: "
        f"{[(i.get('keyword'), i.get('pointer')) for i in issues]}"
    )


@then("the operation should fail")
def then_operation_fails(ctx: dict) -> None:
    """Assert the operation resulted in an error.

    Checks two patterns:
    1. Exception-based: ctx["error"] set by dispatch on exception
    2. Partial success: response.errors non-empty (UC-004 delivery pattern)

    Both paths make a positive assertion that a real error object exists
    with meaningful error information — not just that a ctx key is set.
    """
    error = ctx.get("error")
    if error is not None:
        _assert_meaningful_error(error)
        return
    resp = payload_or_none(ctx)
    if resp is not None and hasattr(resp, "errors") and resp.errors:
        # Promote the first response error to ctx["error"] so downstream
        # Then steps (error_code, error_message) can find it.
        first_error = resp.errors[0]
        assert first_error is not None, "response.errors[0] is None — expected a concrete error object"
        _assert_meaningful_error(first_error)
        ctx["error"] = first_error
        return
    raise AssertionError(
        "Expected the operation to fail but no error was recorded. "
        f"ctx keys: {list(ctx.keys())}, response: {payload_or_none(ctx)!r}"
    )


@then("the entire sync operation fails")
def then_entire_sync_operation_fails(ctx: dict) -> None:
    """Assert the sync operation failed entirely -- no partial successes.

    Stronger than "the operation should fail": this step additionally verifies
    that the failure is total.  When a sync runs in strict validation mode
    (BR-RULE-172 INV-5), a single invalid catalog must cause the entire
    operation to be rejected -- the response must NOT contain any successfully
    processed items alongside the error.

    Asserts:
    1. An error was recorded with meaningful error information.
    2. If a response exists with a results/catalogs collection, NONE of the
       items were processed successfully (no partial success).
    """
    # ── Resolve the error object ────────────────────────────────────
    error = ctx.get("error")
    resp = payload_or_none(ctx)

    # Promote response.errors if no top-level error was captured
    if error is None and resp is not None and hasattr(resp, "errors") and resp.errors:
        first_error = resp.errors[0]
        assert first_error is not None, "response.errors[0] is None -- expected a concrete error"
        ctx["error"] = first_error
        error = first_error

    assert error is not None, (
        "Expected the entire sync operation to fail but no error was recorded. "
        f"ctx keys: {list(ctx.keys())}, response: {resp!r}"
    )

    # ── Verify it carries meaningful error information ──────────────
    _assert_meaningful_error(error)

    # ── Verify NO partial successes ─────────────────────────────────
    # "Entire sync fails" means the operation was rejected wholesale.
    # If a response exists with item-level results, none may have succeeded.
    if resp is not None:
        for attr in ("catalogs", "results", "items"):
            items = getattr(resp, attr, None)
            if items is None:
                continue
            successful = [
                item
                for item in items
                if getattr(item, "action", None) not in (None, "failed", "error", "rejected")
                or getattr(item, "status", None) == "success"
            ]
            assert not successful, (
                f"Expected entire sync to fail but found {len(successful)} "
                f"successfully processed item(s) in response.{attr} -- "
                f"this indicates partial success, not total failure. "
                f"BR-RULE-172 INV-5 requires the ENTIRE operation to fail."
            )


# ── Error code ───────────────────────────────────────────────────────


@then(parsers.parse('the error code should be "{code}"'))
def then_error_code(ctx: dict, code: str) -> None:
    """Assert the error code on the WIRE. No reconstructed fallback.

    There used to be one: with no wire envelope this read ``ctx['error']`` -- the
    exception the harness reconstructed, or an error an earlier step promoted there.
    It is gone because it had NO CONSUMERS. Measured across the whole BDD suite by
    making it raise: 2642 of 2643 passing scenarios already graded the wire, and the
    ONE that did not was BR-UC-004's adapter-error scenario, which asserted envelope
    failure for a per-item error the pin puts in the payload. Fixing that scenario
    (adca254f9) left this branch with nothing.

    Keeping it would keep the hazard it enabled: a scenario whose payload never
    reaches the seller still passes here, because a client-side exception
    reconstructs into something with a code. That is the vacuum ``the response
    arrives`` exists to make visible, and a fallback quietly undoes it.
    """
    actual = _wire_code(ctx)
    assert actual is not None, (
        f"Expected wire error code {code!r}, but no wire envelope was captured. The dispatch "
        f"either succeeded or failed before reaching a transport -- a client-side exception is "
        f"not a seller's answer. If this scenario grades a PER-ITEM error inside a successful "
        f"response, assert it there instead (see 'the <collection> entry carries error code')."
    )
    assert actual == code, f"Expected error code '{code}', got '{actual}'"


# ── Error message content (generic) ───────────────────────────────────


# DELETED — ``the error message should contain "<TEXT>"`` and the two
# ``the wire error message should contain / should not contain "<TEXT>"`` steps.
#
# The first read ``ctx['error']`` through ``_get_error_message``, which is the
# reconstructed-exception grade the Error Verification Policy refuses; no feature in
# this tree carries the sentence, and UC-019 keeps its own module-local override of it.
#
# The pair of wire-message steps were prose pins on text that is now a function of the
# error CODE through CODE_TABLE, so ``the response contains error code <CODE>`` already
# grades everything a positive substring could. The ABSENCE direction is covered, and
# strictly more widely, by ``the wire envelope should not carry the marker "<MARKER>"``
# below — it scans the FULL envelope, including ``errors[0].details`` where
# request-derived values now travel, rather than the message alone.


# ── Error message content (specific) ───────────────────────────────────


# ── Suggestion field ─────────────────────────────────────────────────


@then(parsers.parse('the suggestion should contain "{text}"'))
def then_suggestion_contains(ctx: dict, text: str) -> None:
    """Assert the buyer-facing ``suggestion`` contains the given text.

    Wire-first: ``_wire_suggestion`` reads the real envelope the dispatcher captured.
    The no-wire branch is NOT a reconstructed fallback any more — ``_get_error_dict``
    serves exactly two real sources (the wire object, and an ``adcp.types.Error`` out of
    a successful response's ``errors[]`` advisory channel) and REFUSES anything rebuilt
    test-side from a caught exception. So this step either grades what the buyer
    received, grades a per-item advisory the pin declares, or raises the
    RECONSTRUCTED-ERROR READ diagnosis; it cannot pass on a request the seller never
    answered.

    Kept rather than deleted with the prose-pinning message steps above because
    ``suggestion`` is the one free-form field a scenario still names directly and a live
    scenario binds this sentence (BR-UC-003 @T-UC-003-ext-t, "authorized"). When that
    scenario is re-expressed against the CODE, this step goes with the seven unbound
    ``the suggestion should advise ...`` steps below.
    """
    suggestion = _wire_suggestion(ctx)
    if suggestion is None:
        error = ctx.get("error")
        assert error is not None, "No error recorded in ctx"
        suggestion = _get_error_dict(error).get("suggestion")
    assert suggestion, f"Expected a non-empty suggestion carrying {text!r}, got {suggestion!r}"
    assert text.lower() in suggestion.lower(), f"Expected '{text}' in suggestion: {suggestion}"


@then(parsers.parse("the HTTP status is {status:d}"))
def then_http_status(ctx: dict, status: int) -> None:
    """The HTTP status REST answered with, read off the transport envelope.

    REST's wire failure marker is the status, so this is the one transport-specific
    property a REST-tagged scenario grades; on a transport that has no HTTP status the
    key is absent and the assertion fails, which is right -- the sentence has no meaning
    there.
    """
    envelope = ctx["result"].envelope
    assert envelope.get("status_code") == status, (
        f"expected HTTP {status}, got {envelope.get('status_code')!r} ({envelope!r})"
    )


@then(parsers.parse('the error recovery should be "{recovery}"'))
def then_error_recovery(ctx: dict, recovery: str) -> None:
    """Assert the error recovery hint on the WIRE. No reconstructed fallback.

    Same measurement as ``then_error_code``: this fallback had ZERO consumers across
    the whole BDD suite, so it graded nothing and could only ever have let a
    never-dispatched scenario pass.
    """
    envelope = wire_error_envelope_or_none(ctx)
    assert envelope is not None, (
        f"Expected a wire envelope to read recovery={recovery!r} from, and none was captured. "
        f"The reconstructed fallback that used to answer here had ZERO consumers across the "
        f"whole BDD suite and is gone -- see then_error_code for the measurement."
    )
    wire_code = _wire_code(ctx)
    assert wire_code, f"Expected wire error code when asserting recovery={recovery!r}: {envelope}"
    ctx["result"].assert_wire_error(wire_code, recovery=recovery)


@then('the error should include a "suggestion" field')
@then('the error should include "suggestion" field')
def then_error_has_suggestion(ctx: dict) -> None:
    """Assert error includes a non-empty suggestion — wire-first, reconstructed fallback.

    On a wire transport the suggestion is read from the real envelope (the
    buyer-facing contract); IMPL/no-wire scenarios fall back to the reconstructed
    ``ctx['error']`` (ztl6.6).
    """
    suggestion = _wire_suggestion(ctx)
    if suggestion is not None:
        assert suggestion, "Expected non-empty suggestion in wire envelope"
        return
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    d = _get_error_dict(error)
    assert "suggestion" in d, f"Expected 'suggestion' in error: {d}"
    assert d["suggestion"], "Expected non-empty suggestion"


@then(parsers.parse("the error details should name each rejected {subject_type} with its state"))
def then_error_details_name_each_subject(ctx: dict, subject_type: str) -> None:
    """Assert EVERY offending entity is named, not just the first one.

    The obligation this grades is a refusal that names its whole subject set. A
    request referencing two unassignable creatives that comes back naming one
    forces the buyer to fix, resubmit, and discover the second — a round trip per
    bad item, with no way to know how many remain.

    Graded on ``details.problems`` because that is where this repo puts per-ENTITY
    outcomes. AdCP 3.1.1 leaves the shape open: ``core/error.json`` types
    ``details`` as a free object and reserves ``issues[]`` for per-FIELD schema
    failures ("pointer", "keyword"), which a business-rule state refusal has
    neither of. The set is compared against the creative_ids the scenario
    referenced, so the assertion fails both ways — a missing subject AND an
    invented one.

    Reads ``errors[0]`` through the harness accessor, never a hand-rolled
    ``envelope["errors"][0]``: a second parser in a step module is free to drift
    from the one on the result object, and the two disagreeing is how an error
    assertion goes quietly vacuous.
    """
    referenced = ctx.get("referenced_creative_ids") or []
    assert referenced, (
        "no referenced_creative_ids on the context, so this step has nothing to compare "
        "against. It belongs after Givens that named the offending entities."
    )

    error_object = _wire_error_object(ctx)
    assert error_object is not None, (
        "no wire envelope was captured, so there is nothing buyer-facing to grade. A "
        "client-side exception is not a seller's answer."
    )

    details = error_object.get("details") or {}
    problems = details.get("problems") or []
    assert problems, (
        f"the refusal names no {subject_type}s at all: details={details!r}. The buyer cannot "
        f"tell WHICH of {sorted(referenced)} blocked the request."
    )

    named = {p.get("subject_id") for p in problems}
    assert named == set(referenced), (
        f"the refusal names {sorted(n for n in named if n)} but the request referenced "
        f"{sorted(referenced)}. Every offending {subject_type} must be reported together, so "
        f"the buyer fixes them in one pass rather than one round trip each."
    )
    for problem in problems:
        assert problem.get("subject_type") == subject_type, (
            f"expected subject_type {subject_type!r}, got {problem.get('subject_type')!r}"
        )
        assert problem.get("rejected_value"), (
            f"{problem.get('subject_id')!r} is named without the state that disqualified it, "
            f"so the buyer learns THAT it failed but not WHY"
        )


@then("the error should include a suggestion for how to fix the issue")
def then_error_has_fix_suggestion(ctx: dict) -> None:
    """Assert error includes an actionable suggestion for fixing the issue.

    Unlike then_error_has_suggestion (structural check), this step verifies
    the suggestion contains actionable language (use/try/check/provide/etc.)
    that tells the caller how to correct the problem.
    """
    # Wire-first: on a wire transport the suggestion is the buyer-facing contract.
    # Read it from the real envelope; fall back to the reconstructed ctx['error']
    # for IMPL/no-wire scenarios (ztl6.8).
    suggestion = _wire_suggestion(ctx)
    if suggestion is None:
        error = ctx.get("error")
        assert error is not None, "No error recorded in ctx"

        # Pydantic ValidationErrors carry the fix guidance inline in each field
        # error's ``msg`` (e.g. "Input should be 'operator', 'agent' or 'advertiser'")
        # rather than a separate ``suggestion`` field. That inline message IS the
        # actionable guidance, so accept it without the verb check below.
        from pydantic import ValidationError

        if isinstance(error, ValidationError):
            details = error.errors()
            assert details, "ValidationError has no field-level details to guide a fix"
            for detail in details:
                msg = detail.get("msg", "")
                assert isinstance(msg, str) and msg.strip(), f"ValidationError detail lacks fix guidance: {detail}"
            return

        suggestion = _get_error_dict(error).get("suggestion")
    assert suggestion, "Expected non-empty suggestion"
    # A fix suggestion must contain actionable guidance — a verb telling the
    # caller what to DO, not just describing the problem.
    suggestion_lower = suggestion.lower()
    # Split into words to avoid substring matches (e.g., "reset" matching "set")
    words = set(suggestion_lower.split())
    action_verbs = {
        "use",
        "try",
        "check",
        "provide",
        "include",
        "ensure",
        "remove",
        "specify",
        "set",
        "omit",
        "add",
        "verify",
    }
    found = words & action_verbs
    assert found, (
        f"Expected actionable fix suggestion with a verb ({', '.join(sorted(action_verbs))}), got: {suggestion}"
    )


# ── Suggestion content ───────────────────────────────────────────────
#
# UNBOUND, AND KEPT DELIBERATELY. No feature carries any of the seven sentences below —
# checked by literal grep and by matching each pattern against all 49534 sentences
# rendered from every feature's Examples through pytest-bdd's own FeatureParser. They are
# not dead weight, though, and deleting them would destroy the only record that these
# remediations were ever identified: each one names a specific thing a rejection should
# tell the buyer to DO, and no scenario grades any of them.
#
# Measured against CODE_TABLE, which owns the suggestion text a code resolves to:
#
#   - `then_suggestion_auth` is the weak one. AUTH_MISSING's own entry reads "provide
#     credentials via the auth header and retry", so its keyword test passes on the table
#     text alone. Asserting the CODE is strictly stronger — it pins WHICH code as well —
#     which is why nothing should bind this sentence as written.
#   - The other six demand wording NO code's entry carries. INVALID_REQUEST resolves to
#     "check request parameters and fix" and VALIDATION_ERROR to "review error details and
#     fix field values"; neither says DisclosurePosition, positions, duplicates, FormatId,
#     or agent_url. So those six record obligations production does not meet, which were
#     never ledgered because no scenario reaches them.
#
# Those scenarios are not written yet. When they are, the assertion should
# be re-expressed against the CODE plus the sanctioned wire oracle rather than against
# prose: core/error.json leaves `suggestion` free-form text the seller may reword, and
# these keyword tests would grade one seller's phrasing.


@then("the suggestion should advise providing authentication credentials")
def then_suggestion_auth(ctx: dict) -> None:
    """Assert suggestion mentions authentication credentials — wire-first, reconstructed fallback (ztl6.8)."""
    suggestion = _wire_suggestion(ctx)
    if suggestion is None:
        suggestion = _get_error_dict(ctx.get("error")).get("suggestion") or ""
    suggestion_lower = suggestion.lower()
    assert "credential" in suggestion_lower or "auth" in suggestion_lower, f"Expected auth suggestion: {suggestion}"


@then("the suggestion should provide valid parameter values")
def then_suggestion_valid_values(ctx: dict) -> None:
    """Assert suggestion provides valid parameter values — wire-first, reconstructed fallback (ztl6.8).

    Must reference both validity AND values.
    """
    suggestion = _wire_suggestion(ctx)
    if suggestion is None:
        suggestion = _get_error_dict(ctx.get("error")).get("suggestion") or ""
    assert suggestion, "Expected non-empty suggestion"
    suggestion_lower = suggestion.lower()
    # Must mention validity concept
    assert any(kw in suggestion_lower for kw in ("valid", "allowed", "accepted", "supported")), (
        f"Expected suggestion to indicate valid/allowed/accepted values, got: {suggestion}"
    )
    # Must mention values/options concept (not just "use valid X")
    assert any(kw in suggestion_lower for kw in ("values", "options", ":", "'", '"', "[", ",")), (
        f"Expected suggestion to enumerate or reference specific values, got: {suggestion}"
    )


@then("the suggestion should advise using valid DisclosurePosition enum values")
def then_suggestion_disclosure_enum(ctx: dict) -> None:
    """Assert suggestion mentions both DisclosurePosition AND valid values — wire-first (ztl6.8)."""
    raw = _wire_suggestion(ctx)
    if raw is None:
        raw = _get_error_dict(ctx.get("error")).get("suggestion") or ""
    suggestion = raw.lower()
    # Gherkin requires both concepts: "DisclosurePosition" AND "valid enum values"
    assert (
        "disclosureposition" in suggestion or "disclosure_position" in suggestion or "disclosure position" in suggestion
    ), f"Expected 'DisclosurePosition' in suggestion: {raw}"
    assert "valid" in suggestion or "allowed" in suggestion or "enum" in suggestion, (
        f"Expected valid/allowed/enum values language in suggestion: {raw}"
    )


@then("the suggestion should advise providing at least one position or omitting the filter")
def then_suggestion_positions_or_omit(ctx: dict) -> None:
    """Assert suggestion advises providing positions OR omitting the filter.

    Gherkin describes two alternatives — the suggestion should mention at least
    one alternative completely (position + provide/add, or omit/remove).
    Wire-first, reconstructed fallback (ztl6.8).
    """
    raw = _wire_suggestion(ctx)
    if raw is None:
        raw = _get_error_dict(ctx.get("error")).get("suggestion") or ""
    suggestion = raw.lower()
    has_provide_position = "position" in suggestion and any(
        w in suggestion for w in ("provide", "add", "include", "at least")
    )
    has_omit = "omit" in suggestion or "remove" in suggestion
    assert has_provide_position or has_omit, (
        f"Expected suggestion to advise providing positions or omitting filter: {raw}"
    )


@then("the suggestion should advise removing duplicate positions")
def then_suggestion_remove_dupes(ctx: dict) -> None:
    """Assert suggestion advises removing duplicates — wire-first, reconstructed fallback (ztl6.8).

    Both concepts required.
    """
    raw = _wire_suggestion(ctx)
    if raw is None:
        raw = _get_error_dict(ctx.get("error")).get("suggestion") or ""
    suggestion = raw.lower()
    # Gherkin says "removing duplicate" — both concepts must appear
    assert "duplicate" in suggestion, f"Expected 'duplicate' in suggestion: {raw}"
    assert any(w in suggestion for w in ("remove", "deduplicate", "dedup", "eliminate")), (
        f"Expected removal action in suggestion: {raw}"
    )


@then("the suggestion should advise providing at least one FormatId or omitting the filter")
def then_suggestion_format_id_or_omit(ctx: dict) -> None:
    """Assert suggestion advises providing FormatId OR omitting the filter.

    Same pattern as positions_or_omit — one complete alternative required.
    Wire-first, reconstructed fallback (ztl6.8).
    """
    raw = _wire_suggestion(ctx)
    if raw is None:
        raw = _get_error_dict(ctx.get("error")).get("suggestion") or ""
    suggestion = raw.lower()
    has_provide_format = ("formatid" in suggestion or "format_id" in suggestion or "format id" in suggestion) and any(
        w in suggestion for w in ("provide", "add", "include", "at least")
    )
    has_omit = "omit" in suggestion or "remove" in suggestion
    assert has_provide_format or has_omit, f"Expected suggestion to advise providing FormatId or omitting filter: {raw}"


@then("the suggestion should advise including agent_url (URI) and id fields")
def then_suggestion_agent_url_id(ctx: dict) -> None:
    """Assert suggestion advises including both agent_url AND id fields — wire-first (ztl6.8)."""
    import re

    suggestion = _wire_suggestion(ctx)
    if suggestion is None:
        suggestion = _get_error_dict(ctx.get("error")).get("suggestion") or ""
    assert suggestion, "Expected non-empty suggestion"
    suggestion_lower = suggestion.lower()
    assert "agent_url" in suggestion_lower or "uri" in suggestion_lower, (
        f"Expected agent_url/URI in suggestion: {suggestion}"
    )
    # Use word-boundary match to avoid false positives on "invalid", "bidder", etc.
    assert re.search(r"\bid\b", suggestion_lower), (
        f"Expected standalone 'id' field reference in suggestion: {suggestion}"
    )


# ── No error raised ─────────────────────────────────────────────────


@then("no error should be returned")
def then_no_error_returned(ctx: dict) -> None:
    """Assert no error was returned (synonym for no error raised)."""
    assert "error" not in ctx, f"Expected no error but got: {ctx.get('error')}"


@then(parsers.parse('no error should be raised for "{value}"'))
def then_no_error_for_value(ctx: dict, value: str) -> None:
    """Assert no error was raised for a specific value (silent exclusion)."""
    assert "error" not in ctx, f"Expected no error for '{value}' but got: {ctx.get('error')}"


# ── Validation error (sandbox) ───────────────────────────────────────


@then("the response should indicate a validation error")
def then_validation_error(ctx: dict) -> None:
    """Assert response indicates a validation error — wire-first, reconstructed fallback.

    Wire-first via the sanctioned surface: when a wire envelope was captured,
    grade it through ``ctx['result'].assert_wire_error`` (VALIDATION_ERROR is a
    canonical pinned code, so this is the invariant-blessed check, not a
    hand-rolled one). Presence of that envelope is read through the single guarded
    accessor ``wire_error_envelope_or_none`` (``_outcome_helpers.py``) rather than
    a hand-rolled ``result.wire_error_envelope`` — it returns ``None`` both when no
    ``TransportResult`` exists and when one exists without a real envelope, which is
    exactly the pair of cases this step must route to the fallback (and is the form
    ``assert_wire_error`` requires, since it reads the real envelope specifically).
    Only when no wire exists — the dispatch-exception path, where
    ``dispatch_request`` never produced a ``TransportResult`` — fall back to the
    reconstructed ``ctx['error']``. The fallback stays because that path has no
    ``ctx['result']`` to assert against; it is not a second wire mechanism.

    Envelope PRESENCE is read through ``wire_error_envelope_or_none`` — the tolerant
    guarded accessor, which is the one place that knows whether the bytes are a real
    wire capture — never a direct ``result.wire_error_envelope`` read. It also folds
    in the ``result is not None`` half of the old condition (it returns ``None`` when
    there is no result), so the branch means exactly what it meant before: grade the
    wire when one exists, otherwise grade the reconstructed error. Neither path can
    return without a verdict.
    """
    if wire_error_envelope_or_none(ctx) is not None:
        ctx["result"].assert_wire_error("VALIDATION_ERROR")
        return
    error = ctx.get("error")
    assert error is not None, "Expected a validation error"
    assert _get_error_code(error) == "VALIDATION_ERROR", f"Expected VALIDATION_ERROR, got {_get_error_code(error)}"


@then("the error should be a real validation error, not simulated")
def then_real_validation_error(ctx: dict) -> None:
    """Assert the error is a real Pydantic validation error, not a simulated one.

    Two-part contract. PRIMARY (wire): when a wire envelope was captured, the
    buyer-facing code must be VALIDATION_ERROR, graded through the sanctioned
    ``assert_wire_error`` surface. SECONDARY (type): the caught exception must be a
    real ``pydantic.ValidationError`` with per-field details, distinguishing it from
    ``AdCPValidationError`` (our wrapper) or a sandbox-simulated error.

    The type check CANNOT be replaced by a wire assertion: both a raw
    ``pydantic.ValidationError`` and an ``AdCPValidationError`` collapse to the same
    VALIDATION_ERROR wire code, so nothing on the wire distinguishes "real" from
    "wrapped/simulated". Hence it stays as an explicit secondary check rather than
    being dropped — and it is not a reconstructed-envelope grade, so it is not the
    anti-pattern the Error Verification Policy targets.

    As in ``then_validation_error``, envelope presence comes from the tolerant guarded
    accessor ``wire_error_envelope_or_none`` (``_outcome_helpers.py``) rather than a
    hand-rolled ``result.wire_error_envelope`` read: it returns ``None`` both when no
    ``TransportResult`` exists and when one carries no real envelope — the same pair of
    cases the old presence guard covered, and the form ``assert_wire_error`` needs (it
    reads the real envelope, not a synthesized one). The wire grade stays CONDITIONAL —
    tightening it to "there must be an envelope" would fail every no-wire dispatch of
    this step — while the type check below is unconditional, so the step always reaches
    a verdict.
    """
    from pydantic import ValidationError

    if wire_error_envelope_or_none(ctx) is not None:
        ctx["result"].assert_wire_error("VALIDATION_ERROR")

    error = ctx.get("error")
    assert error is not None, "Expected an error"
    assert isinstance(error, ValidationError), (
        f"Expected a real pydantic.ValidationError, got {type(error).__name__}: {error}"
    )
    assert error.errors(), "Expected ValidationError with field-level error details"


# ── Generic field presence / value ──────────────────────────────────


# Fields defined at the TOP LEVEL of the error.json protocol schema. Presence of
# these MUST be asserted at the top level of the wire error object — a copy buried
# in the free-form ``details`` dict does NOT satisfy the protocol contract (same
# burial disease removed from extract_wire_suggestion, #1417/ioni).
_ERROR_JSON_TOP_LEVEL_FIELDS = frozenset(
    {"code", "message", "field", "suggestion", "retry_after", "issues", "details", "recovery"}
)


@then(parsers.parse('the error should include "{field}" field'))
def then_error_includes_field(ctx: dict, field: str) -> None:
    """Assert the error includes a named field with a non-empty value — wire-first.

    When the scenario dispatched through a wire transport, read the field from
    the real wire envelope's error object (the buyer-facing contract); otherwise
    fall back to the reconstructed ``ctx['error']`` for IMPL/no-wire scenarios.

    For fields defined at the top level of error.json (see
    ``_ERROR_JSON_TOP_LEVEL_FIELDS``) the assertion requires the TOP-LEVEL position
    only — a value buried in ``details`` is a protocol-conformance violation, not a
    pass. The ``details`` alternative is kept only for genuinely detail-scoped keys.
    """
    protocol_top_level = field in _ERROR_JSON_TOP_LEVEL_FIELDS
    wire = _wire_error_object(ctx)
    if wire is not None:
        wire_details = wire.get("details") or {}
        has_top = bool(field in wire and wire[field])
        has_detail = bool(field in wire_details and wire_details[field])
        has_field = has_top if protocol_top_level else (has_top or has_detail)
        assert has_field, (
            f"Expected wire error to include non-empty '{field}' field"
            + (" at the protocol top level (not in details)" if protocol_top_level else "")
            + f". Wire error keys: {list(wire.keys())}, details keys: {list(wire_details.keys())}"
        )
        return
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    d = _get_error_dict(error)
    # Also check details sub-dict and direct attributes
    details = getattr(error, "details", None) or {}
    has_top = bool((field in d and d[field]) or (hasattr(error, field) and getattr(error, field)))
    has_detail = bool(field in details and details[field])
    has_field = has_top if protocol_top_level else (has_top or has_detail)
    assert has_field, (
        f"Expected error to include non-empty '{field}' field"
        + (" at the protocol top level (not in details)" if protocol_top_level else "")
        + f". Error dict keys: {list(d.keys())}, details keys: {list(details.keys())}"
    )


@then(parsers.parse('the error should include "{field}" field with value "{value}"'))
def then_error_field_with_value(ctx: dict, field: str, value: str) -> None:
    """Assert the error includes a named field matching the expected value — wire-first.

    When the scenario dispatched through a wire transport, read the field from the
    real wire error object (``errors[0]``, the buyer-facing contract) — this is what
    the buyer actually receives, and grading only the reconstructed exception (as
    this step previously did) verifies the lossy reconstruction layer, not the wire
    (Error Verification Policy). Fall back to the reconstructed ``ctx['error']`` only
    on the IMPL/no-wire dispatch-exception path. Compares as strings for cross-type
    compatibility (enum ``.value``, int, etc.).
    """
    wire = _wire_error_object(ctx)
    if wire is not None:
        actual = wire.get(field)
        if actual is None:
            actual = (wire.get("details") or {}).get(field)
        assert actual is not None, (
            f"Expected wire error to include '{field}' field but it was not found. Wire error keys: {list(wire.keys())}"
        )
        actual_str = actual.value if hasattr(actual, "value") else str(actual)
        assert actual_str == value, f"Expected wire {field}='{value}', got '{actual_str}'"
        return
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    actual = _resolve_error_field(error, field)
    assert actual is not None, (
        f"Expected error to include '{field}' field but it was not found. Available: {_available_error_fields(error)}"
    )
    # Compare as strings for cross-type compatibility (enum .value, int, etc.)
    actual_str = actual.value if hasattr(actual, "value") else str(actual)
    assert actual_str == value, f"Expected {field}='{value}', got '{actual_str}'"


# ── Error details assertions ────────────────────────────────────────


def _scalar_leaves(value: object) -> list[str]:
    """Every scalar inside a details value, stringified, at any nesting depth.

    A details value is a list of SCALARS for the simple cases (duplicate ids, missing
    ids) but a list of RECORDS when one violation needs more than one field to describe
    it -- ``geo_overlaps`` carries ``{include, exclude, values}`` per conflicting pair.
    Flattening lets a scenario name any one of those identifiers, which is what the
    contract asks for: assert a value the SCENARIO supplied. Stringifying the record
    itself (the previous behaviour) could only ever match a dict repr, which no scenario
    would write.
    """
    if isinstance(value, dict):
        return [leaf for item in value.values() for leaf in _scalar_leaves(item)]
    if isinstance(value, (list, tuple)):
        return [leaf for item in value for leaf in _scalar_leaves(item)]
    return [str(value)]


@then(parsers.parse('the wire error details should include {key} "{value}"'))
def then_wire_error_details_include(ctx: dict, key: str, value: str) -> None:
    """Assert ``errors[0].details[key]`` carries ``value`` ON THE WIRE.

    The wire variant of the reconstructed-error step below, and the one to prefer:
    ``details`` is where request-derived values legitimately reach the buyer now that
    the message is a function of the error CODE through CODE_TABLE. Reading the real
    envelope rather than ``ctx["error"]`` keeps the assertion on what the buyer
    actually received.

    Accepts a scalar (equality) or a list (membership), because a details value that
    names several offending items — duplicate ids, unsupported values — is naturally
    an array and the scenario names ONE of them.
    """
    error_object = ctx["result"].wire_error_object()
    assert error_object is not None, "no wire error object captured"
    details = error_object.get("details") or {}
    assert key in details, f"expected {key!r} in errors[0].details; keys present: {sorted(details)}"
    actual = details[key]
    if isinstance(actual, (list, tuple)):
        assert value in _scalar_leaves(actual), f"expected {value!r} among errors[0].details[{key!r}] = {actual!r}"
    else:
        assert str(actual) == value, f"expected errors[0].details[{key!r}] == {value!r}, got {actual!r}"


@then(parsers.parse('the wire error object carries no "{key}" key'))
def then_wire_error_key_absent(ctx: dict, key: str) -> None:
    """Assert *key* is absent from BOTH layers of the wire error object.

    The complement of the marker scan, and it catches what a marker scan CANNOT. A marker
    scan finds a leak of a VALUE the caller can name in advance; some forbidden disclosures
    carry no such value. AdCP 3.1.1 L1/security.mdx, on the IDEMPOTENCY_CONFLICT body: "A
    ``field`` json-pointer hint seems harmless but reveals schema shape (e.g.,
    ``/packages/0/budget`` tells an attacker the victim's payload had a budget in the first
    package). Sellers MUST NOT emit one." A bare pointer like ``accounts[0].brand.domain``
    echoes nothing the buyer sent, so it slips a value-marker scan while still being the
    disclosure the spec refuses -- measured, not reasoned: production emitting exactly that
    left this scenario GREEN until this step existed.

    Both layers, for the reason ``assert_envelope_shape`` checks ``recovery`` on both: a
    disclosure that reaches only ``adcp_error`` is still on the wire the buyer reads.

    ``wire_error_dict`` is the LOUD accessor: a dispatch that captured no envelope raises
    with that diagnosis rather than handing this check a ``None`` to pass against. So the
    step cannot be satisfied by "no error happened" -- it needs a real error envelope that
    simply does not carry *key*.
    """
    from tests.helpers.envelope_assertions import locate_envelope_error, locate_envelope_mirror

    envelope = wire_error_dict(ctx)
    error = locate_envelope_error(envelope)
    assert error is not None, f"no errors[0] object in the wire envelope to check for {key!r}: {envelope!r}"
    assert key not in error, f"errors[0] carries a forbidden {key!r}: {error[key]!r} (full entry: {error!r})"
    # Both layers through their own LOCATORS, never a direct ``envelope.get("adcp_error")``.
    # The mirror is a protocol position, so where it lives is the harness's answer to give
    # once (tests/unit/test_architecture_bdd_wire_discipline.py check (c)); this step only
    # asks the containment question.
    mirror = locate_envelope_mirror(envelope)
    assert key not in mirror, f"adcp_error carries a forbidden {key!r}: {mirror[key]!r} (full mirror: {mirror!r})"


@then(parsers.parse('the wire envelope should not carry the marker "{marker}"'))
def then_wire_envelope_marker_absent(ctx: dict, marker: str) -> None:
    """Assert ``marker`` appears NOWHERE in the wire error envelope.

    Replaces the message-scoped ``should not contain`` check and is strictly stronger:
    it scans the FULL envelope, so it also covers ``errors[0].details`` — which is
    exactly where request-derived values now travel. A RootModel stringified into
    details would render ``root=...`` there, so this check becomes MORE load-bearing
    after that move, not less.

    Not a prose pin: it asserts the ABSENCE of a leak. Nothing derives this marker from
    the error code, so there is no code/sentence tautology to guard against.

    The envelope comes from ``wire_error_dict`` — the LOUD guarded accessor — not a
    direct ``result.wire_error_envelope`` read: this step's whole claim is about what
    the buyer received on the wire, so a dispatch that captured no envelope must raise
    with that diagnosis rather than hand the scan a ``None`` to say nothing about.
    """
    from tests.helpers import assert_no_marker_in_envelope

    assert_no_marker_in_envelope(wire_error_dict(ctx), marker)


@then(parsers.parse("the error details should include {key:w} {value:S}"))
def then_error_details_include_unquoted(ctx: dict, key: str, value: str) -> None:
    """Assert error.details contains a key with the given value (numeric/unquoted).

    Handles numeric coercion: if the expected value looks like a number,
    compare numerically. Otherwise compare as strings.

    Field TYPES matter here, they are not decoration. Written as the bare
    ``{key} {value}`` this sentence is ``.+? .+?`` anchored to the prefix, so it
    swallows every PROSE continuation of "the error details should include ..."
    too — "... policy_id and a non-empty reasons array", "... a policy_url where
    the full policy can be reviewed", "... supported_versions as a non-empty
    array". For those it binds nonsense (``key='policy_id'``,
    ``value='and a non-empty reasons array'``) and can only ever fail, while
    ALSO shadowing the domain step that does grade the obligation — the disease
    ``tests/unit/test_architecture_bdd_no_shadowed_steps.py`` exists to remove
    (GH #1941: one Gherkin sentence has exactly one meaning).

    ``{key:w}`` (``\\w+``, one identifier) and ``{value:S}`` (one non-whitespace
    token) pin the step to the shape it was written for — an identifier followed
    by a single scalar, quoted or not. Every scalar site in ``tests/bdd/features``
    still binds with identical arguments (``minimum_budget 500``,
    ``current_version 1``, ``currency "USD"``, ``resource_id "mb-789"``); the
    prose sentences no longer bind here, leaving each to its own domain step.
    """
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    details = _get_error_details(error)
    assert key in details, f"Expected '{key}' in error details. Available keys: {list(details.keys())}"
    actual = details[key]
    _assert_detail_value_matches(key, actual, value)


@then(parsers.parse('the error "details" object should include "{key}" with value {value:d}'))
def then_error_details_object_numeric(ctx: dict, key: str, value: int) -> None:
    """Assert error.details contains a key with an integer value.

    Feature-file pattern: 'the error "details" object should include "minimum_budget" with value 500'
    Delegates to the same _get_error_details / _assert_detail_value_matches helpers
    as the unquoted-key variant above.
    """
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    details = _get_error_details(error)
    assert key in details, f"Expected '{key}' in error details. Available keys: {list(details.keys())}"
    actual = details[key]
    _assert_detail_value_matches(key, actual, str(value))


@then(parsers.parse('the error "details" object should include "{key}" with value "{value}"'))
def then_error_details_object_string(ctx: dict, key: str, value: str) -> None:
    """Assert error.details contains a key with a string value.

    Feature-file pattern: 'the error "details" object should include "currency" with value "USD"'
    """
    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    details = _get_error_details(error)
    assert key in details, f"Expected '{key}' in error details. Available keys: {list(details.keys())}"
    actual = details[key]
    assert str(actual) == value, f"Expected details['{key}'] = '{value}', got '{actual}'"


@then(parsers.parse('the "{field}" value should match ISO 4217 alphabetic format'))
def then_field_matches_iso4217(ctx: dict, field: str) -> None:
    """Assert the given field value in error details matches ISO 4217 format.

    ISO 4217 alphabetic codes are exactly 3 uppercase ASCII letters (e.g., USD, EUR, GBP).
    """
    import re

    error = ctx.get("error")
    assert error is not None, "No error recorded in ctx"
    details = _get_error_details(error)
    actual = details.get(field)
    assert actual is not None, f"Field '{field}' not found in error details. Available keys: {list(details.keys())}"
    assert isinstance(actual, str), f"Expected '{field}' to be a string, got {type(actual).__name__}: {actual!r}"
    assert re.fullmatch(r"[A-Z]{3}", actual), (
        f"Expected '{field}' value '{actual}' to match ISO 4217 alphabetic format "
        "(exactly 3 uppercase ASCII letters, e.g., USD)"
    )


# ── Terminal failure ────────────────────────────────────────────────


@then("the response should indicate a terminal failure")
def then_terminal_failure(ctx: dict) -> None:
    """Assert the operation failed with a terminal (non-recoverable) error.

    Verifies both that an error occurred and that its recovery hint is
    'terminal' -- meaning the buyer cannot retry with corrected input.

    Wire-first. This step previously had NO wire path at
    all: it read recovery off the reconstructed ``ctx['error']``, and its final
    branch fell off the end asserting nothing, on the reasoning quoted below
    that a non-AdCP exception is terminal anyway. That made it the step most
    exposed to a change in what ``ctx['error']`` holds -- an object that is not
    an AdCPSalesAgentError satisfied neither branch and the scenario passed having graded
    no recovery hint whatsoever. On a wire transport the buyer-facing hint now
    comes from the envelope, where a missing or wrong recovery fails.
    """
    result = ctx.get("result")
    wire_code = result.wire_error_code() if result is not None else None
    if wire_code is not None:
        # The code is read from the wire (this step names none); the graded claim
        # is that its recovery is terminal, pinned on both envelope layers.
        result.assert_wire_error(wire_code, recovery="terminal")
        return

    error = ctx.get("error")
    assert error is not None, (
        "Expected a terminal failure but no error was recorded. "
        f"ctx keys: {list(ctx.keys())}, response: {payload_or_none(ctx)!r}"
    )
    _assert_meaningful_error(error)
    from src.core.exceptions import AdCPSalesAgentError

    if isinstance(error, AdCPSalesAgentError):
        # Both sides' wire checks are kept, because they cover different states.
        # The early return above handles the ordinary wire case (an envelope whose
        # ``errors[0].code`` is readable). Reaching here with an envelope still
        # present means the code was unreadable — a degenerate envelope — and
        # origin/main's check is what grades recovery there: ``error`` is the
        # harness's RECONSTRUCTION and its ``.recovery`` is derived from its own
        # class, so asserting on it compares the derivation against itself and
        # would pass under any value the wire actually carried. Only with no
        # envelope at all does the reconstruction become the product worth
        # grading, and then the class check is all that level can offer.
        wire = _wire_error_object(ctx)
        if wire is not None:
            actual = wire.get("recovery")
            assert actual == "terminal", f"Expected terminal recovery on the wire, got {actual!r}: {wire}"
        else:
            assert error.recovery == "terminal", f"Expected terminal recovery, got '{error.recovery}'"
    elif hasattr(error, "recovery"):
        recovery = error.recovery.value if hasattr(error.recovery, "value") else str(error.recovery)
        assert recovery == "terminal", f"Expected terminal recovery, got '{recovery}'"
    # If the error type doesn't carry recovery info, the error itself is
    # sufficient -- non-AdCP exceptions are terminal by nature.


# ── No records created (DB state assertions) ────────────────────────


@then("no database records should be created")
def then_no_db_records_created(ctx: dict) -> None:
    """Assert that no new database records were created by the operation.

    For create operations: verifies no media buy was persisted.
    Uses the media_buy_id from the response (if any) or checks that no
    new records exist beyond what was set up by Given steps.
    """
    _assert_no_new_media_buy(ctx)


@then("no new media buy should have been created")
def then_no_new_media_buy(ctx: dict) -> None:
    """Assert no new media buy record was persisted in the database."""
    _assert_no_new_media_buy(ctx)


_ADAPTER_CREATE_METHODS = ("create_order", "create_line_item", "create_media_buy")


@then("no new ad platform order should have been created")
def then_no_new_ad_platform_order(ctx: dict) -> None:
    """Assert the action under test booked NO new ad platform order.

    "No NEW order" means: the adapter created no order beyond what already
    existed before the action under test. The expected create-count is read
    from an explicit baseline rather than sniffing the environment:

      baseline = ctx.get("adapter_calls_after_first_create")

    Two scenario families share this step text, distinguished only by whether
    that baseline is present:

    * Baseline ABSENT (default 0) -- fresh-failure scenarios (validation /
      account-not-found). The request fails before reaching the adapter, so
      EVERY adapter create method must show zero calls. We scan all the create
      methods (``create_order``, ``create_line_item``, ``create_media_buy``) on
      both the adapter mock and its ``return_value`` (the adapter instance),
      because the request never got far enough to call any of them.

    * Baseline PRESENT -- idempotency-replay scenarios. The "already created"
      Given step performed a REAL first create (which DID call the adapter) and
      recorded ``adapter_calls_after_first_create`` = the
      ``create_media_buy`` call_count immediately after that first create. The
      replay must serve the cached response WITHOUT a second booking, so the
      post-action ``create_media_buy`` call_count must not exceed the baseline.

    The baseline default of 0 is the correct expected count for the fresh case
    (nothing booked yet), so the same arithmetic check -- "current count <=
    baseline" -- serves both families without an env-sniffing branch.
    """
    env = ctx["env"]
    baseline = ctx.get("adapter_calls_after_first_create")

    if baseline is None:
        # Fresh-failure family: the adapter was never reached. Any call to ANY
        # create method on the adapter mock or its instance is a real booking.
        adapter_mock = env.mock.get("adapter")
        assert adapter_mock is not None, "No adapter mock in the harness env — cannot verify booking state"
        scan_targets = [adapter_mock]
        adapter_instance = getattr(adapter_mock, "return_value", None)
        if adapter_instance is not None:
            scan_targets.append(adapter_instance)
        for target, label in zip(scan_targets, ("adapter", "adapter()"), strict=False):
            for method_name in _ADAPTER_CREATE_METHODS:
                method = getattr(target, method_name, None)
                call_count = getattr(method, "call_count", 0) if method is not None else 0
                assert call_count == 0, (
                    f"Expected no new ad platform order but {label}.{method_name} was called "
                    f"{call_count} time(s) — the request booked an order despite failing/short-circuiting"
                )
        return

    # Idempotency-replay family: the first create already booked one order
    # (baseline). The replay must not book another.
    adapter_instance = env.mock["adapter"].return_value
    after = adapter_instance.create_media_buy.call_count
    assert after <= baseline, (
        f"Adapter create_media_buy was called {after} time(s) total, but only "
        f"{baseline} (the original) is allowed — the replay re-booked an ad platform order "
        "instead of serving the cached response"
    )


# ── Helpers for new steps ───────────────────────────────────────────


def _resolve_error_field(error: object, field: str) -> object | None:
    """Resolve a named field from an error, checking multiple sources."""
    # 1. Direct attribute on the error
    if hasattr(error, field):
        val = getattr(error, field)
        if val is not None:
            return val
    # 2. The error dict (to_dict() representation)
    d = _get_error_dict(error)
    if field in d and d[field] is not None:
        return d[field]
    # 3. The details sub-dict
    details = getattr(error, "details", None) or {}
    if field in details and details[field] is not None:
        return details[field]
    return None


def _available_error_fields(error: object) -> list[str]:
    """List available field names from all error sources for diagnostics."""
    fields: set[str] = set()
    d = _get_error_dict(error)
    fields.update(d.keys())
    details = getattr(error, "details", None) or {}
    fields.update(details.keys())
    for attr in ("error_code", "message", "recovery", "suggestion", "field"):
        if hasattr(error, attr):
            fields.add(attr)
    return sorted(fields)


def _get_error_details(error: object) -> dict:
    """Extract the details dict from an error object."""
    wire = _wire_of(error)
    if wire is not None:
        return dict(wire.get("details") or {})
    from src.core.exceptions import AdCPSalesAgentError

    if isinstance(error, AdCPSalesAgentError):
        return error.details or {}
    # adcp.types.Error model
    if hasattr(error, "details") and not isinstance(error, Exception):
        return error.details or {}
    # Fallback: try the error dict
    d = _get_error_dict(error)
    return d.get("details", {})


def _assert_detail_value_matches(key: str, actual: object, expected_str: str) -> None:
    """Assert a detail value matches, with numeric coercion."""
    # Try numeric comparison first
    try:
        expected_num = float(expected_str)
        actual_num = float(actual)  # type: ignore[arg-type]
        if expected_num == int(expected_num):
            # Integer comparison (e.g., "500" should match 500 and 500.0)
            assert actual_num == expected_num, f"Expected details['{key}'] = {expected_str}, got {actual}"
        else:
            assert abs(actual_num - expected_num) < 1e-9, f"Expected details['{key}'] = {expected_str}, got {actual}"
        return
    except (ValueError, TypeError):
        pass
    # Fall back to string comparison
    assert str(actual) == expected_str, f"Expected details['{key}'] = '{expected_str}', got '{actual}'"


def _assert_no_new_media_buy(ctx: dict) -> None:
    """Shared implementation: verify no new media buy was created.

    Two strategies:
    1. If a response exists with a media_buy_id, verify that ID does not
       exist in the database.
    2. If the harness tracks pre-operation media buy count, verify count
       is unchanged.
    3. Fallback: verify the operation errored (no response = no creation).
    """
    env = ctx["env"]
    resp = payload_or_none(ctx)

    # Strategy 1: if we got a response with media_buy_id, it should not be in DB
    if resp is not None:
        mb_id = getattr(resp, "media_buy_id", None)
        if mb_id is not None:
            mb = env.get_media_buy(mb_id) if hasattr(env, "get_media_buy") else None
            assert mb is None, f"Expected no media buy to be created but found {mb_id} in database"
            return

    # Strategy 2: operation should have errored (no response = nothing created)
    error = ctx.get("error")
    if error is not None:
        # Error means the operation failed before creating anything
        return

    # Strategy 3: check that response doesn't indicate creation
    if resp is not None and not hasattr(resp, "media_buy_id"):
        return

    raise AssertionError(
        "Cannot verify no media buy was created: no error recorded and "
        f"response has media_buy_id. ctx keys: {list(ctx.keys())}"
    )


@then(parsers.parse('the error field should contain "{field}"'))
def then_error_field_contains(ctx: dict, field: str) -> None:
    """Assert the wire error object's ``field`` pointer names ``field``.

    This step text has been written into scenarios since UC-004/UC-019/UC-021
    were generated, but no step definition ever existed -- so every scenario
    using it xfailed with "Step definition not found" while the TAUTOLOGICAL
    prose steps next to it were implemented and ran. That is exactly backwards:
    ``field`` is the only part of an error object that carries information the
    CODE does not already determine (``message`` and ``suggestion`` are pure
    functions of the code via CODE_TABLE), so it is the one part worth grading.

    Substring, not equality: the spec's pointer is a PATH
    (``packages[0].budget``), and a scenario legitimately grades the leaf it
    cares about without pinning the whole path.

    Wire-only, and deliberately loud: an error scenario that reached no wire
    envelope has not graded the buyer-facing contract, and silently passing on
    a reconstructed exception is the failure mode this epic removes.
    """
    result = ctx.get("result")
    assert result is not None, "No transport result recorded -- the When step did not dispatch"
    error = result.wire_error_object()
    assert error is not None, (
        f"No wire error envelope was captured, so there is no field pointer to grade. "
        f"Transport={ctx.get('transport')!r}."
    )
    actual = error.get("field")
    assert actual is not None, f"Wire error carries no 'field' pointer; error object was {error!r}"
    assert field in str(actual), f"Expected error field to contain {field!r}, got {actual!r}"


# ── Per-ENTRY errors ─────────────────────────────────────────────────
#
# A DIFFERENT CHANNEL from everything above, and the distinction is the point.
# Every other step in this file grades the ERROR ENVELOPE — adcp_error, the shape
# a refusal takes. These grade an error carried INSIDE a success: sync_accounts
# and sync_creatives answer per row, so one rejected row rides in a completed
# response at accounts[].errors / creatives[].errors while the others succeed.
# assert_wire_error structurally cannot serve those; there is no envelope.
#
# Consolidates two domain-local spellings that asserted the same thing on the same
# shape — uc011's 'the per-account errors array contains an error with code' and
# uc006's 'the per-creative errors[0].code should be'. uc011's was the stronger of
# the two (it pinned the entry count); uc006's took creatives[0] positionally, so
# the right code landing on the wrong row would have passed. Both now run the
# stronger rule.
#
# NOT extended to UC-004, which looks similar and is not: get-media-buy-delivery
# puts its failures in a ROOT errors[] and names the buy inside details, so there
# is no per-entry container to select. Collapsing the two shapes would mean one
# step switching on response topology, which is the DRY invariant's "genuinely
# different operations that happen to look similar" case. UC-004 keeps its own.


def _entry_error_codes(ctx: dict, collection: str) -> list[str]:
    """Codes on the SOLE *collection* entry's ``errors[]``, located on the wire.

    The count is PINNED to one. An index-0 default is only sound on a genuinely
    single-entry response, and pinning it means a scenario that grows a second row
    fails loudly instead of grading whichever row happens to be first -- which is
    the weakness this consolidation removed from uc006's positional read.
    """
    entries = wire_field(ctx, collection)
    assert len(entries) == 1, (
        f"the sole-entry read needs exactly one {collection} entry on the wire, got "
        f"{len(entries)}. Sole-entry is the point: grading row 0 of a multi-row response is "
        f"how the right code on the WRONG row passes. A multi-row scenario needs a selector "
        f"form, which does not exist because nothing needs one yet -- add it with the "
        f"scenario that does."
    )
    errors = wire_entry_errors(ctx, collection, index=0)
    assert errors, f"The {collection} entry carries an EMPTY errors[], so it reports no failure at all"
    return [error.get("code") if isinstance(error, dict) else getattr(error, "code", None) for error in errors]


@then(parsers.re(r'the (?P<collection>\w+) entry carries error code "(?P<code>[^"]+)"$'))
def then_sole_entry_error_code(ctx: dict, collection: str, code: str) -> None:
    """The response's SOLE *collection* entry carries *code*. One property only."""
    codes = _entry_error_codes(ctx, collection)
    assert code in codes, f"Expected error code {code!r} on the sole {collection} entry, got {codes}"


def _entry_index_for(ctx: dict, collection: str, entry_id: str) -> int:
    """Index of the *collection* entry whose id field is *entry_id*, located on the wire.

    The selector form the sole-entry docstring said to add with the scenario that
    needs it: a sync that carries an anchor creative AND a synthesized entry for an
    assignment reference has two rows, and grading "row 0" would grade the anchor.
    The id key is the collection's singular -- ``creatives`` -> ``creative_id``,
    ``accounts`` -> ``account_id`` -- which is how every per-item entry the pinned
    response schemas define names itself.
    """
    id_key = f"{collection[:-1]}_id"
    entries = wire_field(ctx, collection)
    matches = [i for i, entry in enumerate(entries) if entry.get(id_key) == entry_id]
    assert len(matches) == 1, (
        f"expected exactly one {collection} entry with {id_key}={entry_id!r} on the wire, found "
        f"{len(matches)} among {[e.get(id_key) for e in entries]}"
    )
    return matches[0]


@then(parsers.re(r'the (?P<collection>\w+) entry for "(?P<entry_id>[^"]+)" carries error code "(?P<code>[^"]+)"$'))
def then_selected_entry_error_code(ctx: dict, collection: str, entry_id: str, code: str) -> None:
    """The *collection* entry selected by its id carries *code* in its ``errors[]``."""
    index = _entry_index_for(ctx, collection, entry_id)
    errors = wire_entry_errors(ctx, collection, index=index)
    assert errors, f"the {collection} entry for {entry_id!r} carries an EMPTY errors[], so it reports no failure"
    codes = [error.get("code") if isinstance(error, dict) else getattr(error, "code", None) for error in errors]
    assert code in codes, f"Expected error code {code!r} on the {collection} entry for {entry_id!r}, got {codes}"


@then(parsers.re(r'the (?P<collection>\w+) entry for "(?P<entry_id>[^"]+)" has action "(?P<action>[^"]+)"$'))
def then_selected_entry_action(ctx: dict, collection: str, entry_id: str, action: str) -> None:
    """The *collection* entry selected by its id reports *action* on the wire."""
    index = _entry_index_for(ctx, collection, entry_id)
    actual = wire_field(ctx, collection)[index].get("action")
    assert actual == action, f"Expected action {action!r} on the {collection} entry for {entry_id!r}, got {actual!r}"


@then(
    parsers.re(
        r'the (?P<collection>\w+) entry for "(?P<entry_id>[^"]+)" carries a non-empty "(?P<key>\w+)" error detail$'
    )
)
def then_selected_entry_error_detail(ctx: dict, collection: str, entry_id: str, key: str) -> None:
    """One error on the selected entry carries a non-empty *key* under ``details`` (core/error.json).

    ``details`` is "Additional task-specific error details"; the pin's error-details/*.json
    files give each code its RECOMMENDED keys (creative-rejected: ``reasons``), so the grade
    is presence with content, not an exact shape.
    """
    index = _entry_index_for(ctx, collection, entry_id)
    errors = wire_entry_errors(ctx, collection, index=index)
    assert errors, f"the {collection} entry for {entry_id!r} carries an EMPTY errors[], so it reports no failure"
    details = [(e.get("details") if isinstance(e, dict) else getattr(e, "details", None)) or {} for e in errors]
    assert any(d.get(key) for d in details), (
        f"Expected a non-empty {key!r} under details on the {collection} entry for {entry_id!r}, got {details}"
    )


@then(parsers.re(r'the (?P<collection>\w+) entry for "(?P<entry_id>[^"]+)" omits the "(?P<field>\w+)" field$'))
def then_selected_entry_omits_field(ctx: dict, collection: str, entry_id: str, field: str) -> None:
    """The *collection* entry selected by its id has no *field* key on the wire at all.

    Key ABSENCE, not a null value: a schema that says a field MUST be omitted (the
    per-creative ``status`` on a failed or deleted action) is violated by ``"status": null``
    just as much as by a value, so the wire dict is read for the key itself.
    """
    index = _entry_index_for(ctx, collection, entry_id)
    entry = wire_field(ctx, collection)[index]
    assert field not in entry, (
        f"Expected the {collection} entry for {entry_id!r} to omit {field!r}, but it carries {entry.get(field)!r}"
    )
