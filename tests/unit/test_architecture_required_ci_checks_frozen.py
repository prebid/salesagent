"""Guard: CI required check names are frozen.

Branch protection matches rendered check names exactly. This guard prevents
accidental renames that would silently break protection coverage.
"""

from __future__ import annotations

import pathlib

import yaml

WORKFLOW_PATH = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"

REQUIRED_RENDERED_CHECKS = {
    "CI / Quality Gate",
    "CI / Type Check",
    "CI / Schema Contract",
    "CI / Security Audit",
    "CI / Quickstart",
    "CI / Smoke Tests",
    "CI / Unit Tests",
    "CI / Integration Tests",
    "CI / E2E Tests",
    "CI / Admin UI Tests",
    "CI / BDD Tests",
    "CI / Migration Roundtrip",
    "CI / Coverage",
    "CI / Summary",
}


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_ci_workflow_name_is_frozen() -> None:
    workflow = _load_workflow()
    assert workflow["name"] == "CI", "Workflow name must remain 'CI' for stable rendered check names."


def test_required_check_names_are_frozen() -> None:
    workflow = _load_workflow()
    jobs = workflow["jobs"]
    rendered = {f"CI / {job['name']}" for job in jobs.values()}

    assert rendered == REQUIRED_RENDERED_CHECKS, (
        "Required rendered CI check names drifted.\n"
        f"Expected: {sorted(REQUIRED_RENDERED_CHECKS)}\n"
        f"Actual:   {sorted(rendered)}"
    )
