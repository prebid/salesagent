"""Oracle: the recovery this platform puts on the wire IS the pinned
``error-code.json`` ``enumMetadata`` recovery classification (#1417).

The ``enumMetadata`` block is normative — its ``$comment`` states: "SDKs MUST
consume this block ... the recovery classification embedded in that prose is
normative and MUST match the value here."

**Why this guard still exists after ADR-010.** The version of this file on
``origin/main`` graded a per-class ``_default_recovery`` literal against the pin.
That literal is gone: ``recovery`` is now a read-only property over
``CODE_TABLE`` (``src/core/errors/codes.py``), and ``__init_subclass__`` refuses
a ``_code`` the table does not classify, so a class can no longer carry a
recovery of its own to drift. Grading a per-class literal here would grade
nothing.

What did NOT become structural is the step underneath it. Every recovery answer
in production — every class, every instance, every envelope — now funnels
through ONE loader, ``codes._load_published_codes()``, reading one block of one
file. That is a single point of failure that nothing else grades: a loader that
resolved the wrong schema bundle, read ``enumDescriptions`` prose instead of the
normative ``enumMetadata``, or fell back to the SDK helper's
``STANDARD_ERROR_CODES`` (which contradicts the pin on 7 of its 38 codes) would
produce a table of entirely plausible size and shape, and every downstream test
that asserts recovery would agree with it — because they all read it. Deleting
this file would have made the loader unenforced, not redundant.

So the obligations kept here are the ones that survive ADR-010, restated against
the merged design:

  * ``CODE_TABLE`` mirrors the pin, code for code, over the pin's full key set;
  * an INSTANCE of every concrete ``AdCPSalesAgentError`` subclass — what the
    envelope builder reads — reports the pinned recovery for its code;
  * the derivation is LIVE at read time, not a literal frozen at construction;
  * ``recovery`` cannot be reassigned onto an instance, and is not a constructor
    kwarg either — the two spellings of one contradiction;
  * the platform-only codes, which sit outside the pin and so escape the oracle
    entirely, are a pinned roster rather than an open door;
  * ``assert_envelope_shape`` — the one helper every other suite asserts an error
    through — derives its own expectation from this same pinned block, so no test
    anywhere can grade a pair the spec contradicts.

That last obligation belongs HERE rather than beside the helper: the grader's
derivation and ``CODE_TABLE``'s derivation read the same normative block, and an
oracle is worth only as much as the assertion helper the rest of the suite
reaches it through.

Dropped with the symbols they exercised, all deleted by ADR-010 as a second
answer to a question ``CODE_TABLE`` already answers: ``ERROR_CODE_MAPPING`` /
``translate_error_code`` / ``WIRE_STANDARD_CODES`` (boundary translation — the
AdCP code vocabulary is OPEN, codes reach the buyer verbatim),
``RECOVERY_BY_WIRE_CODE`` (a second load of this same block), ``synthesize``
and ``wire_advisory``. The oracle that graded ``synthesize(recovery=...)``
went with it; the one that graded ``AdCPSalesAgentError(recovery=...)`` did not,
because that constructor still exists and keeping a caller out of it is still a
live obligation.

Every expectation below is read from the pin through
``tests.helpers.pinned_schema.recovery_by_code()`` — the ONE test-side reader of
that block — and never from ``src``'s table. That independence from ``src`` is
the load-bearing property: the oracle grades src's loader rather than agreeing
with it.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from adcp.signing.errors import REQUEST_TO_WEBHOOK_CODE

from src.core import exceptions
from src.core.errors.codes import CODE_TABLE, AppErrorCode, SignatureErrorCode
from src.core.exceptions import (
    AdCPAdapterError,
    AdCPRequestSignatureAlgNotAllowedError,
    AdCPSalesAgentError,
    AdCPValidationError,
)
from src.core.schemas._base import AdcpErrorResponse
from src.core.tools._wire import to_wire
from tests.helpers import assert_envelope_shape, pinned_schema

_RECOVERY_BY_CODE = pinned_schema.recovery_by_code()

#: Codes this platform emits that AdCP's published enum does not define. They are
#: legal on the wire (the vocabulary is open) but the pin cannot classify them, so
#: they are outside every assertion below. Pinned as a roster so that adding one —
#: which buys a code no spec oracle grades — is a deliberate edit here, not a
#: silent widening of the ungraded set.
_KNOWN_PLATFORM_CODES = frozenset(
    {
        "ACTIVATION_WORKFLOW_FAILED",
        "AD_SERVER_CREATE_FAILED",
        "AD_SERVER_UPDATE_FAILED",
        "AGENT_UNREACHABLE",
        "INTERNAL_ERROR",
        "MEDIA_BUY_REJECTED",
        "PARTIAL_FAILURE",
        "WORKFLOW_CREATION_FAILED",
    }
    # The RFC 9421 transport error taxonomy. The 27 SDK-table rows are NOT listed here by
    # hand: the roster reads the same ``adcp.signing.errors`` table
    # ``src.core.errors.signature_codes`` generates its members from, so the two cannot
    # drift and adding one of those is not a thing anybody can do here.
    #
    # They are outside the assertions below for the reason the roster exists -- the pinned
    # error-code enum does not define them -- but they are not ungraded. The spec grades
    # them directly, byte-for-byte, in the ``WWW-Authenticate: Signature error="<code>"``
    # challenge (security.mdx @ v3.1.1 § Transport error taxonomy), and
    # ``tests/unit/test_signature_challenge_string.py`` pins that string for every one of
    # them against the SDK's own builder.
    | frozenset(REQUEST_TO_WEBHOOK_CODE)
    # The 28th, and the ONE code in this family that has to be named: production's
    # ``_TAXONOMY`` is the SDK's retag table PLUS ``request_target_uri_malformed``, which
    # ``REQUEST_TO_WEBHOOK_CODE`` omits at ``adcp==6.6.0`` (upstream fix: adcp-client-python
    # PR #987). The verifier emits it from ``reject_malformed_target``
    # (``src/core/signing/canonical.py``), so it reaches ``CODE_TABLE`` and the wire like the
    # other 27, and the shipped conformance vectors grade it -- six ``reject: true``
    # canonicalization cases expect exactly this string
    # (``tests/unit/test_signing_conformance_canonicalization.py``).
    #
    # Written as a literal rather than imported from ``src``, for the reason the module
    # docstring gives: this roster is an oracle, and importing production's own constant
    # would restate ``_TAXONOMY``'s expression instead of grading it. The same literal
    # stands, deliberately and for the same reason, in
    # ``tests/unit/test_signing_challenge_vocabulary.py``. The union is idempotent, so when
    # the pin advances past the upstream fix this line stops mattering on its own.
    | frozenset({"request_target_uri_malformed"})
)


def _code_of(cls: type[AdCPSalesAgentError]) -> str:
    """The code a class IS, or ``""`` for an ABSTRACT one that declares none.

    Two classes declare no ``_code`` and neither can be constructed:
    :class:`AdCPSalesAgentError` and :class:`AdCPRequestSignatureError`. The second used to
    be constructible and to name its code per raise site; it is now the abstract parent of
    the 28 hand-written classes below it, one per member of the RFC 9421 transport
    taxonomy, each declaring its own code like every other error class here.

    Those 28 are outside THIS oracle, which grades a class against the PUBLISHED enum, and
    the published enum does not define them (the wire vocabulary is open). They are not
    ungraded: every one goes through ``CodeEntry``, whose constructor refuses an empty
    message or suggestion and whose ``recovery`` is a :class:`Recovery` member, and the
    challenge string they produce is pinned against the SDK in
    ``tests/unit/test_signature_challenge_string.py``.
    """
    return str(getattr(cls, "_code", ""))


_GRADED_CLASSES = sorted(
    (c for c in AdCPSalesAgentError.iter_concrete_subclasses() if _code_of(c) in _RECOVERY_BY_CODE),
    key=lambda c: c.__name__,
)


def _instance_of(cls: type[AdCPSalesAgentError]) -> AdCPSalesAgentError:
    """An instance of *cls*, built through its own constructor wherever possible.

    A subclass that requires domain keywords still delegates to
    ``AdCPSalesAgentError.__init__``, which sets ``_error_code`` from the class's
    own ``_code`` and never touches recovery — so the fallback reaches the same
    object state this oracle reads. Skipping such a class instead would leave a
    live wire-facing class ungraded.
    """
    try:
        return cls()
    except TypeError:
        instance = cls.__new__(cls)
        AdCPSalesAgentError.__init__(instance)
        return instance


def test_pinned_enum_metadata_loaded() -> None:
    """Meta-guard: the pin loaded and a representative set is graded, so the
    parametrized oracles below can never silently degrade to zero cases."""
    assert len(_RECOVERY_BY_CODE) >= 50, (
        f"Expected the pinned enumMetadata to define recovery for many codes, got {len(_RECOVERY_BY_CODE)}"
    )
    assert len(_GRADED_CLASSES) >= 25, (
        f"Expected to grade many AdCPSalesAgentError subclasses, got {len(_GRADED_CLASSES)}"
    )


def test_code_table_mirrors_pinned_enum() -> None:
    """``CODE_TABLE`` carries the pin's recovery for every published code.

    Exact per-code equality over the pin's full key set, not a size check: a
    loader that resolves the wrong schema bundle, parses the ``Recovery: X``
    clause out of ``enumDescriptions`` prose instead of reading the normative
    ``enumMetadata``, or falls back to the SDK helper's ``STANDARD_ERROR_CODES``
    all produce a table of plausible size. The expectation is loaded
    independently via ``tests.helpers.pinned_schema`` and never read from src's
    table, so this grades src's loader rather than agreeing with it.
    """
    src_table = {str(code): str(entry.recovery) for code, entry in CODE_TABLE.items()}

    missing = sorted(set(_RECOVERY_BY_CODE) - set(src_table))
    assert not missing, (
        f"CODE_TABLE does not classify published code(s) {missing}. Every code in the pinned "
        f"error-code.json enum reaches buyers verbatim (the AdCP code vocabulary is open), so a "
        f"code with no entry makes the recovery answer a KeyError at the moment a caller needs it."
    )

    divergent = {
        code: {"src": src_table[code], "pin": expected}
        for code, expected in sorted(_RECOVERY_BY_CODE.items())
        if src_table[code] != expected
    }
    assert not divergent, (
        f"CODE_TABLE diverges from the pinned error-code.json enumMetadata on "
        f"{len(divergent)} code(s): {divergent}. The enumMetadata block is normative "
        f"($comment: 'SDKs MUST consume this block instead of parsing Recovery: X from "
        f"enumDescriptions prose') — load it at import time; do not hand-type values and do "
        f"not source them from the SDK helper's STANDARD_ERROR_CODES."
    )


def test_platform_only_codes_are_the_pinned_roster() -> None:
    """Codes ``CODE_TABLE`` classifies that the pin does not define are exactly the
    platform roster.

    These are legal on the wire but ungradeable against a spec enum that does not
    contain them, so they escape every assertion above. Pinning the roster means a
    NEW platform code is surfaced for review here rather than silently joining the
    ungraded set.
    """
    unpinned = {str(code) for code in CODE_TABLE if str(code) not in _RECOVERY_BY_CODE}
    assert unpinned == _KNOWN_PLATFORM_CODES, (
        f"CODE_TABLE's non-spec codes are {sorted(unpinned)}, expected {sorted(_KNOWN_PLATFORM_CODES)}. "
        f"A code outside the pinned enum carries a hand-authored recovery no spec oracle grades. "
        f"Either add it to the AdCP error-code enum (and advance the pin), or record it in "
        f"_KNOWN_PLATFORM_CODES here."
    )
    assert unpinned == {str(member) for member in AppErrorCode} | {str(member) for member in SignatureErrorCode}, (
        "CODE_TABLE's non-spec codes must be exactly the AppErrorCode members plus the "
        "SignatureErrorCode ones — a published code shadowed by an entry from either enum would "
        "take its recovery from that entry, not the pin."
    )


@pytest.mark.parametrize("cls", _GRADED_CLASSES, ids=lambda c: c.__name__)
def test_instance_recovery_matches_pinned_enum(cls: type[AdCPSalesAgentError]) -> None:
    """Each subclass's INSTANCE recovery — what the envelope builder reads — equals
    the pinned enum's normative value for the class's code."""
    code = _code_of(cls)
    expected = _RECOVERY_BY_CODE[code]
    actual = str(_instance_of(cls).recovery)
    assert actual == expected, (
        f"{cls.__name__} (code {code!r}) reports recovery={actual!r} but the pinned "
        f"error-code.json enumMetadata says {expected!r}. The enumMetadata recovery is "
        f"normative: fix the derivation, or advance the pin if the spec changed."
    )


def test_instance_recovery_follows_the_code_table_at_read_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mirror above is a live derivation, not a coincidence of hand-typed literals.

    Mutation, so the mirror cannot pass vacuously: point the table at a map that
    classifies ``VALIDATION_ERROR`` differently and an instance must FOLLOW it. A
    class that froze its recovery at construction — or copied it into a class
    literal, the channel ADR-010 removed — keeps answering the old value, which is
    exactly the shape where the oracle grades a literal twice and grades no
    derivation at all.
    """
    code = AdCPValidationError._code
    pinned = _RECOVERY_BY_CODE[str(code)]
    flipped = "terminal" if pinned != "terminal" else "transient"
    mutated = dict(CODE_TABLE)
    mutated[code] = dataclasses.replace(CODE_TABLE[code], recovery=flipped)
    monkeypatch.setattr(exceptions, "CODE_TABLE", mutated)

    assert str(AdCPValidationError().recovery) == flipped, (
        f"AdCPValidationError().recovery did not follow CODE_TABLE's entry for {str(code)!r}. "
        f"Recovery must be read from the table at access time; a value frozen at construction "
        f"(or copied into a class literal) is not a derivation, and the mirror above then "
        f"grades nothing."
    )


