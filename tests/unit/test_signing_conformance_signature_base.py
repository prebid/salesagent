"""L1(a) — signature-base conformance, over the vectors' ORIGINAL untouched URLs.

#1291 B3 (``salesagent-z6nr.14``), design step 3. Grading level L1 of three:

* **L1 (here)** — no app, no DB, no crypto, no transplant, no re-signing. For every
  vector shipping ``expected_signature_base``, compute the signature base from the
  vector's OWN url/method/headers and assert it equals the shipped bytes EXACTLY.
* **L2** (``tests/integration/test_signing_conformance_vectors.py``) — the 15-check
  ordering, the wire envelope and the error codes, through the real middleware.
* **L3** — B4 (``salesagent-z6nr.15``), black-box, the vectors' own URL space.

Why this level cannot go green vacuously — and why it must exist. At L2 the harness
re-signs 14 transplanted vectors using the SAME canonicalizer the verifier uses, so
signer and verifier agree BY CONSTRUCTION whatever they compute; positives 005-012
(default-port, dot-segment, query bytes, percent-encoding, IPv6) would pass
tautologically if L2 were the only grading. Here NOTHING of ours produced the
expected bytes: ``expected_signature_base`` is spec-authored data, and the vector
README says it "exists specifically to make this check byte-level and
implementation-independent", warning that "locking a canonicalization bug into the
committed signatures would be the worst outcome". This is the level that grades
canonicalization honestly.

Deliberately NOT asserted: that re-signing the base reproduces the vector's shipped
``Signature`` bytes. The README calls that a convenience check, explicitly not
normative, and ES256 is non-deterministic under random-k.

Spec grounding: AdCP 3.1.1 (``adcp==6.6.0``);
``adcontextprotocol/adcp@v3.1.1:docs/reference/url-canonicalization.mdx``
(the authoritative canonicalization algorithm — security.mdx §"@target-uri
canonicalization" defers to it) and ``dist/compliance/3.1.1/test-vectors/
request-signing/{positive,negative}/``.
"""

from __future__ import annotations

import pytest
from adcp.signing.canonical import build_signature_base, parse_signature_input_header

from tests.helpers.signing_vectors import VERIFIED_LABEL, vectors_with_expected_signature_base

_L1_VECTORS = sorted(vectors_with_expected_signature_base())


def _diff(actual: str, expected: str) -> str:
    """A line-indexed diff, so a failure names the canonicalized component that broke."""
    actual_lines, expected_lines = actual.split("\n"), expected.split("\n")
    for index in range(max(len(actual_lines), len(expected_lines))):
        got = actual_lines[index] if index < len(actual_lines) else "<missing>"
        want = expected_lines[index] if index < len(expected_lines) else "<missing>"
        if got != want:
            return f"first divergence at line {index}:\n  computed: {got!r}\n  spec:     {want!r}"
    return "no line differs (trailing-byte difference?)"


@pytest.mark.parametrize("vector_id", _L1_VECTORS)
def test_signature_base_matches_the_spec_bytes(vector_id: str) -> None:
    """Our computed signature base equals the vector's shipped bytes, byte-for-byte.

    The URL is passed UNTOUCHED — that is the whole point of this level.
    """
    vector = vectors_with_expected_signature_base()[vector_id]
    req = vector["request"]
    headers = {key.lower(): value for key, value in req["headers"].items()}

    parsed = parse_signature_input_header(headers["signature-input"])
    assert VERIFIED_LABEL in parsed, (
        f"{vector_id}: no {VERIFIED_LABEL!r} label in Signature-Input (labels: {sorted(parsed)}). "
        "The AdCP profile mandates the verifier process exactly one label, conventionally "
        "sig1, and positive/004 pins expected_outcome.verified_label == 'sig1' — so this is "
        "asserted rather than defaulting to 'the first key'."
    )

    base = build_signature_base(req["method"], req["url"], headers, parsed[VERIFIED_LABEL])
    expected = vector["expected_signature_base"]
    assert base == expected, f"{vector_id}: signature base diverges from the spec bytes.\n{_diff(base, expected)}"


def test_l1_grades_every_vector_that_ships_the_field() -> None:
    """Sanity on the parametrization itself: 13 cases, from the vendored data.

    The count is corrected from the B3 design's "12" — that figure missed
    ``negative/010-content-digest-mismatch``, which ships the field too. The exact
    SET is pinned by name in ``tests/unit/test_adcp_conformance_vectors_pin.py``.
    """
    assert len(_L1_VECTORS) == 13, _L1_VECTORS
