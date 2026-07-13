"""Compiler-level regression guard: hand-edited BDD scenarios survive rederive.

Salesagent-local scenarios (not produced by adcp-req) are kept across
``compile_bdd.py --merge`` only if they carry a ``@hand-edited`` marker AND the
compiler actually parses that marker onto the scenario. The compiler collects
tags CONTIGUOUSLY: a comment line placed BETWEEN the tag line and ``Scenario:``
makes the parser discard the collected tags (compile_bdd.py: the ``if not m:
idx += 1; continue`` after a tag block that isn't immediately followed by
``Scenario:``), yielding ``tags=[], id=None`` -> ``LEGACY-DELETE``. Both the
scenario and its ``bdd-traceability.yaml`` row are then dropped on the next
rederive.

The obligation-sync guard parses @T-* tags with different logic and does NOT
catch this. This test pins the compiler's own parse/classify contract:

1. Every ``@hand-edited`` scenario in the shipped feature files parses WITH its
   tag and classifies ``LEGACY-PRESERVE``.
2. The known-bad layout (comment between tag and ``Scenario:``) classifies
   ``LEGACY-DELETE`` — documenting exactly why the marker must sit adjacent.

All functions used are pure and importable without the external adcp-req repo.
"""

from __future__ import annotations

from pathlib import Path

from scripts.compile_bdd import (
    _has_hand_edited_marker,
    _scenario_id,
    classify_scenario_pair,
    parse_feature_file,
)

_FEATURES_DIR = Path(__file__).resolve().parents[1] / "bdd" / "features"


def _hand_edited_tag_lines(text: str) -> list[str]:
    """Raw feature lines that are a tag line carrying @hand-edited.

    Restricts to lines whose (pre-comment) content starts with '@' so prose
    mentions of '@hand-edited' inside comments are not counted.
    """
    lines = []
    for ln in text.splitlines():
        code = ln.split("#", 1)[0].strip()
        if code.startswith("@") and "@hand-edited" in code:
            lines.append(ln)
    return lines


class TestHandEditedScenariosSurviveRederive:
    """Every shipped @hand-edited scenario must parse with its tag + id and be preserved."""

    def test_all_hand_edited_scenarios_classify_legacy_preserve(self):
        checked = 0
        for feature_file in _FEATURES_DIR.glob("*.feature"):
            text = feature_file.read_text()
            raw_marker_count = len(_hand_edited_tag_lines(text))
            if raw_marker_count == 0:
                continue

            feature = parse_feature_file(text)
            # Count by PARSED TAG — the exact thing the contiguity bug drops.
            tagged = [s for s in feature.scenarios if "@hand-edited" in s.tags]

            assert len(tagged) == raw_marker_count, (
                f"{feature_file.name}: {raw_marker_count} raw @hand-edited tag line(s) but "
                f"{len(tagged)} parsed onto scenarios — a marker was orphaned (a comment "
                f"between the tag line and 'Scenario:' discards the tags -> LEGACY-DELETE)."
            )

            for scenario in tagged:
                sid = _scenario_id(scenario)
                assert sid is not None, f"{feature_file.name}: a @hand-edited scenario parsed without a @T-* id"
                bucket = classify_scenario_pair(scenario, None)
                assert bucket == "LEGACY-PRESERVE", (
                    f"{feature_file.name}: hand-edited scenario {sid} classifies {bucket}, "
                    f"not LEGACY-PRESERVE — it (and its traceability row) would be dropped on rederive."
                )
                checked += 1

        assert checked > 0, "expected at least one shipped @hand-edited scenario to guard"


class TestHandEditedMarkerPlacementContract:
    """Pin the parse contract: comment ABOVE the tag preserves; comment BETWEEN drops."""

    _GOOD = """Feature: X

  # HAND-EDITED: salesagent-local scenario — comment ABOVE the tag line.
  @T-UC-999-good @alternative @hand-edited
  Scenario: good placement
    Given a step
"""

    _BAD = """Feature: X

  @T-UC-999-bad @alternative @hand-edited
  # HAND-EDITED: comment BETWEEN the tag line and Scenario — breaks contiguity.
  Scenario: bad placement
    Given a step
"""

    def test_comment_above_tag_line_is_preserved(self):
        scenario = parse_feature_file(self._GOOD).scenarios[0]
        assert "@hand-edited" in scenario.tags
        assert _scenario_id(scenario) == "T-UC-999-good"
        assert _has_hand_edited_marker(scenario) is True
        assert classify_scenario_pair(scenario, None) == "LEGACY-PRESERVE"

    def test_comment_between_tag_and_scenario_is_dropped(self):
        scenario = parse_feature_file(self._BAD).scenarios[0]
        # The intervening comment causes the tag block to be discarded.
        assert "@hand-edited" not in scenario.tags
        assert _scenario_id(scenario) is None
        assert classify_scenario_pair(scenario, None) == "LEGACY-DELETE"
