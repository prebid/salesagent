"""Structural guard: every gradable (storyboard, step) has a wireability triage decision.

``docs/test-obligations/storyboard-wireability.yaml`` is a second hand-curated input,
same shape as ``storyboard-issue-map.yaml`` -- but keyed one grain finer, per
``(storyboard, step)`` rather than per storyboard, because "can this be wired
end-to-end" is a per-step judgement (one storyboard can mix a wireable step with a
webhook-gated one). Before this guard, a missing entry became `e2e_wireable:
"unassessed"` in ``storyboard_check_index.build()`` (a real, silent fallback --
``scripts/audit/storyboard_check_index.py::_wire_fields``) and ``render()`` then
DROPPED that row from the "End-to-end wireability" table entirely
(``if r["e2e_wireable"] in ("wireable", "unassessed"): continue``). An untriaged step
vanished instead of showing up as a gap -- the same rot the issue-map guard exists to
prevent for storyboards.

Two fixes ship alongside this guard:

* ``render()`` no longer skips ``unassessed`` rows -- only genuinely-good-news
  ``wireable`` ones. An untriaged step is now a visible row in the rendered table
  (acceptance criterion 2).
* ``docs/test-obligations/storyboard-wireability.yaml`` gains an ``untriaged:`` list,
  the escape hatch this guard actually enforces against: a graded, non-controller-gated
  step must either carry a verdict in ``steps:`` or be named in ``untriaged:`` --
  stating "nobody has judged this yet" out loud, the same discipline
  ``storyboard-issue-map.yaml``'s ``coverage: none`` already requires for storyboards.

Offline reach: full per-step enumeration needs the actual storyboard body text (which
step ids exist, and which require ``comply_test_controller`` at the STEP level) --
unavailable from the vendored ``index.json`` alone, whose ``phases`` field is a flat,
undifferentiated superset of every ``- id:`` in the file at any depth (phases AND
steps both), by construction of ``_refresh.py``. So, like
``test_architecture_storyboard_controller_divergence.py`` and
``test_architecture_storyboard_check_inventory.py`` before it: offline tests prove the
guard LOGIC (well-formedness of the real committed file, stale-reference detection
against the vendored superset, and the missing-entry arithmetic itself, via a
meta-test) unconditionally in CI; the one test that needs the real per-step
enumeration (``test_every_on_path_non_controller_step_is_assessed_or_untriaged``) is
gated on a local ``~/projects/adcp`` clone, same contract as its siblings.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INDEX = REPO_ROOT / "tests" / "fixtures" / "adcp_storyboards_pinned" / "index.json"
WIREABILITY = REPO_ROOT / "docs" / "test-obligations" / "storyboard-wireability.yaml"

sys.path.insert(0, str(REPO_ROOT))

from scripts.audit import ledger, storyboard_check_index  # noqa: E402
from tests.unit._storyboard_guard_env import (  # noqa: E402
    ADCP_HOME,
    requires_pinned_bundle,
)

VALID_VERDICTS = {"wireable", "conditional", "not_wireable"}


def _steps() -> dict[str, dict]:
    """Real, committed (storyboard::step) -> verdict entry."""
    return ledger.load_wireability(REPO_ROOT)


def _untriaged() -> set[str]:
    return ledger.load_wireability_untriaged(REPO_ROOT)


def _known_storyboard_ids(index: dict) -> set[str]:
    return set(index["storyboards"])


def _known_ids_for(index: dict, rel: str) -> list[str]:
    """The undifferentiated (phase-or-step) id superset ``_refresh.py`` vendors.

    A cited step_id absent from this list cannot be a real step -- it isn't
    anywhere in the file, phase or step, at any depth.
    """
    entry = index["storyboards"].get(rel)
    return list(entry.get("phases", [])) if entry else []


# --- Well-formedness: the real, committed file (offline, no clone needed) ---


def test_wireability_entries_are_well_formed() -> None:
    """Every entry declares a verdict from the vocabulary; a conditional one says why."""
    problems: list[str] = []
    for key, entry in sorted(_steps().items()):
        verdict = entry.get("verdict")
        if verdict not in VALID_VERDICTS:
            problems.append(f"{key}: verdict {verdict!r} not one of {sorted(VALID_VERDICTS)}")
            continue
        if verdict == "conditional" and not entry.get("requires") and not entry.get("blocker"):
            problems.append(f"{key}: verdict 'conditional' needs a `requires` list or a `blocker` explaining why")
    assert problems == [], "Malformed storyboard-wireability.yaml entries:\n" + "\n".join(f"  {p}" for p in problems)


def test_wireability_has_no_entries_for_unknown_storyboards() -> None:
    """An entry's storyboard half must name a file that exists at the pin."""
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    known = _known_storyboard_ids(index)
    unknown = sorted({key for key in _steps() if key.partition("::")[0] not in known})
    assert unknown == [], (
        "storyboard-wireability.yaml entries naming storyboards that do not exist at the pin. "
        "These storyboards do not exist — remove the entries, or fix the path:\n" + "\n".join(f"  {s}" for s in unknown)
    )


