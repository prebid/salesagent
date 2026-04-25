"""Guard: ci.yml renders the 11 frozen required-check names per D17.

The check names are a contract with branch protection. Renaming any of them
is an atomic flip handled by PR 3 Phase B; the names cannot drift in code
without coordinating the branch-protection update.

Per D26: GitHub renders status checks as `<workflow.name> / <job.name>`. The
workflow header is `name: CI`; jobs use BARE `name: 'Quality Gate'` (NOT
`name: 'CI / Quality Gate'` — that would render as `CI / CI / Quality Gate`).
This guard validates the bare job-name convention plus the workflow `name: CI`
prefix, which together produce the rendered names that branch protection sees.
"""

import re

from tests.unit._architecture_helpers import repo_root

# D17 — bare job names (the rendered names are workflow `CI` + ` / ` + bare).
_BARE_JOB_NAMES: tuple[str, ...] = (
    "Quality Gate",
    "Type Check",
    "Schema Contract",
    "Unit Tests",
    "Integration Tests",
    "E2E Tests",
    "Admin UI Tests",
    "BDD Tests",
    "Migration Roundtrip",
    "Coverage",
    "Summary",
)


def test_ci_yml_workflow_name_is_CI():
    text = (repo_root() / ".github" / "workflows" / "ci.yml").read_text()
    assert re.search(r"^name:\s+CI\s*$", text, flags=re.MULTILINE), (
        "ci.yml workflow header must be `name: CI` so GitHub auto-prefixes job "
        "names to produce the D17 frozen check names. Per D26."
    )


def test_ci_yml_declares_all_frozen_job_names():
    text = (repo_root() / ".github" / "workflows" / "ci.yml").read_text()
    missing: list[str] = []
    for bare in _BARE_JOB_NAMES:
        # Job names live UNDER the jobs: block, indented. Match:
        #   name: 'Foo'   or   name: "Foo"   or   name: Foo
        if not re.search(
            rf"^\s+name:\s+(?:['\"]?){re.escape(bare)}(?:['\"]?)\s*$",
            text,
            flags=re.MULTILINE,
        ):
            missing.append(bare)
    assert not missing, (
        "Frozen job name(s) missing from ci.yml — branch protection drift "
        "risk. Add the missing job(s) or coordinate a Phase-B-style flip:\n" + "\n".join(f"  - {n}" for n in missing)
    )


def test_ci_yml_jobs_do_not_include_CI_prefix():
    """Per D26: job names must NOT include the `CI / ` prefix.

    GitHub auto-prefixes the workflow `name:` onto the job `name:` separated
    by ` / `. Including `CI /` in the job name produces `CI / CI / Quality
    Gate`, which Phase B's atomic branch-protection PATCH does not match.
    """
    text = (repo_root() / ".github" / "workflows" / "ci.yml").read_text()
    bad = re.findall(r"^\s+name:\s+['\"]CI / .+['\"]\s*$", text, flags=re.MULTILINE)
    assert not bad, (
        "ci.yml jobs must NOT use 'CI / X' name format (D26). Use bare 'X' — "
        "GitHub auto-prefixes the workflow name. Offending lines:\n  " + "\n  ".join(bad)
    )


def test_ci_yml_test_suite_jobs_use_composite_action_not_reusable_workflow():
    """Per Decision-4 (P0 sweep, D26 corollary): test-suite jobs MUST use a composite
    action (`./.github/actions/_pytest`), NOT a reusable workflow (`./.github/workflows/_pytest.yml`).

    Reusable workflow nesting renders as `CI / Unit Tests / pytest` (3 segments).
    Branch protection's required-checks list uses 2-segment names (`CI / Unit Tests`).
    A reusable-workflow `_pytest.yml` would silently 422 the Phase B PATCH.

    Composite actions don't add path segments — the calling job's name is the rendered name.
    """
    text = (repo_root() / ".github" / "workflows" / "ci.yml").read_text()
    # Detect any `uses: ./.github/workflows/_*.yml` reference inside ci.yml jobs
    bad = re.findall(
        r"^\s+uses:\s+['\"]?\.\/?\.github\/workflows\/[^'\"\s]+\.yml[^'\"\s]*['\"]?\s*$",
        text,
        flags=re.MULTILINE,
    )
    assert not bad, (
        "ci.yml jobs use a reusable workflow (./.github/workflows/...) for test-suite "
        "execution. Per Decision-4 (P0 sweep), test-suite jobs MUST use composite actions "
        "(./.github/actions/...) to avoid 3-segment rendered names that would 422 the "
        "Phase B branch-protection PATCH. Offending lines:\n  " + "\n  ".join(bad)
    )
