"""Guard: CI required check names are frozen.

Branch protection matches rendered check names exactly. This guard prevents
accidental renames that would silently break protection coverage.
"""

from __future__ import annotations

from tests.unit.workflow_helpers import load_ci_workflow, rendered_ci_check_names

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
    "CI / BDD Tests",
    "CI / Migration Roundtrip",
    "CI / Coverage",
    "CI / Summary",
}


def test_ci_workflow_name_is_frozen() -> None:
    workflow = load_ci_workflow()
    assert workflow["name"] == "CI", "Workflow name must remain 'CI' for stable rendered check names."


def test_required_check_names_are_frozen() -> None:
    rendered = rendered_ci_check_names()

    assert rendered == REQUIRED_RENDERED_CHECKS, (
        "Required rendered CI check names drifted.\n"
        f"Expected: {sorted(REQUIRED_RENDERED_CHECKS)}\n"
        f"Actual:   {sorted(rendered)}"
    )
