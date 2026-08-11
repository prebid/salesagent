"""Production-side invariant guard: the wire EMISSION vocabulary is canonical.

Foundation deliverable for salesagent-44c8 (error-code reconciliation). The
Core Invariant of that epic is: every error code that reaches the wire — whether
emitted by a production path or asserted by a BDD scenario — MUST be a member of
the canonical AdCP error-code enum (the pinned ``error-code.json`` that
``assert_wire_error`` already treats as authoritative), carrying that enum's
recovery classification.

``assert_wire_error`` (``tests/harness/transport.py``) guards only the TEST/BDD
side: it hard-fails when a scenario asserts a code the pinned enum does not
define. Nothing guards the PRODUCTION EMISSION side — ``WIRE_STANDARD_CODES``
(``src/core/exceptions.py``) is the set of codes production may put on the wire,
and today it can contain a code that is not in the canonical enum (a legacy SDK
code with zero canonical standing). This is the exact gap the architect review
of salesagent-44c8 flagged (xtbj.4 finding "NOT_SUPPORTED invariant gap +
MISSING production-side guard").

This test ties the production emission vocabulary to the SAME single source of
truth the test side is already guarded against — the pinned enum keys read via
``tests/harness/transport._pinned_error_metadata()``.

TDD-red note: this test FAILS today. ``WIRE_STANDARD_CODES`` inherits the legacy
SDK code ``NOT_SUPPORTED`` (via ``STANDARD_ERROR_CODES``), which is not in the
pinned enum and has zero production raise sites. The implement atom
(salesagent-xtbj.7) makes it green by mapping ``NOT_SUPPORTED`` ->
``UNSUPPORTED_FEATURE`` and bumping the pin 66 -> 92; this guard must NOT be
made to pass any other way.
"""

from __future__ import annotations

from src.core.exceptions import ERROR_CODE_MAPPING, WIRE_STANDARD_CODES
from tests.harness.transport import _pinned_error_metadata


def _canonical_codes() -> set[str]:
    """Keys of the pinned AdCP error-code enum — the canonical wire vocabulary.

    Same source ``assert_wire_error`` uses, so "canonical" means the same thing
    on the production-emission side as it does on the BDD/test side.
    """
    return set(_pinned_error_metadata())


def test_wire_standard_codes_are_all_canonical() -> None:
    """Every code production may emit on the wire is a canonical AdCP code.

    ``WIRE_STANDARD_CODES`` is the production emission vocabulary; it must be a
    subset of the pinned enum keys that ``assert_wire_error`` validates against.
    A code here that is not canonical means production can emit a wire code no
    buyer-facing spec defines — a Core-Invariant violation.
    """
    emittable = set(WIRE_STANDARD_CODES)
    canonical = _canonical_codes()
    offenders = sorted(emittable - canonical)
    assert not offenders, (
        "WIRE_STANDARD_CODES contains non-canonical wire codes not in the pinned "
        f"AdCP error-code enum: {offenders}. Production may emit these on the wire "
        "but no buyer-facing AdCP spec defines them (assert_wire_error would "
        "hard-fail on them). Map each to a canonical code via ERROR_CODE_MAPPING "
        "(or bump the pin if the code is genuinely canonical in v3.1.1)."
    )


def test_error_code_mapping_targets_are_all_emittable() -> None:
    """Every ERROR_CODE_MAPPING target is an emittable standard code.

    Mirrors the runtime assert at ``src/core/exceptions.py`` (mapping targets must
    be a subset of ``WIRE_STANDARD_CODES``) as a first-class test. Combined with
    the canonical-subset check above, this transitively proves every translation
    target is also a canonical AdCP code.
    """
    targets = set(ERROR_CODE_MAPPING.values())
    emittable = set(WIRE_STANDARD_CODES)
    offenders = sorted(targets - emittable)
    assert not offenders, (
        "ERROR_CODE_MAPPING translates internal codes to targets that are not in "
        f"WIRE_STANDARD_CODES: {offenders}. Every translation target must itself be "
        "an emittable standard code."
    )
