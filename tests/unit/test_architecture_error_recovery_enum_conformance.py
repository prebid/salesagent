"""Oracle: every ``AdCPError`` subclass's ``_default_recovery`` matches the pinned
``error-code.json`` ``enumMetadata`` recovery classification (#1417).

The ``enumMetadata`` block is normative — its ``$comment`` states: "SDKs MUST
consume this block ... the recovery classification embedded in that prose is
normative and MUST match the value here." This oracle locks in the auth-family
recovery fix (#1417: ``AUTH_REQUIRED`` is ``correctable``) and prevents
any exception class from drifting away from the spec's buyer-facing retry
semantics. Per-class tests that assert a hardcoded literal cannot catch a
divergence between the class and the spec — this parametrized oracle does.

Codes absent from the pinned enum (internal/adapter-only codes that have no AdCP
wire equivalent — e.g. ``WORKFLOW_CREATION_FAILED``, ``GAM_UPDATE_FAILED``) cannot
be graded against an enum that does not contain them; they are reported by
``test_internal_only_codes_are_documented`` rather than silently skipped.

Reads the ``recovery`` field through ``tests.helpers.pinned_schema`` (the
installed SDK's own error-code.json), NOT the vendored fixture the sibling
``suggestion``-conformance oracle still uses. Verified before this migration:
the SDK's 92-code enum is a strict superset of the fixture's 64 (fixture-only
set is empty), and the ``recovery`` classification is IDENTICAL across all 64
shared codes (0 divergences; 30 AdCPError subclasses graded, unchanged before
and after) — the ``suggestion`` field is NOT identical across those same
codes (4 textual divergences), which is why only this reader migrated.
Reproduce the fixture count: ``uv run python3 -c "import json;
print(len(json.load(open('tests/fixtures/adcp_schemas_pinned/enums/error-code.json'))['enum']))"``
-> 64 (see docs/adcp-spec-version.md "Pinned schema sources" for the full measurement).

The final sections extend the same oracle beyond the exception classes, to every
other surface that answers a recovery question:

- ``src/``'s own wire-code tables: ``src.core.exceptions`` must expose
  ``RECOVERY_BY_WIRE_CODE``, machine-read from the same normative
  ``enumMetadata`` block, and no other table in that module may answer a
  recovery question.
- the test-side grader itself: ``tests.helpers.assert_envelope_shape`` must
  refuse a (code, recovery) pair the pin contradicts, so a test cannot grade
  what the spec forbids.
- the raise site: ``recovery`` must not be nameable at all — no ``recovery=``
  kwarg on ``__init__`` or ``synthesize``, no assignment on an instance — and
  the value the envelope builder reads off an instance must be derived from
  that instance's WIRE code at access time.

Every expectation in this file is read from the pin through
``tests.helpers.pinned_schema.recovery_by_code()`` — the ONE test-side reader of
that block — and never from ``src``'s table. That independence from src/ is the
load-bearing property: the oracle grades src's loader rather than agreeing with
it. (The map used to be loaded a second time here; delegating to the shared
test-side accessor removes the duplicate load without touching the
independence.)
"""

from __future__ import annotations

import pytest
from adcp.server.helpers import STANDARD_ERROR_CODES

from src.core.exceptions import (
    _SPEC_DEMOTED_CODES,
    ERROR_CODE_MAPPING,
    WIRE_STANDARD_CODES,
    AdCPAdapterError,
    AdCPError,
    AdCPValidationError,
    translate_error_code,
)
from tests.helpers import pinned_schema


def _pinned_recovery_by_code() -> dict[str, str]:
    """Return ``{error_code: recovery}`` from the pinned enumMetadata block.

    Delegates to the shared test-side accessor rather than loading the block a
    second time: ``assert_envelope_shape`` now grades against the same map, and
    two loads of one normative block is the duplication this project treats as
    a defect. Independence from ``src`` — the property this oracle actually
    rests on — is unchanged: the accessor lives in ``tests/`` and reads the
    installed SDK's pinned tree, never ``src.core.exceptions``.
    """
    return pinned_schema.recovery_by_code()


_RECOVERY_BY_CODE = _pinned_recovery_by_code()


def _adcp_error_subclasses() -> list[type[AdCPError]]:
    # Walk the concrete-subclass tree (the production single source of truth used by
    # tool_error_logging._build_error_code_to_status), not just the exceptions module
    # namespace — future-proofs the oracle against a subclass defined outside
    # exceptions.py that inspect.getmembers would silently miss. (#1417)
    return list(AdCPError.iter_concrete_subclasses())


