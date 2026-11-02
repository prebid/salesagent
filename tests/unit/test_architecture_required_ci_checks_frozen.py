"""Guard: CI required check names are frozen.

Branch protection matches rendered check names exactly. This guard prevents
accidental renames that would silently break protection coverage.
"""

from __future__ import annotations

import pytest

from scripts.ci.workflow_helpers import load_ci_workflow, rendered_ci_check_names

REQUIRED_RENDERED_CHECKS = {
    "CI / Quality Gate",
    "CI / Type Check",
    "CI / Schema Contract",
    "CI / Security Audit",
    "CI / Quickstart",
    "CI / Smoke Tests",
    "CI / Unit Tests",
    "CI / Integration (creative)",
    "CI / Integration (product)",
    "CI / Integration (media-buy)",
    "CI / Integration (infra)",
    "CI / Integration (other)",
    "CI / E2E Tests",
    "CI / Admin UI Tests",
    # Four shards, not two. The BDD suite outgrew a two-way split as this branch
    # converted dormant scenarios into executing ones, and both shards were being
    # KILLED at the 25-minute cap rather than failing -- so nothing reported a
    # failure and nothing cascaded. Sharding now prices each file by measured
    # seconds instead of Gherkin scenario count, which called two shards even at
    # 586 against 569 while they actually held 120 and 91 minutes.
    #
    # These names are matched exactly by branch protection, so widening the split
    # means an out-of-band GitHub settings edit: the two 1/2 checks must be
    # replaced by these four, or protection silently stops covering BDD.
    "CI / BDD Tests (Shard 1/4)",
    "CI / BDD Tests (Shard 2/4)",
    "CI / BDD Tests (Shard 3/4)",
    "CI / BDD Tests (Shard 4/4)",
    "CI / BDD Tests",
    # In-network bdd (e2e_rest transport) — grades the known-failures ledger
    # (PR #1430 review). Mirror this into branch protection's required checks.
    "CI / BDD In-Network (e2e_rest)",
    # Grades tests/storyboard/ against a live in-network stack. The
    # known-failures ledger IS seeded from a real run, so what still gates
    # promotion to a required check is a branch-protection change, which is an
    # out-of-band GitHub settings edit rather than anything in this repo. Until
    # that happens it stays out of the Summary job's `needs` — but it is
    # rendered unconditionally by rendered_ci_check_names(), so it must be
    # listed here regardless of blocking status.
    "CI / Storyboard Conformance",
    "CI / Migration Roundtrip",
    "CI / Coverage",
    "CI / Summary",
}


@pytest.mark.arch_guard
def test_ci_workflow_name_is_frozen() -> None:
    workflow = load_ci_workflow()
    assert workflow["name"] == "CI", "Workflow name must remain 'CI' for stable rendered check names."


@pytest.mark.arch_guard
def test_required_check_names_are_frozen() -> None:
    rendered = rendered_ci_check_names()

    assert rendered == REQUIRED_RENDERED_CHECKS, (
        "Required rendered CI check names drifted.\n"
        f"Expected: {sorted(REQUIRED_RENDERED_CHECKS)}\n"
        f"Actual:   {sorted(rendered)}"
    )