def test_wireability_has_no_entries_for_steps_absent_from_the_pinned_tree() -> None:
    """A cited step must appear somewhere in its storyboard, not just the storyboard's name."""
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    stale: list[str] = []
    for key in sorted(_steps()):
        rel, _, step_id = key.partition("::")
        if rel not in index["storyboards"]:
            continue  # caught by test_wireability_has_no_entries_for_unknown_storyboards
        if step_id not in _known_ids_for(index, rel):
            stale.append(key)
    assert stale == [], (
        "storyboard-wireability.yaml entries whose step_id is not a real step in the pinned "
        "storyboard — is not a real step id at the pin. Remove or fix these entries:\n"
        + "\n".join(f"  {s}" for s in stale)
    )


def test_untriaged_entries_do_not_duplicate_an_assessed_step() -> None:
    """A step is either assessed or explicitly deferred, never both.

    Listing a step in both places is contradictory -- it claims a verdict is
    both known and not yet judged -- and would silently favor whichever
    consumer reads first.
    """
    overlap = sorted(_untriaged() & set(_steps()))
    assert overlap == [], (
        "Steps listed in both `untriaged` and `steps` in storyboard-wireability.yaml -- pick one:\n"
        + "\n".join(f"  {s}" for s in overlap)
    )


def test_untriaged_entries_name_known_non_stale_steps() -> None:
    """The deferral list is held to the same real-tree grounding as assessed entries."""
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    bad: list[str] = []
    for key in sorted(_untriaged()):
        rel, _, step_id = key.partition("::")
        if rel not in index["storyboards"]:
            bad.append(f"{key} (unknown storyboard)")
        elif step_id not in _known_ids_for(index, rel):
            bad.append(f"{key} (not a real step id at the pin)")
    assert bad == [], "storyboard-wireability.yaml `untriaged` entries naming non-existent storyboards/steps:\n" + (
        "\n".join(f"  {b}" for b in bad)
    )


@pytest.mark.parametrize(
    "entry,expected",
    [
        ({"verdict": "sometimes"}, "not one of"),
        ({"verdict": "conditional"}, "needs a `requires`"),
        ({"verdict": "conditional", "blocker": ""}, "needs a `requires`"),
    ],
)
def test_wellformedness_check_catches_each_defect(monkeypatch, entry: dict, expected: str) -> None:
    """Meta-test: a guard that cannot fail is not a guard."""
    monkeypatch.setattr(f"{__name__}._steps", lambda: {"universal/security.yaml::probe": entry})
    with pytest.raises(AssertionError, match=expected):
        test_wireability_entries_are_well_formed()


def test_stale_reference_checks_actually_catch_the_defects_they_claim(monkeypatch) -> None:
    """Meta-test for the two real-tree grounding checks."""
    monkeypatch.setattr(f"{__name__}._steps", lambda: {"nonexistent/path.yaml::whatever": {"verdict": "wireable"}})
    with pytest.raises(AssertionError, match="do not exist at the pin"):
        test_wireability_has_no_entries_for_unknown_storyboards()

    monkeypatch.setattr(
        f"{__name__}._steps",
        lambda: {"protocols/media-buy/index.yaml::not_a_real_step_id_at_all": {"verdict": "wireable"}},
    )
    with pytest.raises(AssertionError, match="not a real step id at the pin"):
        test_wireability_has_no_entries_for_steps_absent_from_the_pinned_tree()