def test_recovery_cannot_be_reassigned_on_an_instance() -> None:
    """Read-only: the closed constructor channel does not reopen one line later.

    ``exc = AdCPValidationError(); exc.recovery = "terminal"`` is the same
    contradiction as a ``recovery=`` kwarg spelled in two statements, and it
    reaches the envelope builder identically. A property without a setter makes
    the derivation the only answer.
    """
    exc = AdCPValidationError()
    with pytest.raises(AttributeError):
        exc.recovery = "terminal"  # type: ignore[misc]
    assert str(exc.recovery) == _RECOVERY_BY_CODE[str(AdCPValidationError._code)]


# ---------------------------------------------------------------------------
# raise-site mirror: recovery is DERIVED from the code, never named by a caller
# ---------------------------------------------------------------------------
# The reassignment oracle above closes the channel one statement AFTER
# construction. This closes it AT construction, which is where a raise site would
# reach for it: ``raise AdCPValidationError(recovery="terminal")`` is the same
# contradiction spelled earlier, and nothing else in this file would see it —
# the per-class oracles grade the CLASS's code, and an instance carrying a
# hand-typed recovery is what the envelope builder would actually read.
#
# ``__init__`` is keyword-only and names no ``recovery`` parameter, so the refusal
# is structural rather than scanned for. This grades that it stays structural:
# "I want terminal" must remain expressible only by raising a terminal-coded
# class, where possession of the class is the proof.