_GRADED_CLASSES = sorted(
    (c for c in _adcp_error_subclasses() if c._default_error_code in _RECOVERY_BY_CODE),
    key=lambda c: c.__name__,
)


def test_pinned_enum_metadata_loaded() -> None:
    """Meta-guard: the pinned enum loaded and graded a representative set, so the
    parametrized oracle below can never silently degrade to zero cases."""
    assert len(_RECOVERY_BY_CODE) >= 50, (
        f"Expected the pinned enumMetadata to define recovery for many codes, got {len(_RECOVERY_BY_CODE)}"
    )
    assert len(_GRADED_CLASSES) >= 25, f"Expected to grade many AdCPError subclasses, got {len(_GRADED_CLASSES)}"


@pytest.mark.parametrize("cls", _GRADED_CLASSES, ids=lambda c: c.__name__)
def test_default_recovery_matches_pinned_enum(cls: type[AdCPError]) -> None:
    """Each subclass's INSTANCE recovery must equal the pinned enum's normative value.

    Reads ``_instance_of(cls).recovery``, not ``cls._default_recovery``. Since
    recovery became a read-only derived property, the class-level declaration is
    no longer the answer — it is only the fallback for a wire code the pin does
    not define, and the per-subclass declarations were deleted precisely because
    they were a re-divergence channel. What must conform is what an instance
    actually reports, which is what the envelope builder reads.
    """
    code = cls._default_error_code
    expected = _RECOVERY_BY_CODE[code]
    actual = _instance_of(cls).recovery
    assert actual == expected, (
        f"{cls.__name__} (code {code!r}) reports recovery={actual!r} "
        f"but the pinned error-code.json enumMetadata says {expected!r}. The enumMetadata "
        f"recovery is normative (xc2j): fix the derivation, or advance the pin if the spec changed."
    )


# Classes whose _default_error_code is rewritten by ERROR_CODE_MAPPING before it
# reaches the wire. Base AdCPError is included explicitly: iter_concrete_subclasses
# yields descendants only, yet the base class is live on the wire via the
# normalize_to_adcp_error crash-wrap path at every transport boundary.
_REMAPPED_CLASSES = sorted(
    (c for c in [AdCPError, *AdCPError.iter_concrete_subclasses()] if c._default_error_code in ERROR_CODE_MAPPING),
    key=lambda c: c.__name__,
)


def test_remapped_classes_enumerated() -> None:
    """Meta-guard: every ERROR_CODE_MAPPING target is present in the pinned
    enumMetadata, so fixture drift can never silently un-grade the wire-recovery
    oracle below (a target missing from _RECOVERY_BY_CODE would KeyError, but
    only for classes that carry it — this pins the full target set)."""
    missing = sorted(set(ERROR_CODE_MAPPING.values()) - set(_RECOVERY_BY_CODE))
    assert not missing, (
        f"ERROR_CODE_MAPPING target(s) {missing} are absent from the pinned "
        f"error-code.json enumMetadata, so the wire-recovery oracle cannot grade "
        f"them. Advance the pinned fixture (the enum defines these codes upstream) "
        f"or fix the mapping."
    )
    assert len(_REMAPPED_CLASSES) >= 10, f"Expected to grade many remapped classes, got {len(_REMAPPED_CLASSES)}"


@pytest.mark.parametrize("cls", _REMAPPED_CLASSES, ids=lambda c: c.__name__)
def test_wire_recovery_matches_pinned_enum(cls: type[AdCPError]) -> None:
    """For every class whose default code is remapped by ERROR_CODE_MAPPING, the
    class's INSTANCE recovery must equal the pinned enum's normative recovery
    for the code actually emitted on the wire (post-translation). The envelope
    builder emits ``exc.wire_error_code`` alongside ``exc.recovery`` — recovery
    follows the wire code, never the internal class taxonomy."""
    wire_code = translate_error_code(cls._default_error_code)
    expected = _RECOVERY_BY_CODE[wire_code]
    actual = _instance_of(cls).recovery
    assert actual == expected, (
        f"{cls.__name__} (code {cls._default_error_code!r} -> wire {wire_code!r}) "
        f"reports recovery={actual!r} but the pinned error-code.json "
        f"enumMetadata says the wire code {wire_code!r} is {expected!r}. The envelope "
        f"emits the wire code with the class recovery, so this pair is spec-nonconformant "
        f"(nr2q): fix the class recovery or the mapping, or advance the pin if the spec changed."
    )


