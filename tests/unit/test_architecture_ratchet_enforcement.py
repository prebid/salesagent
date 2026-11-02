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

from tests.unit._architecture_helpers import repo_root
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