_CONTRADICTING_KWARG_CASES = [
    # (class, a recovery its own pinned code contradicts). AdCPValidationError emits
    # VALIDATION_ERROR (pinned correctable); AdCPAdapterError emits SERVICE_UNAVAILABLE
    # (pinned transient). Each pair is a contradiction a raise site would want to spell.
    (AdCPValidationError, "terminal"),
    (AdCPAdapterError, "correctable"),
]


@pytest.mark.parametrize(("cls", "requested"), _CONTRADICTING_KWARG_CASES, ids=lambda p: getattr(p, "__name__", p))
def test_recovery_is_not_a_constructor_kwarg(cls: type[AdCPSalesAgentError], requested: str) -> None:
    """``recovery=`` is not accepted at construction, and the pinned value survives."""
    with pytest.raises(TypeError):
        cls(recovery=requested)  # type: ignore[call-arg]

    pinned = _RECOVERY_BY_CODE[str(cls._code)]
    assert pinned != requested, (
        f"this case no longer states a contradiction: the pin classifies {str(cls._code)!r} as "
        f"{pinned!r}, which is what the rejected kwarg asked for. Pick another value."
    )
    assert str(cls().recovery) == pinned, (
        f"{cls.__name__}().recovery is {str(cls().recovery)!r}, not the pinned {pinned!r} — a "
        f"refused kwarg is only half the closure; the derivation must still answer."
    )