def test_internal_only_codes_are_documented() -> None:
    """Codes carried by exception classes but absent from the pinned enum are
    internal/adapter-only (no AdCP wire equivalent). Pin the known set so a NEW
    exception class with a non-spec code is surfaced for review rather than
    silently escaping the recovery oracle."""
    internal_codes = {
        c._default_error_code for c in _adcp_error_subclasses() if c._default_error_code not in _RECOVERY_BY_CODE
    }
    known_internal = {
        "NOT_FOUND",
        "TASK_NOT_FOUND",
        "FORMAT_NOT_FOUND",
        "WORKFLOW_CREATION_FAILED",
        "LINE_ITEM_CREATION_FAILED",
        "PARTIAL_FAILURE",
        "ACTIVATION_WORKFLOW_FAILED",
        "GAM_UPDATE_FAILED",
        "MEDIA_BUY_REJECTED",
        "INVENTORY_UNAVAILABLE",
    }
    unexpected = internal_codes - known_internal
    assert not unexpected, (
        f"New non-spec error code(s) {sorted(unexpected)} are not in the pinned enum and so "
        f"escape the recovery oracle. Either add the code to the AdCP error-code enum (and the "
        f"pin) or, if it is genuinely internal-only, add it to known_internal here."
    )


# ---------------------------------------------------------------------------
# src-side mirror: the production recovery table IS the pin, machine-read
# ---------------------------------------------------------------------------
# The normative enumMetadata block is currently machine-read only by this test
# helper, so nothing in src/ can consult it and every production recovery value
# is hand-typed. These three tests grade the src-side table that fixes that:
# it must equal the pin exactly, it must classify every code src can put on the
# wire, and it must be the ONLY table in exceptions.py that answers a recovery
# question (WIRE_STANDARD_CODES answers membership only).

# Spec codes the SDK helper table has not caught up to; the pinned enum
# defines them (CREATIVE_NOT_FOUND correctable, CONFIGURATION_ERROR terminal,
# REFERENCE_NOT_FOUND correctable), so they are wire codes src can emit and
# must therefore be classified.
_SUPPLEMENT_WIRE_CODES = frozenset({"CREATIVE_NOT_FOUND", "CONFIGURATION_ERROR", "REFERENCE_NOT_FOUND"})


def _src_recovery_by_wire_code() -> dict[str, str]:
    """The production recovery table, imported inside the test body.

    Deliberately NOT a module-level import. While ``RECOVERY_BY_WIRE_CODE`` does
    not exist in src/, a module-level import would make this whole file a
    collection ERROR and silently un-grade the four oracles above; function-local,
    its absence is a FAILED result on the three obligations below and nothing else.
    """
    from src.core.exceptions import RECOVERY_BY_WIRE_CODE

    return RECOVERY_BY_WIRE_CODE


def test_src_recovery_table_mirrors_pinned_enum() -> None:
    """``src.core.exceptions.RECOVERY_BY_WIRE_CODE`` equals the pinned enumMetadata
    recovery block, code for code.

    Exact dict equality, not a size check: a loader that reads the wrong spec
    directory, drops the codes whose metadata shape differs, or falls back to the
    SDK helper's ``STANDARD_ERROR_CODES`` (which contradicts the pin on 7 of its
    38 codes) all produce a table of plausible size. This test loads the pin
    independently via ``tests.helpers.pinned_schema`` and never reads src's table
    as its own expectation, so it grades src's loader rather than agreeing with it.
    """
    pinned = _pinned_recovery_by_code()
    src_table = _src_recovery_by_wire_code()

    divergent = {
        code: {"src": src_table.get(code), "pin": pinned.get(code)}
        for code in sorted(set(pinned) | set(src_table))
        if src_table.get(code) != pinned.get(code)
    }
    assert src_table == pinned, (
        f"src.core.exceptions.RECOVERY_BY_WIRE_CODE diverges from the pinned "
        f"error-code.json enumMetadata on {len(divergent)} code(s): {divergent}. "
        f"The enumMetadata block is normative ($comment: 'SDKs MUST consume this "
        f"block instead of parsing Recovery: X from enumDescriptions prose') — load "
        f"it at import time; do not hand-type values and do not source them from "
        f"the SDK helper's STANDARD_ERROR_CODES."
    )


def test_src_recovery_table_covers_every_code_that_reaches_the_wire() -> None:
    """Every wire code src can emit has a classification in the src-side table.

    The codes that reach a buyer are the ERROR_CODE_MAPPING translation targets
    plus the pinned-spec supplement. A table that omits one of them makes the
    recovery answer a KeyError at exactly the moment a caller needs it.
    """
    src_table = _src_recovery_by_wire_code()

    emittable = set(ERROR_CODE_MAPPING.values()) | set(_SUPPLEMENT_WIRE_CODES)
    unclassified = sorted(emittable - set(src_table))
    assert not unclassified, (
        f"Wire code(s) {unclassified} can reach a buyer (ERROR_CODE_MAPPING target "
        f"or pinned-spec supplement) but carry no recovery classification in "
        f"src.core.exceptions.RECOVERY_BY_WIRE_CODE."
    )


