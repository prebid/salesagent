"""Unified AdCP two-layer error envelope assertion.

Single helper for the wire shape every transport boundary must emit::

    {
        "adcp_error": {"code": "...", "message": "...", "recovery": "..."},
        "errors":     [{"code": "...", "message": "...", "recovery": "..."}],
        "context":    {...},   # optional
    }

Replaces the per-boundary helpers (``_assert_two_layer_envelope``,
``_assert_mcp_envelope``, ``_assert_a2a_envelope``, ``_assert_rest_envelope``)
that all verified the same shape with diverging signatures. A spec change to
the envelope now requires updating exactly one helper.

The helper catches TWO kinds of drift, not one:

- exception <-> wire: the two envelope layers must agree with each other and
  with the caller's expectation, so a typed exception whose recovery stops
  reaching the wire reddens.
- wire <-> spec: the ``recovery`` the caller pins must be the one the pinned
  ``error-code.json`` ``enumMetadata`` classifies that code as. Checking only
  the first left the helper blind to the second, and a *shipped, green* test
  graded ``SERVICE_UNAVAILABLE`` + ``terminal`` — a pair the normative pin
  contradicts (``SERVICE_UNAVAILABLE`` is ``transient``). Deriving the
  expectation from the pin makes that contradiction unwritable in any future
  test rather than merely absent from today's ones.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from tests.helpers import pinned_schema


def envelope_for(exc: Any) -> dict[str, Any]:
    """The wire body a buyer receives for one typed exception, built the way the boundary builds it.

    ``to_wire(AdcpErrorResponse.of(exc))`` -- the two production calls every transport makes on
    a failure, composed here so a test that holds an exception and no wire can still assert on
    the body. A test that HAS a wire asserts on it (``TransportResult.assert_wire_error``); this
    is for the ones grading the envelope's own derivation from a code.
    """
    from src.core.schemas._base import AdcpErrorResponse
    from src.core.tools._wire import to_wire

    return to_wire(AdcpErrorResponse.of(exc))


def locate_envelope_error(target: Any) -> dict[str, Any] | None:
    """The payload-layer error object (``errors[0]``) — the protocol position.

    THE one locator: every sanctioned read of an error region (the ``field`` /
    ``details`` kwargs below, and the harness readers on ``TransportResult``)
    resolves through it, so "where does the spec put this?" is answered in
    exactly one place. ``errors[0]`` rather than the envelope-level
    ``adcp_error`` mirror because error.json defines the per-error fields
    (``field``, ``details``, ``suggestion``) on the payload-layer object.

    Tolerant by design — returns ``None`` when there is no envelope at all —
    because the tolerant step-layer readers built on it must keep their
    ``| None`` contract. Strictness belongs to the callers that assert.
    """
    if target is None:
        return None
    body = target.envelope if hasattr(target, "envelope") else target
    if not isinstance(body, dict):
        return None
    errors = locate_envelope_errors(target)
    return errors[0] if errors else None


def locate_envelope_mirror(target: Any) -> dict[str, Any]:
    """The envelope-layer error object (``adcp_error``) — the mirror position.

    The sibling of :func:`locate_envelope_error`, and the reason it exists is that the
    two layers are not interchangeable: ``errors[0]`` is where error.json defines the
    per-error fields, and ``adcp_error`` is the envelope-level mirror the buyer reads
    first. A step whose obligation covers BOTH layers (an absence check — a disclosure
    that reaches only the mirror is still on the wire) therefore needs a locator for
    this one too, and before this existed the only way to reach it from a step was
    ``envelope.get("adcp_error")`` — a second answer to "where does the spec put this?",
    which is what tests/unit/test_architecture_bdd_wire_discipline.py forbids.

    Returns ``{}`` rather than ``None`` for an absent or non-dict mirror, because every
    caller so far asks a containment question (``key not in mirror``) and an empty dict
    answers that correctly without each of them writing its own ``or {}``.

    ``assert_envelope_shape`` below still indexes ``body["adcp_error"]`` directly. That
    is not an oversight and not a second locator: it asserts the mirror is PRESENT
    (``assert "adcp_error" in body``) before reading required fields off it, so it needs
    the KeyError, where this returns a default. Routing it through here would be a
    worthwhile follow-up only if the presence assertion moved with it.
    """
    if target is None:
        return {}
    body = target.envelope if hasattr(target, "envelope") else target
    if not isinstance(body, dict):
        return {}
    mirror = body.get("adcp_error")
    return mirror if isinstance(mirror, dict) else {}


def locate_envelope_errors(target: Any) -> list[dict[str, Any]]:
    """Every payload-layer error object, in wire order.

    The plural of :func:`locate_envelope_error`, for the steps whose obligation is about
    ALL entries ("each error should include a suggestion") rather than the first. Sharing
    the locator is the point: a step that walked ``envelope["errors"]`` itself would be a
    second answer to "where does the spec put this?".

    Tolerant on the same terms as the singular: an absent envelope is an empty list.
    """
    if target is None:
        return []
    body = target.envelope if hasattr(target, "envelope") else target
    if not isinstance(body, dict):
        return []
    return [entry for entry in body.get("errors") or [] if isinstance(entry, dict)]


def assert_no_raw_validation_leak(message: str) -> None:
    """Assert a buyer-facing validation message omits raw Pydantic internals."""
    assert "input_value" not in message, f"raw Pydantic input leaked into validation message: {message!r}"
    assert "errors.pydantic.dev" not in message, f"Pydantic documentation URL leaked into message: {message!r}"


def _assert_marker_absent(rendered_region: Any, marker: str, *, region_label: str) -> None:
    """Core of the wire-safety marker scan: ``marker`` must not appear in a region.

    Shared by the two carriers the spec treats as equal — the two-layer ERROR
    envelope and a SUCCESS payload's ``errors[]`` (error-compliance.yaml's
    ``validate_error_shape`` grades a typed error code "via either adcp_error
    (envelope) or errors[] (payload)"). One scan body, two entry points, so a
    third carrier never becomes a third copy.
    """
    rendered = str(rendered_region)
    assert marker not in rendered, f"marker {marker!r} leaked into {region_label}: {rendered!r}"


def assert_no_marker_in_envelope(envelope: Mapping[str, Any] | None, marker: str) -> None:
    """Assert ``marker`` is absent from the FULL wire error envelope.

    Sibling to :func:`assert_no_raw_validation_leak`, for the wire-safety
    obligation: unlike a positive match scoped to ``errors[0].message``,
    this scans
    ``str(envelope)`` so a leak buried anywhere in the envelope
    (``adcp_error.message``, ``errors[0].details``, ``suggestion``, ``context``)
    fails the check — mirroring the exemplar
    ``tests/integration/test_prkv8_untyped_exception_wire_leak.py::_assert_no_leak``,
    which scans the WHOLE envelope rather than a single field.
    """
    assert envelope is not None, f"no wire envelope captured to check for marker {marker!r}"
    _assert_marker_absent(envelope, marker, region_label="wire error envelope")


def assert_no_marker_in_payload_errors(wire_response: Mapping[str, Any] | None, marker: str) -> None:
    """Assert ``marker`` is absent from a SUCCESS payload's ``errors[]``.

    The success-path counterpart to :func:`assert_no_marker_in_envelope`. A tool
    that degrades gracefully (``list_creative_formats`` with one unreachable
    agent) returns HTTP success with per-agent failures reported in
    ``errors[]`` — so ``result.is_error`` is False and ``wire_error_envelope``
    is ``None``, which makes the envelope helpers either fail on their
    ``not None`` guard or pass vacuously. That carrier is still buyer-facing:
    AdCP 3.1.1 ``transport-errors.mdx`` § Security Considerations opens with
    "Every field is client-facing", and the storyboard gives the two carriers
    equal status.

    Asserts ``errors[]`` is present and NON-EMPTY first, so the scan cannot
    pass vacuously against a response that carried no errors at all.
    """
    assert wire_response is not None, f"no wire response captured to check for marker {marker!r}"
    errors = wire_response.get("errors")
    assert errors, f"expected a non-empty payload-level errors[] to scan for {marker!r}, got {wire_response!r}"
    _assert_marker_absent(errors, marker, region_label="payload errors[]")


def _subset_mismatch(expected: Mapping[str, Any], actual: Mapping[str, Any], *, position: str) -> str | None:
    """``None`` when every key in ``expected`` is in ``actual`` with an equal value.

    Otherwise the first mismatch, already phrased for an assertion message. Shared
    by the ``details`` and ``issues`` kwargs below because they are the SAME check:
    core/error.json declares both objects ``additionalProperties: true``, so both
    are subsets rather than equality, and they differ only in whether the object is
    the block itself or one entry of an array.
    """
    for key, value in expected.items():
        if key not in actual:
            return f"{position} is missing {key!r}"
        if actual[key] != value:
            return f"{position}[{key!r}]={actual[key]!r}, expected {value!r}"
    return None


def assert_envelope_shape(
    target: Any,
    code: str,
    *,
    recovery: str,
    message_substr: str | None = None,
    field: str | None = None,
    details: Mapping[str, Any] | None = None,
    issues: Sequence[Mapping[str, Any]] | None = None,
    retry_after: int | None = None,
    check_mcp_tool_error: bool = False,
) -> None:
    """Assert the AdCP spec two-layer error envelope shape.

    Args:
        target: The envelope under test. Accepts either:
                - a ``dict`` (REST JSON body, A2A ``error.data``, raw envelope),
                - an ``AdCPToolError`` (MCP boundary) — its ``.envelope`` attr
                  is read transparently.
        code: Expected wire error code; must match BOTH ``adcp_error.code``
                and ``errors[0].code``. Two-layer invariant: both layers
                always agree.
        recovery: Required. Both ``adcp_error.recovery`` and
                ``errors[0].recovery`` must equal this hint. Pinning recovery
                is mandatory: it is the buyer-facing retry semantics
                (``correctable`` / ``transient`` / ``terminal``) and a silent
                drift between a typed exception's recovery and the wire is
                exactly the regression this helper exists to catch. It must
                ALSO agree with the pinned ``enumMetadata`` classification of
                *code* whenever the pin defines one: the caller's literal pins
                intent, but an intent the spec contradicts is not gradeable.
                Codes the pin does not classify (e.g. ``NOT_SUPPORTED``) keep
                the caller's literal as the only expectation.
        message_substr: If provided, must appear in ``errors[0].message``.
                ``adcp_error.message`` is allowed to differ (it carries the
                envelope-level summary). A message substring is the WEAKEST
                oracle this helper offers — prefer ``field`` / ``details`` /
                ``issues``, which grade structured protocol positions rather
                than authored prose.
        field: If provided, both ``adcp_error.field`` and ``errors[0].field``
                must equal this JSONPath-lite path into the buyer's request
                payload (``core/error.json`` @3.1.1 — e.g.
                ``property_list.agent_url``) — the error.json ``field`` pointer
                naming WHICH request field was rejected. Checked on BOTH layers
                for the same reason ``recovery`` is: the pinned storyboards read
                both in the wild — ``proposal_finalize.yaml:207/352/397`` grade
                ``adcp_error.field`` while the other scenarios grade
                ``errors[0].field`` — and ``error-handling.mdx:88`` calls
                populating only one layer "the source-of-truth for most interop
                bugs". Asserted at the protocol top level of each layer only: a
                copy buried in the free-form ``details`` dict is not at the
                protocol position and does not satisfy the contract (same burial
                rule as ``extract_wire_suggestion``). ``None`` (the default) does
                not assert absence: ``field`` is optional in the schema, so most
                envelopes legally carry none. A call site that needs "no ``field``
                key at all" asserts that itself. This lives here, on the one
                envelope primitive, rather than as a second free-function error
                surface — a parallel error-assertion mechanism is exactly what
                step definitions must not have to choose between.
        details: If provided, each key must be present in ``errors[0].details``
                with an equal value — a SUBSET check, never dict equality:
                ``details`` is an OPEN object in core/error.json (the error
                object declares ``additionalProperties: true``), so production
                may carry diagnostic keys no oracle names. Asserted at the
                protocol position ``errors[0].details`` only — a block that
                lives on the envelope-level mirror alone, or a value nested one
                level deeper, does not satisfy it (the same burial rule as
                ``field``). Lives here for the same reason ``field`` does: one
                sanctioned error surface, so a step never has to choose a
                mechanism. Non-binary oracles (non-empty, regex-per-entry,
                membership) use ``TransportResult.wire_error_details`` instead,
                which asserts the code before handing the block over.
        issues: If provided, the LIST analogue of ``details``. ``errors[0].issues``
                must be a non-empty list, and each expected item must be carried
                by at least ONE wire item that has every key in it with an equal
                value. Per item and order-independent: two expected keys
                satisfied by two DIFFERENT wire items do not satisfy one expected
                item, because ``issues[]`` is the pin's map of WHICH field failed
                for WHICH reason and splitting a pair across entries loses that
                pairing. A matched wire item may carry keys the oracle does not
                name — the pinned item is ``additionalProperties: true``, the same
                openness rule ``details`` follows. Asserted at the protocol
                position ``errors[0].issues`` only, so a block on the
                envelope-level mirror alone does not satisfy it (the same burial
                rule as ``field`` and ``details``). Non-binary oracles
                (find-by-pointer, regex-per-entry) use
                ``TransportResult.wire_error_issues`` instead.
        retry_after: If provided, both ``adcp_error.retry_after`` and
                ``errors[0].retry_after`` must equal it. Mirrors ``recovery`` in
                being checked on BOTH layers — it is the transient-retry budget
                the buyer schedules against, so a value that reaches one layer
                only is a real interop bug.
        check_mcp_tool_error: If ``True``, additionally assert that ``target``
                is an ``AdCPToolError`` instance before reading its envelope.
                MCP-boundary call sites use this to pin the exception type as
                well as the wire shape — a plain ``ToolError`` would still
                expose ``.envelope`` via duck-typing but would not be the
                typed MCP-boundary exception the test claims to inspect.
    """
    if check_mcp_tool_error:
        from src.core.tool_error_logging import AdCPToolError

        assert isinstance(target, AdCPToolError), f"expected AdCPToolError, got {type(target).__name__}"

    body = target.envelope if hasattr(target, "envelope") else target

    assert isinstance(body, dict), f"envelope target must resolve to dict, got {type(body).__name__}"
    assert "adcp_error" in body, f"missing envelope-level adcp_error: {body}"
    assert "errors" in body, f"missing payload-level errors[]: {body}"
    assert body["errors"], "errors[] must contain at least one entry"

    # Through the locator, not a second body["errors"][0] of its own: this function is
    # where "the payload-layer error object lives at errors[0]" is decided, so reading
    # it any other way here would be the primitive contradicting itself.
    error = locate_envelope_error(body)
    assert error is not None, f"errors[0] is not an error object: {body}"

    assert body["adcp_error"]["code"] == code, f"adcp_error.code={body['adcp_error']['code']!r}, expected {code!r}"
    assert error["code"] == code, f"errors[0].code={error['code']!r}, expected {code!r}"

    assert body["adcp_error"].get("recovery") == recovery, (
        f"adcp_error.recovery={body['adcp_error'].get('recovery')!r}, expected {recovery!r}"
    )
    assert error.get("recovery") == recovery, f"errors[0].recovery={error.get('recovery')!r}, expected {recovery!r}"

    pinned_recovery = pinned_schema.recovery_by_code().get(code)
    assert pinned_recovery is None or pinned_recovery == recovery, (
        f"this call grades ({code!r}, {recovery!r}), but the pinned error-code.json "
        f"enumMetadata says {code!r} is {pinned_recovery!r} — a test may not grade a pair "
        f"the spec contradicts. The enumMetadata recovery is normative, so either the raise "
        f"site is wrong (pick the exception class whose pinned recovery IS the intent) or "
        f"the pin moved (advance it); do not relax this helper."
    )

    if field is not None:
        # Payload layer first: it is the protocol position error.json defines the
        # per-error fields on, and the envelope-level mirror is graded right after
        # so a `field` that reaches only one layer still reddens.
        actual_field = error.get("field")
        assert actual_field == field, f"errors[0].field={actual_field!r}, expected {field!r}"
        assert body["adcp_error"].get("field") == field, (
            f"adcp_error.field={body['adcp_error'].get('field')!r}, expected {field!r}"
        )

    if retry_after is not None:
        # Mirrors `recovery` in being checked on BOTH layers. Until this existed
        # there was no way to grade retry_after through the sanctioned surface, so
        # the one test that needs it hand-indexed the envelope
        # (`wire_error_envelope["adcp_error"].get("retry_after")`) -- an off-path
        # read that existed because the helper could not express the fact, not
        # because the author reached past it.
        assert body["adcp_error"].get("retry_after") == retry_after, (
            f"adcp_error.retry_after={body['adcp_error'].get('retry_after')!r}, expected {retry_after!r}"
        )
        assert error.get("retry_after") == retry_after, (
            f"errors[0].retry_after={error.get('retry_after')!r}, expected {retry_after!r}"
        )

    if details is not None:
        actual_details = error.get("details")
        assert isinstance(actual_details, dict), (
            f"errors[0].details={actual_details!r} is not an object at the protocol position; "
            f"expected it to carry {dict(details)!r}"
        )
        mismatch = _subset_mismatch(details, actual_details, position="errors[0].details")
        assert mismatch is None, f"{mismatch}: {actual_details!r}"

    if issues is not None:
        actual_issues = error.get("issues")
        assert isinstance(actual_issues, list) and actual_issues, (
            f"errors[0].issues={actual_issues!r} is not a non-empty array at the protocol position; "
            f"expected it to carry {[dict(item) for item in issues]!r}"
        )
        for expected_issue in issues:
            # ONE entry must carry the whole expected item. Checking each key against
            # the array as a whole would let a pointer from one rejection pair with a
            # keyword from another, which is exactly the field-to-reason mapping
            # `issues[]` exists to carry and `field` (singular) could not.
            carried = any(
                isinstance(item, dict) and _subset_mismatch(expected_issue, item, position="issue") is None
                for item in actual_issues
            )
            assert carried, (
                f"no entry in errors[0].issues carries {dict(expected_issue)!r} in full; issues were {actual_issues!r}"
            )

    if message_substr is not None:
        actual = error.get("message", "")
        assert message_substr in actual, f"errors[0].message={actual!r} does not contain {message_substr!r}"


@contextmanager
def raises_adcp(expected: type[Exception] | str) -> Iterator[Any]:
    """Assert the block raises ``AdcpFailure`` whose RESPONSE carries *expected*'s code.

    The boundary answers every failure with a response and raises ``AdcpFailure`` carrying it,
    so a caller no longer receives the typed exception the raise site built. What it receives
    is the buyer-facing CODE, which is the obligation worth grading -- ``tests/CLAUDE.md``
    § "Error verification policy" says exactly that: assert on the wire, not on a
    reconstructed exception.

    *expected* may be the typed exception CLASS, read for its declared code, so a test keeps
    naming the refusal it means rather than a string literal that can drift from it.

    Yields pytest's ``ExceptionInfo`` so a caller needing the failure itself can bind it with
    ``as``.
    """
    import pytest

    from src.core.exceptions import AdcpFailure

    code = expected if isinstance(expected, str) else expected().error_code
    with pytest.raises(AdcpFailure) as excinfo:
        yield excinfo
    response = excinfo.value.response
    assert response.adcp_error is not None, f"AdcpFailure carried a response with no adcp_error: {response!r}"
    assert response.adcp_error.code == code, f"adcp_error.code={response.adcp_error.code!r}, expected {code!r}"