# ---------------------------------------------------------------------------
# test-side mirror: the grader itself cannot grade a pin-contradicting pair
# ---------------------------------------------------------------------------
# ``assert_envelope_shape`` took ``recovery`` as a free caller literal and checked
# only that the two envelope layers agreed WITH EACH OTHER, so it was blind to
# wire<->spec drift: a shipped, green test asserted SERVICE_UNAVAILABLE+terminal,
# a pair the normative enumMetadata contradicts. The helper now derives the
# expected recovery from the same pinned block these oracles read. These tests are
# what stops that derivation from being silently removed the next time it reddens
# something — the failure mode being re-armed is "the grader agrees with the bug".
#
# Where an envelope can be real it is built by the production path
# (``AdcpErrorResponse.of`` serialized through ``to_wire``), so the shapes graded
# are the shapes the boundaries emit.


def _envelope_for(exc: AdCPSalesAgentError) -> dict[str, Any]:
    """The wire body a buyer receives for *exc*, built the way the boundary builds it."""
    return to_wire(AdcpErrorResponse.of(exc))


def _envelope_carrying(code: str, recovery: str, message: str = "x") -> dict[str, Any]:
    """A two-layer envelope carrying *code* and *recovery*, built as a literal.

    Deliberately NOT built from a real exception. The test below needs an envelope
    whose pair CONTRADICTS the pin — that is the whole point of the helper it
    grades — and no exception can carry such a pair any more: ``recovery`` is a
    read-only property over ``CODE_TABLE`` and there is no constructor kwarg, so
    production cannot express the contradiction. Building the dict directly keeps
    the guard alive without reopening the channel ADR-010 closed. The shape mirrors
    ``AdcpErrorResponse.of``'s output, which is all ``assert_envelope_shape`` reads.
    """
    return {
        "adcp_error": {"code": code, "message": message, "recovery": recovery},
        "errors": [{"code": code, "message": message, "recovery": recovery}],
    }