def test_wire_standard_codes_carry_no_classification() -> None:
    """``WIRE_STANDARD_CODES`` answers membership only — one provenance rule.

    Every entry's value must be empty. The table is built from the SDK helper's
    ``STANDARD_ERROR_CODES``, whose recovery values contradict the pin on 7 codes
    (UNSUPPORTED_FEATURE, AUTHORIZATION_REQUIRED, IDEMPOTENCY_CONFLICT,
    IDEMPOTENCY_EXPIRED, BUDGET_EXHAUSTED, CONFLICT, ACCOUNT_PAYMENT_REQUIRED);
    keeping those values live in the module makes a future value read silently
    pin-contradicting for 7 codes instead of a loud KeyError for all of them.
    RECOVERY_BY_WIRE_CODE is the only classification either TABLE in exceptions.py
    carries. Two hand-typed surfaces remain outside the tables: the per-class
    ``_default_recovery`` literals, which the oracles above DO grade against this
    pin, and the ``recovery=`` constructor kwarg, which nothing grades — a call site
    can still pair a code with a recovery the pin contradicts.
    """
    classified = {code: entry for code, entry in WIRE_STANDARD_CODES.items() if entry != {}}
    assert not classified, (
        f"{len(classified)} WIRE_STANDARD_CODES entries still carry values "
        f"(e.g. {dict(sorted(classified.items())[:3])}). The table answers membership "
        f"only; recovery comes from RECOVERY_BY_WIRE_CODE, machine-read from the pin."
    )

    assert set(WIRE_STANDARD_CODES) == (set(STANDARD_ERROR_CODES) | set(_SUPPLEMENT_WIRE_CODES)) - set(
        _SPEC_DEMOTED_CODES
    ), (
        "WIRE_STANDARD_CODES must stay the SDK helper's code-NAME baseline plus the "
        "pinned-spec supplement names, MINUS the demoted set — emptying the values "
        "must not change which codes are members. A helper code the pin does not "
        "define belongs in _SPEC_DEMOTED_CODES with a reason, not in the wire set."
    )

    # The invariant #1417 exists to make definitional:
    # every code that can reach the wire has a pinned recovery classification, so
    # no lookup falls back to an authored default (invariant I6, "recovery is
    # derived, never authored"). src enforces this at import with a raise; this
    # asserts the same property from the test side against _pinned_recovery_by_code(),
    # which reads the installed SDK's pinned tree and never imports
    # src.core.exceptions — so src and this oracle cannot agree by sharing a bug.
    pinned = _pinned_recovery_by_code()
    assert set(WIRE_STANDARD_CODES) <= set(pinned), (
        f"Wire code(s) with no pinned recovery classification: "
        f"{sorted(set(WIRE_STANDARD_CODES) - set(pinned))}. "
        f"The recovery table must be TOTAL over the wire set."
    )

    assert len(WIRE_STANDARD_CODES) == 40, (
        f"WIRE_STANDARD_CODES has {len(WIRE_STANDARD_CODES)} entries, not 40 (38 SDK "
        f"+ 3 supplement - 1 demoted). If the SDK pin moves, the demoted-set comment "
        f"in src/core/exceptions.py is where the reason lives; update it there."
    )


def test_wire_advisory_derives_the_pair_and_contains_internal_codes() -> None:
    """``wire_advisory`` is the one advisory constructor, and it DERIVES recovery.

    The ``errors[]`` advisory channel is the one place a (code, recovery) pair
    reaches a buyer without passing the boundary translator — it rides inside a
    SUCCESS response and serializes verbatim. ``adcp.types.Error`` types ``code``
    as a bare ``str`` and leaves ``recovery`` free, so before this constructor a
    call site could pair any code with any recovery and no oracle would see it.

    Two obligations, graded together because the constructor exists to hold both:

    1. The recovery is the PIN's, for every code the pin defines — not a caller's
       literal, and not the SDK helper's (which contradicts the pin on 7 codes).
    2. An internal-only code cannot leak. ``ADAPTER_ERROR`` is not a wire code;
       it must arrive as its mapped ``SERVICE_UNAVAILABLE`` carrying
       SERVICE_UNAVAILABLE's classification, never as itself and never carrying
       the classification of the code it was translated FROM.
    """
    from src.core.exceptions import wire_advisory

    for code, expected in sorted(_pinned_recovery_by_code().items()):
        if code not in WIRE_STANDARD_CODES:
            continue  # not a code an advisory may carry; the mapping cases are below
        advisory = wire_advisory(code, "x")
        assert (advisory.code, advisory.recovery) == (code, expected), (
            f"wire_advisory({code!r}) produced {(advisory.code, advisory.recovery)!r}; the pinned "
            f"enumMetadata says {expected!r}. Recovery must be derived from the code, never chosen."
        )

    internal = wire_advisory("ADAPTER_ERROR", "x")
    assert (internal.code, internal.recovery) == ("SERVICE_UNAVAILABLE", "transient"), (
        f"wire_advisory('ADAPTER_ERROR') produced {(internal.code, internal.recovery)!r}. An internal-only "
        "code must be translated to its wire equivalent AND take that equivalent's pinned recovery."
    )


