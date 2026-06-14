"""Unit tests for resolve_pre_v3_buying_mode.

Pins the spec-deviation default (wholesale, not brief) so changing line 109 of
src/core/product_conversion.py would break a test instead of changing behavior
silently. AdCP 3.0 spec text says "Sellers receiving requests from pre-v3 clients
without buying_mode SHOULD default to 'brief'." — we deliberately deviate to
'wholesale' when the pre-v3 client sent no brief either (defaulting to 'brief'
would immediately fail the cross-mode validator). The flag observed in the audit
log is the recovery mechanism.
"""

import pytest

from src.core.product_conversion import resolve_pre_v3_buying_mode


class TestResolvePreV3BuyingMode:
    """Lock in the pre-v3 default shim contract."""

    @pytest.mark.parametrize(
        ("buying_mode", "adcp_version", "brief", "expected_mode", "expected_defaulted"),
        [
            # ── pre-v3 client (no buying_mode) ──
            # Spec-deviation: empty brief defaults to 'wholesale', not 'brief'.
            ("__omitted__", "2.5.0", None, "wholesale", True),
            ("__omitted__", "2.5.0", "", "wholesale", True),
            ("__omitted__", "2.5.0", "   ", "wholesale", True),
            # Pre-v3 + non-empty brief: default to 'brief' (spec-compatible).
            ("__omitted__", "2.5.0", "find me video ads", "brief", True),
            # ── v3 client (with buying_mode supplied) ──
            ("brief", "3.0.0", "find me video ads", "brief", False),
            ("wholesale", "3.0.0", "", "wholesale", False),
            ("refine", "3.0.0", None, "refine", False),
            # ── v3 client without buying_mode is a programmer error — passthrough as None ──
            # The schema validator catches missing buying_mode on v3+; the shim only
            # activates for explicit pre-v3 versions.
            ("__omitted__", "3.0.0", "any", None, False),
            # ── No adcp_version declared → treated as pre-v3 (safer-default semantics
            #    shared with needs_v2_compat) → defaulting still applies ──
            ("__omitted__", None, "any", "brief", True),
            ("__omitted__", None, "", "wholesale", True),
            # ── Pre-v3 with explicit buying_mode → passthrough (shim only fills gaps) ──
            ("wholesale", "2.5.0", None, "wholesale", False),
            ("brief", "2.5.0", "x", "brief", False),
        ],
    )
    def test_resolve_returns_expected(
        self,
        buying_mode: str,
        adcp_version: str | None,
        brief: str | None,
        expected_mode: str | None,
        expected_defaulted: bool,
    ):
        """Pin every documented branch of the shim, including the wholesale spec-deviation."""
        actual_mode, actual_defaulted = resolve_pre_v3_buying_mode(
            None if buying_mode == "__omitted__" else buying_mode,
            adcp_version,
            brief,
        )
        assert actual_mode == expected_mode, f"mode={actual_mode!r} expected={expected_mode!r}"
        assert actual_defaulted is expected_defaulted

    def test_wholesale_default_is_observable_via_flag(self):
        """The spec-deviation case must set pre_v3_defaulted=True so audit can see it.

        AdCP 3.0 spec says default-to-'brief'; we default to 'wholesale' when no brief.
        The defaulted flag is the only signal that this PR's wrapper applied the deviation.
        """
        mode, defaulted = resolve_pre_v3_buying_mode(None, "2.0.0", None)
        assert mode == "wholesale"
        assert defaulted is True, "pre_v3_defaulted must be True so the audit log records the spec deviation"

    def test_brief_default_is_observable_via_flag(self):
        """The spec-compatible 'brief' default also sets the flag."""
        mode, defaulted = resolve_pre_v3_buying_mode(None, "2.0.0", "anything")
        assert mode == "brief"
        assert defaulted is True

    def test_v3_client_supplied_mode_is_never_flagged(self):
        """A v3 client that supplies buying_mode is never flagged as defaulted."""
        for supplied in ("brief", "wholesale", "refine"):
            mode, defaulted = resolve_pre_v3_buying_mode(supplied, "3.0.0", None)
            assert mode == supplied
            assert defaulted is False
