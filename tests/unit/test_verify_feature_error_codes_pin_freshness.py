"""Regression test: the vendored error-code enum must track the CURRENT spec pin.

Bug salesagent-ulft: tests/fixtures/adcp_schemas_pinned/enums/error-code.json was
vendored from adcp commit 04f59d2d5 ("AdCP 3.1", 64 codes) while the rest of the
project pins AdCP 3.1.1 (92 codes, adcp==6.6.0). scripts/verify_feature_error_codes.py
reads this fixture as its canonical source, so its --casing-only repo-wide gate
(gated in make quality) could not see any codes added between 3.1 and 3.1.1 — a
BDD feature using a lowercase spelling of one of those 28 new codes (e.g.
"stale_response", added in 3.1.1) classified as neither LOWERCASE_VARIANT nor
NON_CANONICAL (classify() returned None), so the casing gate silently passed the
exact defect it exists to catch.

Verified failing before the fix (manual measurement against the stale 64-code
fixture, matching the ticket's own numbers): classify("stale_response", enum) and
classify("format_not_supported", enum) both returned None. Re-vendoring the
fixture at the CURRENT pin (v3.1.1, via tests/fixtures/adcp_schemas_pinned/_refresh.py)
fixes this; this test pins the fixed state and the reproducible check.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import verify_feature_error_codes as m  # noqa: E402

# Codes added to the AdCP error-code enum between 3.1 and 3.1.1 (per the ticket's
# own measurement) — a lowercase spelling of any of these was invisible to the
# --casing-only gate under the stale 64-code fixture.
_CODES_ADDED_BETWEEN_3_1_AND_3_1_1 = ("FORMAT_NOT_SUPPORTED", "STALE_RESPONSE")


def test_enum_has_at_least_92_codes() -> None:
    """The pinned enum must track AdCP 3.1.1 (92 codes), not the stale 3.1 pin (64)."""
    enum = m.load_enum()
    assert len(enum) >= 92, (
        f"Vendored error-code enum has only {len(enum)} codes — expected >= 92 (AdCP 3.1.1). "
        "Re-run tests/fixtures/adcp_schemas_pinned/_refresh.py against the current pin."
    )


def test_post_311_codes_are_classified_as_lowercase_variant_when_lowercased() -> None:
    """A lowercase spelling of a code added in 3.1.1 must be caught by the casing gate.

    This is the exact defect: under the stale 64-code fixture, these tokens'
    uppercase form was NOT in the enum, so classify() returned None instead of
    LOWERCASE_VARIANT — invisible to make quality's --casing-only gate.
    """
    enum = m.load_enum()
    for code in _CODES_ADDED_BETWEEN_3_1_AND_3_1_1:
        assert code in enum, f"{code} missing from the vendored enum — pin is stale"
        verdict = m.classify(code.lower(), enum)
        assert verdict == m.LOWERCASE_VARIANT, (
            f"classify({code.lower()!r}, enum) returned {verdict!r}, expected LOWERCASE_VARIANT — "
            "the casing gate cannot see this code, reproducing salesagent-ulft's defect"
        )
