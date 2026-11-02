"""The dormancy baseline's own check — it decides whether a scenario may grade nothing.

A scenario whose step has no binding executes nothing and is reported as an xfail, so
it is invisible in a green run. ``tests/bdd/dormant_scenarios.txt`` is what stops that
set growing silently (#1929), which makes the parsing and key-normalization
below load-bearing: if the key does not match, a recorded gap reads as NEW dormancy and
reddens a build, or worse, a genuinely new gap matches something and passes.
"""

from __future__ import annotations

import re

from tests.bdd.conftest import (
    DORMANT_SCENARIOS_PATH,
    _dormant_baseline,
    _normalize_step_text,
)


class TestStepTextNormalization:
    """One missing binding must produce ONE key, whatever an Examples row substituted."""

    def test_quoted_literals_collapse(self) -> None:
        a = _normalize_step_text('delivery data for two media buys owned by "buyer-001"')
        b = _normalize_step_text('delivery data for two media buys owned by "buyer-002"')
        assert a == b, "two Examples rows of one scenario must share a key"
        assert a == 'delivery data for two media buys owned by "<>"'

    def test_bare_numbers_collapse(self) -> None:
        a = _normalize_step_text("owns media buy with 10 history entries")
        b = _normalize_step_text("owns media buy with 12 history entries")
        assert a == b == "owns media buy with <n> history entries"

    def test_inline_objects_collapse(self) -> None:
        assert _normalize_step_text("status canceled and cancellation {canceled_at:'2026-05-01'}") == (
            "status canceled and cancellation {<>}"
        )

    def test_distinct_steps_stay_distinct(self) -> None:
        """Normalization must not merge two genuinely different gaps."""
        assert _normalize_step_text('a forecast point for media buy "mb-001"') != _normalize_step_text(
            'a package for media buy "mb-001" with committed_metrics present'
        )


class TestBaselineFile:
    def test_parses_and_is_populated(self) -> None:
        entries = _dormant_baseline()
        assert entries, "baseline parsed empty — every dormant scenario would redden the build"
        assert all(isinstance(t, str) and isinstance(s, str) for t, s in entries)

    def test_every_entry_is_tag_keyed(self) -> None:
        """Positional keys rot: salesagent-d34gf rewrites 613 scenarios and would invalidate them."""
        for tag, step in _dormant_baseline():
            assert re.match(r"^T-[A-Z0-9-]", tag), f"entry not keyed on a scenario tag: {tag!r}"
            assert step.split(" ", 1)[0] in {"given", "when", "then"}, f"no step keyword: {step!r}"
            assert ".feature" not in step and "Line " not in step, f"positional data leaked into key: {step!r}"

    def test_no_duplicate_entries(self) -> None:
        """A duplicate would let the list grow while appearing to hold its size."""
        lines = [
            ln.strip()
            for ln in DORMANT_SCENARIOS_PATH.read_text().splitlines()
            if " :: " in ln and not ln.strip().startswith("#")
        ]
        assert len(lines) == len(set(lines)), "duplicate baseline entries"

    def test_normalized_keys_are_already_normal(self) -> None:
        """Round-trip: a committed key must equal its own normalization.

        Catches a hand-edited entry that would silently never match, which is how an
        allowlist quietly stops allowing and starts reddening unrelated builds.
        """
        for _tag, step in _dormant_baseline():
            keyword, text = step.split(" ", 1)
            assert step == f"{keyword} {_normalize_step_text(text)}", f"entry is not in normalized form: {step!r}"