# ---------------------------------------------------------------------------
# test-side mirror: the grader itself cannot grade a pin-contradicting pair
# ---------------------------------------------------------------------------
# ``assert_envelope_shape`` took ``recovery`` as a free caller literal and checked
# only that the two envelope layers agreed WITH EACH OTHER, so it was blind to
# wire<->spec drift: a shipped, green test asserted SERVICE_UNAVAILABLE+terminal,
# a pair the normative enumMetadata contradicts. The helper now derives the
# expected recovery from the same pin these oracles read. These tests are what
# stops that derivation from being silently removed the next time it reddens
# something — the failure mode being re-armed is "the grader agrees with the bug".
#
# Envelopes are built by the production builder from a real exception rather than
# hand-written here, so the shapes graded are the shapes the boundaries emit.


def _envelope_for(exc: AdCPError) -> dict:
    from src.core.exceptions import build_two_layer_error_envelope

    return build_two_layer_error_envelope(exc)


def _envelope_carrying(code: str, recovery: str, message: str = "x") -> dict:
    """A two-layer envelope carrying *code* and *recovery*, built as a literal.

    Deliberately NOT built from a real exception. These tests need an envelope
    whose pair CONTRADICTS the pin — that is the whole point of the helper they
    grade — and no exception can carry such a pair any more: ``recovery`` is a
    read-only property derived from the wire code, so production cannot express
    the contradiction and neither can a constructor kwarg. Building the dict
    directly keeps the guard alive without reintroducing the channel this epic
    closed. The shape mirrors ``build_two_layer_error_envelope``'s output, which
    is all ``assert_envelope_shape`` reads.
    """
    return {
        "adcp_error": {"code": code, "message": message, "recovery": recovery},
        "errors": [{"code": code, "message": message, "recovery": recovery}],
    }


def test_assert_envelope_shape_refuses_a_pin_contradicting_pair() -> None:
    """The F3 pair (``SERVICE_UNAVAILABLE`` + ``terminal``) must be ungradeable.

    The pin classifies ``SERVICE_UNAVAILABLE`` as ``transient``. A test that
    pins ``terminal`` for it is asserting that the wire may carry a pair the
    normative enumMetadata forbids, so the helper must fail it — and say which
    value the pin gives, because the fix is at the raise site (pick the class
    whose pinned recovery IS the intent), not in the assertion.
    """
    from tests.helpers import assert_envelope_shape

    pinned = _pinned_recovery_by_code()["SERVICE_UNAVAILABLE"]
    assert pinned == "transient", f"pin moved: SERVICE_UNAVAILABLE is now {pinned!r}"

    envelope = _envelope_carrying("SERVICE_UNAVAILABLE", "terminal", "permanent failure")
    assert envelope["adcp_error"]["recovery"] == "terminal", (
        f"precondition: the envelope must actually carry the contradicting pair, got {envelope['adcp_error']!r}"
    )

    with pytest.raises(AssertionError) as exc_info:
        assert_envelope_shape(envelope, "SERVICE_UNAVAILABLE", recovery="terminal")

    detail = str(exc_info.value)
    assert "enumMetadata" in detail and "'transient'" in detail, (
        f"assert_envelope_shape rejected the pair but its message does not cite the pin "
        f"or the value the pin gives: {detail!r}"
    )


def test_assert_envelope_shape_accepts_the_pinned_pair() -> None:
    """Control: the pin-conformant pair still passes.

    Without this, the test above is satisfied by a helper that rejects every
    call, which would grade nothing at all.
    """
    from src.core.exceptions import AdCPAdapterError
    from tests.helpers import assert_envelope_shape

    envelope = _envelope_for(AdCPAdapterError("upstream down"))
    assert_envelope_shape(envelope, "SERVICE_UNAVAILABLE", recovery="transient", message_substr="upstream down")