def test_assert_envelope_shape_refuses_a_pin_contradicting_pair() -> None:
    """The F3 pair (``SERVICE_UNAVAILABLE`` + ``terminal``) must be ungradeable.

    The pin classifies ``SERVICE_UNAVAILABLE`` as ``transient``. A test that pins
    ``terminal`` for it is asserting that the wire may carry a pair the normative
    enumMetadata forbids, so the helper must fail it — and say which value the pin
    gives, because the fix is at the raise site (pick the class whose pinned
    recovery IS the intent), not in the assertion.
    """
    pinned = _RECOVERY_BY_CODE["SERVICE_UNAVAILABLE"]
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
    """Control: the pin-conformant pair, on a real production envelope, still passes.

    Without this, the test above is satisfied by a helper that rejects every call,
    which would grade nothing at all.
    """
    assert_envelope_shape(_envelope_for(AdCPAdapterError()), "SERVICE_UNAVAILABLE", recovery="transient")


def test_assert_envelope_shape_keeps_the_caller_literal_for_unclassified_codes() -> None:
    """A code the pin does not classify keeps the caller's literal as its only
    expectation — the derivation ADDS a check, it does not replace the caller's.

    The RFC 9421 transport codes are the live representatives of that path:
    ``CODE_TABLE`` classifies all 27, the published error-code enum defines none of
    them (the wire vocabulary is open), and they reach buyers in real refusals.
    Turning "the pin is silent" into a failure would make every signature refusal
    ungradeable through the one sanctioned envelope helper.

    The expected recovery here is read from ``CODE_TABLE`` rather than from the pin
    for the reason the test exists: for a code the pin does not define there IS no
    test-side oracle. What is graded is the helper's branch, not the value —
    ``test_platform_only_codes_are_the_pinned_roster`` above is what keeps this set
    of ungraded codes from widening.
    """
    code = SignatureErrorCode.REQUEST_SIGNATURE_ALG_NOT_ALLOWED
    assert str(code) not in _RECOVERY_BY_CODE, (
        f"the pin now classifies {str(code)!r} — this test needs a code the pin is still silent on"
    )

    exc = AdCPRequestSignatureAlgNotAllowedError()
    assert_envelope_shape(_envelope_for(exc), str(code), recovery=str(CODE_TABLE[code].recovery))


def test_assert_envelope_shape_derives_from_the_test_side_pin_not_src(monkeypatch: pytest.MonkeyPatch) -> None:
    """The helper's expectation comes from ``pinned_schema.recovery_by_code()``.

    Independence, made behavioral rather than left to inspection: point the shared
    test-side accessor at a map that classifies ``SERVICE_UNAVAILABLE`` as
    ``terminal`` and the helper's verdict must FOLLOW it. A helper that read
    ``CODE_TABLE`` (or cached its own copy at import time) would keep rejecting,
    which is the shape that makes the grader agree with the table it grades.
    """
    monkeypatch.setattr(pinned_schema, "recovery_by_code", lambda: {"SERVICE_UNAVAILABLE": "terminal"})

    assert_envelope_shape(
        _envelope_carrying("SERVICE_UNAVAILABLE", "terminal", "permanent failure"),
        "SERVICE_UNAVAILABLE",
        recovery="terminal",
    )

    with pytest.raises(AssertionError, match="enumMetadata"):
        assert_envelope_shape(_envelope_for(AdCPAdapterError()), "SERVICE_UNAVAILABLE", recovery="transient")