def test_duplicate_and_untriaged_grounding_checks_actually_catch_the_defects_they_claim(monkeypatch) -> None:
    """Meta-test for the untriaged-list checks."""
    monkeypatch.setattr(f"{__name__}._steps", lambda: {"universal/security.yaml::probe": {"verdict": "wireable"}})
    monkeypatch.setattr(f"{__name__}._untriaged", lambda: {"universal/security.yaml::probe"})
    with pytest.raises(AssertionError, match="pick one"):
        test_untriaged_entries_do_not_duplicate_an_assessed_step()

    monkeypatch.setattr(f"{__name__}._untriaged", lambda: {"nonexistent/path.yaml::whatever"})
    with pytest.raises(AssertionError, match="unknown storyboard"):
        test_untriaged_entries_name_known_non_stale_steps()


# --- The missing-entry arithmetic: every graded, non-controller step is assessed OR untriaged ---


def _missing(required: set[str], assessed: set[str], untriaged: set[str]) -> set[str]:
    """A required (storyboard, step) satisfies the guard by being assessed or deferred."""
    return required - assessed - untriaged


def test_missing_flags_a_required_step_that_is_neither_assessed_nor_untriaged() -> None:
    """The regression this guard exists to catch, stated directly and offline."""
    required = {"a.yaml::step_one", "a.yaml::step_two"}
    assessed = {"a.yaml::step_one"}
    assert _missing(required, assessed, untriaged=set()) == {"a.yaml::step_two"}


def test_missing_treats_an_untriaged_listing_as_satisfying_the_guard() -> None:
    """Explicitly deferring a step is a real answer, not a hole -- it must not fail the guard."""
    required = {"a.yaml::step_one"}
    assert _missing(required, assessed=set(), untriaged={"a.yaml::step_one"}) == set()


def test_missing_is_empty_when_every_required_step_is_assessed() -> None:
    required = {"a.yaml::step_one"}
    assert _missing(required, assessed=required, untriaged=set()) == set()


@requires_pinned_bundle
def test_every_on_path_non_controller_step_is_assessed_or_untriaged() -> None:
    """The real, exhaustive proof: nothing this repo is graded on may be silently missing.

    Builds the real per-check index (needs the live pinned tree for step-level
    text) and checks every non-controller-gated record's (storyboard, step)
    identity against the committed wireability file.
    """
    result = storyboard_check_index.build(REPO_ROOT, ADCP_HOME)
    # Scoped to the GRADED surface. The index now also carries `gate="GATED"`
    # records (a storyboard whose `requires_capability` the offline classifier
    # cannot evaluate) so the published totals stop reading as a floor — but we
    # do not grade those checks, so demanding an e2e wireability verdict for
    # them would be triage debt for work nobody is measured on.
    required = {
        f"{r['storyboard']}::{r['step_id']}"
        for r in result["records"]
        if not r["requires_controller"] and r["gate"] == "ON-PATH"
    }
    assert required, "no on-path, non-controller-gated records resolved — the fixture, not the fix, is broken"

    missing = sorted(_missing(required, set(_steps()), _untriaged()))
    assert missing == [], (
        f"On-path, gradable (storyboard, step)s missing a triage decision in {ledger.WIREABILITY}. "
        "Each needs a verdict (wireable/conditional/not_wireable) under `steps:`, or must be named "
        "in `untriaged:` stating plainly that nobody has judged it yet:\n" + "\n".join(f"  {s}" for s in missing)
    )


# --- render(): an unassessed row is a visible gap, not a silent drop ---