def test_assert_envelope_shape_keeps_the_caller_literal_for_unclassified_codes() -> None:
    """A code the pin does not classify keeps the caller's literal as its only
    expectation — the derivation adds a check, it does not replace the caller's.

    ``NOT_SUPPORTED`` is used here as a code the pin is SILENT on — which is
    exactly why #1417 removed it from the wire surface, via
    ``_SPEC_DEMOTED_CODES``. It is no longer emittable, and
    ``wire_advisory`` now subscripts rather than falling back, because the wire
    set is total over the recovery table by construction. What survives, and what
    this test still grades, is the OTHER unclassified path: ``AdCPError.recovery``
    keeps its ``.get`` because its domain is ``translate_error_code``'s output,
    which is unbounded (``tool_error_logging`` passes ``type(error).__name__``, so
    ``"ValueError"`` can arrive). ``TOOL_ERROR`` is the live representative of
    that path. Turning "the pin is silent" into a failure would make a genuinely
    reachable code ungradeable.
    """
    from tests.helpers import assert_envelope_shape

    assert "NOT_SUPPORTED" not in _pinned_recovery_by_code(), (
        "the pin now classifies NOT_SUPPORTED — this test needs a code the pin is still silent on"
    )

    envelope = _envelope_carrying("NOT_SUPPORTED", "terminal", "vendor said no")
    assert_envelope_shape(envelope, "NOT_SUPPORTED", recovery="terminal")


def test_assert_envelope_shape_derives_from_the_test_side_pin_not_src(monkeypatch) -> None:
    """The helper's expectation comes from ``pinned_schema.recovery_by_code()``.

    Independence, made behavioral rather than left to inspection: point the
    shared test-side accessor at a map that classifies ``SERVICE_UNAVAILABLE``
    as ``terminal`` and the helper's verdict must FOLLOW it. A helper that read
    ``src.core.exceptions.RECOVERY_BY_WIRE_CODE`` (or cached its own copy at
    import time) would keep rejecting, which is the shape that makes the grader
    agree with the table it is supposed to grade.
    """
    from src.core.exceptions import AdCPAdapterError
    from tests.helpers import assert_envelope_shape

    monkeypatch.setattr(pinned_schema, "recovery_by_code", lambda: {"SERVICE_UNAVAILABLE": "terminal"})

    envelope = _envelope_carrying("SERVICE_UNAVAILABLE", "terminal", "permanent failure")
    assert_envelope_shape(envelope, "SERVICE_UNAVAILABLE", recovery="terminal")

    with pytest.raises(AssertionError, match="enumMetadata"):
        assert_envelope_shape(
            _envelope_for(AdCPAdapterError("upstream down")), "SERVICE_UNAVAILABLE", recovery="transient"
        )


# ---------------------------------------------------------------------------
# raise-site mirror: recovery is DERIVED from the code, never named by a caller
# ---------------------------------------------------------------------------
# The oracles above close every table and every grader, and leave exactly one
# channel open: ``recovery=`` on ``AdCPError.__init__`` / ``AdCPError.synthesize``.
# A raise site can still hand-type a recovery the pinned enumMetadata contradicts
# for the code it emits, and nothing above sees it — the class-conformance oracles
# grade the CLASS DEFAULT, and the instance that reaches the envelope builder may
# carry something else entirely.
#
# These tests grade the closure: the kwarg is not defaulted-from-the-table, it is
# gone, so "I want terminal" is expressible only by raising a terminal-coded class.
# Possession of the class is the proof.
#
# Expectations come from ``_pinned_recovery_by_code()`` (tests/helpers, the SDK's
# pinned tree) and never from ``src.core.exceptions.RECOVERY_BY_WIRE_CODE``, so
# these grade the derivation rather than agreeing with the table it reads.


# (class, a recovery the class's own pinned wire code contradicts). Each pair is a
# contradiction a raise site can construct today: AdCPValidationError emits
# VALIDATION_ERROR (pinned correctable), AdCPAdapterError emits SERVICE_UNAVAILABLE
# (pinned transient), base AdCPError emits SERVICE_UNAVAILABLE too (INTERNAL_ERROR
# is remapped).
_CONTRADICTING_KWARG_CASES = [
    (AdCPValidationError, "terminal"),
    (AdCPAdapterError, "terminal"),
    (AdCPError, "correctable"),
]


