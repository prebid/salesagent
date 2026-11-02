"""The one fabricated ``TransportResult`` for tests that cannot dispatch.

Six test modules need the state a dispatch LEAVES BEHIND without performing one —
either because the input is a client-side error no real dispatch can produce, or
because the subject is what a reader/assertion does with a known envelope rather
than how that envelope was obtained. Each hand-rolled the same construction, and
each held its own row in ``FIXTURE_CONSTRUCTORS``
(``tests/integration/test_harness_wire_response.py``). Seven structurally identical
blocks differing by one parameter is the duplication CLAUDE.md's DRY invariant
names, and collapsing them takes the escape-hatch allowlist from seven rows to two
— an allowlist that genuinely shrinks, which is the only kind of allowlist change
that rule permits.

WHAT DOES *NOT* BELONG HERE, because it is a different operation rather than a
variant of this one — ``test_harness_wire_response.py`` keeps its own row:

* its four real sites are SUCCESS-path (``payload`` + ``wire_response``), grading
  ``wire_field``/``wire_dict``. Not an error envelope at all.
* its fifth is ``TransportResult(payload=None)`` inside ``pytest.raises(TypeError)``
  — a site that exists TO NOT GO THROUGH ANYTHING. Routing it through a helper
  would delete the test it is.
* it IS the guard module for this contract. A guard that constructs through the
  helper it guards is grading itself.

``has_wire`` IS DERIVED, AND THAT IS THE POINT. Across all fifteen call sites the
declaration was never independent of the envelope: every one is either
(envelope captured, ``has_wire=True``) or (nothing captured, ``has_wire=False``).
The two are one question — did wire bytes come back — so the fixture asks it once
and cannot answer it inconsistently. That also keeps ABSENCE SAYABLE: the no-wire
sites are not a separate shape to be excluded, they are the paired control for the
wired ones, and a fixture that could only express the captured half would split a
deliberate contrast across two mechanisms.

A future site that genuinely needs an INCONSISTENT pair — ``has_wire=True`` with no
envelope, to grade that a reader complains — must construct ``TransportResult``
directly and take its own allowlist row with its own reason. That is the honest
escape, and it is deliberately not a parameter here: adding one would let the
inconsistent state be reached by accident, which is what the seam guard exists to
prevent.
"""

from __future__ import annotations

from typing import Any

from tests.harness.transport import TransportResult


def wire_error_result(
    wire_error_envelope: dict[str, Any] | None,
    *,
    error: Exception | None = None,
    envelope: dict[str, Any] | None = None,
) -> TransportResult:
    """A dispatch's error-path result, fabricated.

    Args:
        wire_error_envelope: The two-layer AdCP envelope the buyer received, or
            ``None`` for the no-wire control — the state a dispatch that never
            reached a transport leaves behind. This argument alone decides
            ``has_wire``.
        error: The exception a real dispatch would have carried. Most callers do
            not need it; supply it where the subject is what a step does with the
            error OBJECT (e.g. that it refuses a client-side one).
        envelope: Transport-specific metadata. Defaults to ``{}``, which is what
            every caller but one wants; it is a parameter because a caller
            grading the envelope-level mirror needs the same dict in both places.
    """
    return TransportResult(
        payload=None,
        envelope={} if envelope is None else envelope,
        error=error,
        wire_error_envelope=wire_error_envelope,
        # Derived, never passed in — see the module docstring.
        has_wire=wire_error_envelope is not None,
    )