def _min_record(**overrides) -> dict:
    base = {
        "storyboard": "universal/example.yaml",
        "storyboard_id": "example",
        "phase_id": None,
        "step_id": "a_step",
        "check_type": "response_schema",
        "ordinal": 0,
        "citation": "repo=adcp ref=v3.1.1 path=universal/example.yaml",
        "tier": "universal",
        "required_tools": [],
        "requires_controller": False,
        "measured_failing_protocols": [],
        "measured": storyboard_check_index.MEASURED_NOT_MEASURED,
        "scenarios": [],
        "scenario_grain": "storyboard",
        "scenario_binding_buckets": {},
        "claimed_by_scenario": False,
        "scenario_liveness": {},
        "graded_by_live_scenario": False,
        "graduation_candidate": False,
        "e2e_wireable": "unassessed",
        "e2e_axes": {},
        "e2e_requires": [],
        "e2e_blocker": "",
        "e2e_source": "",
        "issues": [],
        "issue_coverage": "untriaged",
        "issue_note": "",
    }
    base.update(overrides)
    return base


def _min_totals(records: list[dict]) -> dict:
    graded = [r for r in records if r.get("gate", "ON-PATH") == "ON-PATH"]
    return {
        "checks": len(records),
        "storyboards": len({r["storyboard"] for r in records}),
        "graded_checks": len(graded),
        "graded_storyboards": len({r["storyboard"] for r in graded}),
        "gated_checks": len(records) - len(graded),
        "gated_storyboards": len({r["storyboard"] for r in records if r.get("gate") == "GATED"}),
        "with_scenario": sum(1 for r in records if r["scenarios"]),
        "with_live_scenario": sum(1 for r in records if r["graded_by_live_scenario"]),
        "graduation_candidates": sum(1 for r in records if r["graduation_candidate"]),
        "with_issue": sum(1 for r in records if r["issues"]),
        "neither": sum(1 for r in records if not r["scenarios"] and not r["issues"]),
        "failing": sum(1 for r in records if r["measured_failing_protocols"]),
        "not_failing": sum(1 for r in records if r["measured"] == storyboard_check_index.MEASURED_NOT_FAILING),
        "not_measured": sum(1 for r in records if r["measured"] == storyboard_check_index.MEASURED_NOT_MEASURED),
        "ungradable": sum(1 for r in records if r["requires_controller"]),
        "wireable": sum(1 for r in records if r["e2e_wireable"] == "wireable"),
        "conditional": sum(1 for r in records if r["e2e_wireable"] == "conditional"),
        "not_wireable": sum(1 for r in records if r["e2e_wireable"] == "not_wireable"),
        "unassessed": sum(1 for r in records if r["e2e_wireable"] == "unassessed"),
    }


def _wireability_section(rendered: str) -> str:
    return rendered.split("## 5. End-to-end wireability")[1].split("## 6.")[0]


def test_render_shows_an_unassessed_step_as_a_visible_gap() -> None:
    """The deliverable: an untriaged step is a row in the table, not an absence."""
    records = [_min_record(step_id="untriaged_step", e2e_wireable="unassessed")]
    result = {
        "pinned_version": "v3.1.1",
        "liveness_measured": True,
        "totals": _min_totals(records),
        "records": records,
    }

    section = _wireability_section(storyboard_check_index.render(result))

    assert "untriaged_step" in section
    assert "unassessed" in section


def test_render_still_omits_plainly_wireable_steps() -> None:
    """Negative: a fully-wireable check is not a gap and stays out of this table."""
    records = [_min_record(step_id="clean_step", e2e_wireable="wireable")]
    result = {
        "pinned_version": "v3.1.1",
        "liveness_measured": True,
        "totals": _min_totals(records),
        "records": records,
    }

    section = _wireability_section(storyboard_check_index.render(result))

    assert "clean_step" not in section


def test_render_still_shows_conditional_and_not_wireable_steps() -> None:
    """Negative-of-the-negative: the existing conditional/not_wireable rows must survive the fix."""
    records = [
        _min_record(step_id="cond_step", e2e_wireable="conditional", e2e_requires=["webhook_receiver"]),
        _min_record(step_id="blocked_step", e2e_wireable="not_wireable", e2e_blocker="upstream only"),
    ]
    result = {
        "pinned_version": "v3.1.1",
        "liveness_measured": True,
        "totals": _min_totals(records),
        "records": records,
    }

    section = _wireability_section(storyboard_check_index.render(result))

    assert "cond_step" in section
    assert "blocked_step" in section