@pytest.mark.parametrize(("cls", "requested"), _CONTRADICTING_KWARG_CASES, ids=lambda p: getattr(p, "__name__", p))
def test_recovery_is_not_a_constructor_kwarg(cls: type[AdCPError], requested: str) -> None:
    """``recovery=`` is not accepted by ``__init__`` — it is a ``TypeError``.

    Not "accepted and ignored", and not "accepted and defaulted from the table":
    a kwarg that silently does nothing still reads at the call site as a promise
    the wire keeps. The channel has to be unconstructible, which is what makes
    every emitted envelope's recovery definitionally the pin's value for the
    emitted code.

    The pairs above are precisely the contradictions this refusal removes — each
    one constructs successfully today and carries the caller's value onto the
    wire, past oracles that only ever graded the class default.
    """
    wire_code = translate_error_code(cls._default_error_code)
    pinned = _pinned_recovery_by_code()[wire_code]
    assert pinned != requested, (
        f"precondition: {requested!r} must contradict the pin for {wire_code!r} (pin says {pinned!r}) — "
        f"otherwise this case grades nothing"
    )

    with pytest.raises(TypeError, match="recovery"):
        cls("x", recovery=requested)  # type: ignore[call-arg]


def test_synthesize_does_not_accept_a_recovery_kwarg() -> None:
    """``synthesize`` loses the kwarg too — the boundary fallback is not an exemption.

    ``synthesize`` exists so a boundary can name a wire ``error_code`` the typed
    class hierarchy does not model; ``error_code=``/``status_code=`` therefore stay.
    ``recovery`` is different in kind: once the code is named, the pin already
    answers it. Leaving the kwarg here would keep one hand-typed pair reachable
    from the two boundary callers that synthesize the most opaque errors.
    """
    with pytest.raises(TypeError, match="recovery"):
        AdCPError.synthesize("x", error_code="SERVICE_UNAVAILABLE", recovery="terminal")  # type: ignore[call-arg]


# (code, whether the base class default already coincides with the pin). Both codes
# pass through ERROR_CODE_MAPPING untranslated, so the wire code IS the code named.
_SYNTHESIZED_CODES = ["SERVICE_UNAVAILABLE", "BUDGET_EXHAUSTED"]


@pytest.mark.parametrize("code", _SYNTHESIZED_CODES)
def test_synthesized_envelope_carries_the_pinned_recovery_in_both_layers(code: str) -> None:
    """A synthesized error's wire recovery follows its CODE, not the class it was synthesized on.

    ``synthesize`` is always called on a class whose own code is irrelevant (the
    caller is overriding it), so the class default is the wrong answer by
    construction. Both envelope layers are checked because both reach a buyer —
    the storyboard runner reads either path — and a derivation that only reaches
    one of them is the drift this module exists to catch.

    ``SERVICE_UNAVAILABLE`` coincides with the base class default (``transient``)
    and so already holds at HEAD; ``BUDGET_EXHAUSTED`` is pinned ``terminal`` and
    is what a class default can never answer correctly.
    """
    expected = _pinned_recovery_by_code()[code]
    envelope = _envelope_for(AdCPError.synthesize("vendor said no", error_code=code))

    layers = {
        "adcp_error": (envelope["adcp_error"]["code"], envelope["adcp_error"]["recovery"]),
        "errors[0]": (envelope["errors"][0]["code"], envelope["errors"][0]["recovery"]),
    }
    assert layers == {"adcp_error": (code, expected), "errors[0]": (code, expected)}, (
        f"synthesize(error_code={code!r}) produced {layers!r}; the pinned enumMetadata classifies "
        f"{code!r} as {expected!r}. Recovery must be derived from the code that reaches the wire, "
        f"not inherited from whichever class synthesize was called on."
    )


_WIRE_PINNED_CLASSES = sorted(
    (
        c
        for c in [AdCPError, *AdCPError.iter_concrete_subclasses()]
        if translate_error_code(c._default_error_code) in _RECOVERY_BY_CODE
    ),
    key=lambda c: c.__name__,
)


def test_every_concrete_class_has_a_pinned_wire_code() -> None:
    """Meta-guard: no concrete class escapes the instance mirror below.

    Distinct from ``test_remapped_classes_enumerated``, which pins the
    ERROR_CODE_MAPPING TARGET set: a class whose code is never remapped has no
    entry there, so a new class carrying an unpinned, untranslated code would
    slip past it and out of the parametrized set below without a failure.
    """
    ungraded = sorted(
        f"{c.__name__} ({c._default_error_code} -> {translate_error_code(c._default_error_code)})"
        for c in [AdCPError, *AdCPError.iter_concrete_subclasses()]
        if translate_error_code(c._default_error_code) not in _RECOVERY_BY_CODE
    )
    assert not ungraded, (
        f"Exception class(es) {ungraded} emit a wire code the pinned enumMetadata does not "
        f"classify, so their recovery cannot be derived and the mirror below does not grade "
        f"them. Map the code to a pinned wire code in ERROR_CODE_MAPPING, or advance the pin."
    )
    assert len(_WIRE_PINNED_CLASSES) >= 40, f"Expected to mirror many classes, got {len(_WIRE_PINNED_CLASSES)}"


