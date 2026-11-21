"""Guard: every ratcheting baseline is enforced by something that actually RUNS.

A ratchet nobody executes is not a ratchet. `.pre-commit-config.yaml` files the
Layer-2 ratchet counters under ``stages: [pre-push]`` -- deliberately, because
the commit stage is capped at 12 fast hooks (D27,
``test_architecture_pre_commit_hook_count.py``) and heavier checks are meant to
run at push time with "CI is authoritative" as the backstop. Both halves of that
are inert here:

- this repo's documented workflow (``.claude/rules/workflows/session-completion.md``)
  is ephemeral branches merged to main LOCALLY, with ``git push`` never run, so
  the pre-push stage never fires;
- ``.github/workflows/ci.yml`` triggers only on ``push``/``pull_request`` to
  ``main``/``develop``, and that workflow produces neither, so the backstop does
  not fire either -- and when the local merge is eventually pushed, CI grades a
  main that has ALREADY absorbed the drift.

What was left was ``make quality``, which nothing requires. The counts duly
drifted: two ``# type: ignore`` additions landed silently (salesagent-aemue.13),
and ``mypy --check-untyped-defs`` was measured 15 above its committed baseline on
origin/main itself.

So enforcement moves to the one place that runs in every context an executor,
the full gate, and CI all share -- the unit suite -- and this module both IS that
enforcement (it runs the counters) and pins the rule that keeps it that way:
a ratchet may not be filed under an enforcement point that does not execute.
"""

from __future__ import annotations

import pytest
import yaml

from tests.unit import _architecture_helpers  # noqa: F401  (kept for repo_root below)
from tests.unit._architecture_helpers import repo_root
from tests.utils import ratchet_counters
from tests.utils.derived_population import assert_derived_population
from tests.utils.ratchet_counters import (
    ALL_RATCHETS,
    FAST_RATCHETS,
    RatchetCounter,
    assert_not_over_baseline,
    assert_not_slack,
)

#: ``enforced_by`` values in ``.pre-commit-coverage-map.yml`` that name a
#: mechanism which actually executes under the documented workflow. ``pre-push``
#: and ``ci`` are absent on purpose — see the module docstring. ``gate`` is the
#: full-suite run (``run_all_tests.sh`` / ``cassini run``), which is where the
#: two ratchets too slow for this suite are graded.
ALWAYS_RUN_ENFORCEMENT = frozenset({"guard", "guard-existing", "gate"})


@pytest.mark.arch_guard
@pytest.mark.parametrize("ratchet", FAST_RATCHETS, ids=lambda r: r.hook_id)
def test_fast_ratchet_is_at_or_below_its_committed_baseline(ratchet: RatchetCounter) -> None:
    """The ratchet, run where it cannot be silently skipped.

    This is the enforcement, not a description of one: the counter runs against
    the working tree on every unit-suite invocation, so a new violation fails
    here even though the pre-push hook that nominally owns it never fires.
    """
    assert_not_over_baseline(ratchet)


@pytest.mark.arch_guard
@pytest.mark.parametrize("ratchet", FAST_RATCHETS, ids=lambda r: r.hook_id)
def test_fast_ratchet_baseline_is_not_slack(ratchet: RatchetCounter) -> None:
    assert_not_slack(ratchet)


@pytest.mark.arch_guard
def test_the_ratchet_registry_is_not_empty() -> None:
    """The registry every other test in this module is driven by.

    Both ratchet tests are parametrized over ``FAST_RATCHETS``, so an empty
    registry deletes them rather than failing them, and reports one skip while the
    failure count goes down (salesagent-8qela.176). The enforcement check below is
    worse: it is NOT parametrized, so an empty ``ALL_RATCHETS`` makes ``missing``
    and ``unenforced`` both empty and the test passes green over nothing — the
    guard that exists to stop a ratchet going unenforced, itself unenforced.

    The ratchet COUNTERS do not need this: ``assert_not_slack`` builds its
    comparison from ``committed.items()``, the baseline FILE, so a counter whose
    scan returns an empty mapping still yields ``0 < base`` for every committed
    key and fails loudly. Driving the comparison off the side that cannot silently
    empty is what makes that safe, and it is the pattern to copy.
    """
    assert_derived_population(
        FAST_RATCHETS, name="FAST_RATCHETS", derived_from="tests/utils/ratchet_counters.py", minimum=4
    )
    assert_derived_population(
        ALL_RATCHETS, name="ALL_RATCHETS", derived_from="FAST_RATCHETS + SLOW_RATCHETS", minimum=6
    )


def test_every_ratchet_names_an_enforcement_that_executes() -> None:
    """A ratchet filed only under pre-push/CI is enforced nowhere.

    This is the rule that makes the gap unrepresentable rather than merely fixed
    for today's hooks: the next ratchet added under ``stages: [pre-push]`` fails
    here until it names a mechanism that runs.
    """
    coverage_map = yaml.safe_load((repo_root() / ".pre-commit-coverage-map.yml").read_text(encoding="utf-8"))
    missing = sorted(r.hook_id for r in ALL_RATCHETS if r.hook_id not in coverage_map)
    assert missing == [], (
        f"These ratchets have no .pre-commit-coverage-map.yml entry at all: {missing}. "
        "An unmapped ratchet is the same gap one step earlier — nothing even claims to enforce it."
    )

    unenforced = sorted(
        r.hook_id
        for r in ALL_RATCHETS
        if not (set(str(coverage_map[r.hook_id]["enforced_by"]).split(" + ")) & ALWAYS_RUN_ENFORCEMENT)
    )
    assert unenforced == [], (
        f"These ratchets name no enforcement that executes: {unenforced}. "
        "pre-push never fires (git push is never run) and ci.yml triggers only "
        "on push/PR to main|develop. File them under 'guard' (a test in the "
        "always-run unit suite) or 'gate' (the quality tox env)."
    )


@pytest.mark.arch_guard
def test_a_ratchet_counter_runs_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both assertions over one ratchet cost ONE measurement, not two.

    ``assert_not_over_baseline`` and ``assert_not_slack`` each need the same number, and
    each used to measure it for itself. For ``check-code-duplication`` one measurement is
    three pylint R0801 processes, so a gate ran SIX for three answers -- which is how the
    single-process ``quality`` env came to be the longest suite in every full run from
    2026-09-09 and to decide the gate's wall-clock on its own: 50.6m of a 62.4m run,
    48.9m of it this one counter measured twice (1752.3s + 1183.4s in
    innet_171026_2038/quality.json).

    Graded here rather than in ``tests/quality`` on purpose: the property is about the
    caching, not about pylint, so it is measured with a counter that costs nothing and it
    runs in the suite every executor runs.
    """
    monkeypatch.setattr(ratchet_counters, "_MEASURED", {})
    runs: list[int] = []

    def measure_once(_hook: object, _root: object) -> dict[str, int]:
        runs.append(1)
        return {"only": len(runs)}

    counter = RatchetCounter(
        "probe-counts-its-own-runs", "check_type_ignore_count", ".type-ignore-baseline", measure_once
    )

    first, second = counter.current(), counter.current()

    assert len(runs) == 1, f"the counter ran {len(runs)} times for two reads of the same number"
    assert first == second == {"only": 1}