def _instance_of(cls: type[AdCPError]) -> AdCPError:
    """An instance of *cls*, built through its own constructor wherever that is possible.

    A few subclasses take required domain keywords instead of a message — the
    egress seam's ``OutboundDeliveryFailed(attempts=..., http_status=...)`` is the
    live example — and cannot be built from a bare string. Their own ``__init__``
    delegates to ``AdCPError.__init__`` with a fixed message and never touches
    recovery, so the base initializer reaches the same object state this mirror
    reads: the class's own ``_default_error_code``, hence its own wire code.
    Skipping them instead would leave a live wire-facing class ungraded.
    """
    try:
        return cls("x")
    except TypeError:
        instance = cls.__new__(cls)
        AdCPError.__init__(instance, "x")
        return instance


@pytest.mark.parametrize("cls", _WIRE_PINNED_CLASSES, ids=lambda c: c.__name__)
def test_instance_recovery_matches_the_pinned_enum(cls: type[AdCPError]) -> None:
    """The INSTANCE's recovery — what the envelope builder actually reads — is the pin's.

    The oracles above grade ``_default_recovery``, a class attribute. The builder
    reads ``exc.recovery`` off an instance. With recovery derived rather than
    stored, those are the same answer for every class; this mirror is what says so,
    against the independently-loaded pin.
    """
    wire_code = translate_error_code(cls._default_error_code)
    expected = _RECOVERY_BY_CODE[wire_code]
    actual = _instance_of(cls).recovery
    assert actual == expected, (
        f"{cls.__name__} instance carries recovery={actual!r} but the pinned enumMetadata "
        f"classifies its wire code {wire_code!r} as {expected!r}. Recovery is derived from the "
        f"wire code; an instance cannot answer differently from the pin."
    )


def test_instance_recovery_follows_the_wire_code_table_at_read_time(monkeypatch) -> None:
    """The mirror above is a live derivation, not a coincidence of hand-typed literals.

    Mutation, so the mirror cannot pass vacuously: point the wire-code table at a
    map that classifies ``SERVICE_UNAVAILABLE`` as ``terminal`` and an
    ``AdCPAdapterError`` instance must FOLLOW it. A class that stores its literal
    at construction time keeps answering ``transient`` — which is exactly the
    shape where the per-class mirror and the class-default oracles grade the same
    hand-typed value twice and neither one grades a derivation.

    The mutated map also classifies the INTERNAL code ``ADAPTER_ERROR`` as
    ``correctable``. The instance must ignore that: derivation keys on the WIRE
    code (post-ERROR_CODE_MAPPING), which is the code the envelope emits.
    """
    from src.core import exceptions

    mutated = dict(exceptions.RECOVERY_BY_WIRE_CODE) | {
        "SERVICE_UNAVAILABLE": "terminal",
        "ADAPTER_ERROR": "correctable",
    }
    monkeypatch.setattr(exceptions, "RECOVERY_BY_WIRE_CODE", mutated)

    assert AdCPAdapterError("upstream down").recovery == "terminal", (
        "AdCPAdapterError('...').recovery did not follow RECOVERY_BY_WIRE_CODE's entry for its "
        "wire code SERVICE_UNAVAILABLE. Recovery must be read from the table at access time; a "
        "value frozen at construction (or copied into a class literal) is not a derivation, and "
        "the per-class mirror above then grades nothing."
    )
    assert AdCPValidationError("bad field").recovery == _pinned_recovery_by_code()["VALIDATION_ERROR"], (
        "an unmutated code changed value — the derivation must read per-code, not fall back to a "
        "single class-wide answer"
    )


def test_recovery_cannot_be_reassigned_on_an_instance() -> None:
    """Read-only: the closed constructor channel does not reopen one line later.

    ``exc = AdCPAdapterError(...); exc.recovery = "terminal"`` is the same
    contradiction as the deleted kwarg, spelled in two statements, and it reaches
    the envelope builder identically. A property with a setter would leave the
    channel open; a property without one makes the derivation the only answer.
    """
    exc = AdCPAdapterError("upstream down")
    with pytest.raises(AttributeError, match="recovery"):
        exc.recovery = "terminal"  # type: ignore[misc]
    assert exc.recovery == _pinned_recovery_by_code()["SERVICE_UNAVAILABLE"]
