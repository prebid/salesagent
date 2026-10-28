"""Guard: every suite run_all_tests.sh exercises gates CI or is an owned exclusion.

Regression coverage for the post-PR-#1299 silent-breakage gap: PR #1299
merged with CI green, yet ``./run_all_tests.sh`` immediately showed 12 BDD +
32 E2E failures on main. Root cause: the BDD suite had **zero** CI coverage
(no job in ``.github/workflows/ci.yml``), and the aggregation/gating job
must propagate BDD/E2E failures so a red suite turns CI red.

This is a configuration-contract assertion: the workflow file is parsed as
YAML data and the job graph is inspected. It is NOT a source-code AST scan.

Documented Summary exclusions (``ui``, ``quality``, ``storyboard``) are
ratcheted via ``test_local_suites_map_or_explicit_exclusion``. Every
locally-run suite in ``run_all_tests.sh`` ALL_SUITES must map to a summary
gate or appear in ``UNGATED_LOCAL_SUITES`` as an ``UngatedSuite`` record.
CI-only gates (smoke, bdd-in-network) are listed separately.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import pytest

from scripts.ci.workflow_helpers import load_ci_workflow, load_yaml_mapping
from tests.unit._architecture_helpers import (
    all_suites_from_runner,
    assert_violations_match_allowlist,
    compose_service_environment,
    find_step_index,
    job_run_text,
    repo_root,
    steps_text,
)

# Local suite → summary.needs job. Derived from run_all_tests.sh ALL_SUITES;
# unmapped suites must be listed in UNGATED_LOCAL_SUITES with a documented reason.
_LOCAL_SUITE_TO_SUMMARY_GATE: dict[str, str] = {
    "unit": "unit-tests",
    "integration": "integration-tests",
    "bdd": "bdd-tests",
    "admin": "admin-ui-tests",
    "e2e": "e2e-tests",
}


class SuiteGateState(StrEnum):
    """How an ungated local suite relates to CI."""

    NO_AUTOMATED_RUN = "no_automated_run"
    RUNS_REPORT_ONLY = "runs_report_only"
    COVERED_BY_OTHER_JOB = "covered_by_other_job"


@dataclass(frozen=True)
class UngatedSuite:
    """One locally-run suite that does not gate Summary, and the open issue that owns it."""

    owner_issue: int
    state: SuiteGateState
    why: str
    ci_job: str | None = None


# The record is the registry. ``run_all_tests.sh`` holds one pointer per suite
# and does not repeat the rationale or the issue number.
UNGATED_LOCAL_SUITES: dict[str, UngatedSuite] = {
    "ui": UngatedSuite(
        owner_issue=2161,
        state=SuiteGateState.NO_AUTOMATED_RUN,
        why=(
            "tox -e ui needs the E2E compose stack and Playwright. No workflow job runs "
            "tests/ui. admin-ui-tests runs tests/admin against Postgres and is not a substitute."
        ),
    ),
    "quality": UngatedSuite(
        owner_issue=2470,
        state=SuiteGateState.COVERED_BY_OTHER_JOB,
        why="quality-gate runs make quality-ci. It does not run tox -e quality.",
        ci_job="quality-gate",
    ),
    "storyboard": UngatedSuite(
        owner_issue=2471,
        state=SuiteGateState.RUNS_REPORT_ONLY,
        why=(
            "storyboard-conformance runs ./run_all_tests.sh storyboard and is absent from "
            "summary.needs until the conformance ledger is re-verified."
        ),
        ci_job="storyboard-conformance",
    ),
}

# Expected suite → owner. Permuting UNGATED_LOCAL_SUITES without this map fails the test.
_UNGATED_OWNER_ISSUES = {"ui": 2161, "quality": 2470, "storyboard": 2471}

# CI-only gates that are not ALL_SUITES entries but must still floor Summary.
_EXTRA_SUMMARY_GATES = frozenset(
    {
        "bdd-in-network",
        "smoke-tests",
    }
)


# Frozen jobs Summary must require. Deriving this from the suite map lets one
# edit park a suite and drop its job. The derived set may only add jobs.
_SUMMARY_GATE_FLOOR = frozenset(
    {
        "unit-tests",
        "integration-tests",
        "e2e-tests",
        "bdd-tests",
        "admin-ui-tests",
    }
)


def _required_summary_gates() -> frozenset[str]:
    """Mapped Summary jobs from the suite map, plus the CI-only extras.

    ``_SUMMARY_GATE_FLOOR`` is a subset of this set. Dropping a mapped suite
    does not drop a floor job: the floor test fails first.
    """
    return frozenset(set(_LOCAL_SUITE_TO_SUMMARY_GATE.values()) | _EXTRA_SUMMARY_GATES)


# Used by tests below (computed once at import; no import-time membership assert).
REQUIRED_SUMMARY_GATES = _required_summary_gates()
_FREE_DISK_USES = "./.github/actions/_free-disk"
_WEBHOOK_INTERVAL = "${DELIVERY_WEBHOOK_INTERVAL:-5}"


def _is_adcp_testing_true(value: object) -> bool:
    """Match ``os.getenv("ADCP_TESTING") == "true"``: exact string, or YAML bool ``true``."""
    return value is True or value == "true"


def _assert_free_disk_action_reclaims_runner(action: Mapping[str, Any], *, label: str) -> None:
    """The composite's run body removes the preinstalled toolchains and prunes Docker."""
    runs = action.get("runs") or {}
    steps = runs.get("steps") if isinstance(runs, dict) else None
    assert isinstance(steps, list) and steps, f"{label} must declare at least one composite step."
    combined = steps_text(steps)
    assert combined, f"{label} must include a step with a run body."
    assert "/usr/share/dotnet" in combined, "_free-disk run step must remove /usr/share/dotnet."
    assert "docker builder prune" in combined, "_free-disk run step must prune the Docker builder cache."
    assert "set -euo pipefail" in combined, "_free-disk run step must use set -euo pipefail."


def _free_disk_action_for_step(step: Mapping[str, Any]) -> tuple[Mapping[str, Any], str]:
    """Load the action the step's ``uses:`` value names, not a hardcoded path."""
    uses = str(step.get("uses", ""))
    assert uses == _FREE_DISK_USES, f"free-disk step uses {uses!r}, expected {_FREE_DISK_USES}."
    path = repo_root() / uses.removeprefix("./") / "action.yml"
    return load_yaml_mapping(path), uses


def _jobs_that_run_the_runner() -> list[str]:
    """CI jobs whose steps invoke ``./run_all_tests.sh``."""
    names: list[str] = []
    for name, job in load_ci_workflow()["jobs"].items():
        for step in job.get("steps") or []:
            if isinstance(step, dict) and "./run_all_tests.sh" in str(step.get("run", "")):
                names.append(name)
                break
    return names


class TestCISuiteCoverage:
    """BDD and E2E suites must run in CI and gate the test summary."""

    pytestmark = pytest.mark.arch_guard

    def test_free_disk_action_requires_run_step_body(self):
        """Tokens only in description must not satisfy the reclaim contract.

        Mutation-shaped fixture: description mentions reclaim tokens, and a
        non-empty ``run`` step omits them — so the ≥1-step gate passes and the
        reclaim-token asserts are what must fail (empty ``steps: []`` never
        reaches those asserts).
        """
        vacuous = {
            "name": "Free disk space",
            "description": "Reclaim /usr/share/dotnet and docker builder prune",
            "runs": {
                "using": "composite",
                "steps": [
                    {
                        "name": "noop",
                        "run": "echo 'no reclaim tokens here'",
                        "shell": "bash",
                    }
                ],
            },
        }
        # Live helper must reject description-only contracts (MUT8 class).
        with pytest.raises(AssertionError):
            _assert_free_disk_action_reclaims_runner(vacuous, label="vacuous fixture")
        step = {"uses": _FREE_DISK_USES}
        action, label = _free_disk_action_for_step(step)
        _assert_free_disk_action_reclaims_runner(action, label=label)

    @pytest.mark.parametrize("value", ["True", "TRUE", "yes", "1"])
    def test_adcp_testing_rejects_casings_the_server_rejects(self, value: str):
        """``conftest`` compares with ``== "true"``. ``"True"`` must not pass this guard."""
        assert not _is_adcp_testing_true(value)

    def test_adcp_testing_accepts_the_yaml_bool_and_the_lowercase_string(self):
        assert _is_adcp_testing_true(True)
        assert _is_adcp_testing_true("true")

    def test_bdd_job_exists(self):
        """BDD aggregate + parallel shard jobs must exist in CI."""
        jobs = load_ci_workflow()["jobs"]

        assert "bdd-tests" in jobs, "No 'bdd-tests' aggregate job in .github/workflows/ci.yml."
        assert "bdd-tests-shard" in jobs, "No 'bdd-tests-shard' matrix job in .github/workflows/ci.yml."

    def test_bdd_shards_run_the_bdd_suite(self):
        """Each BDD shard must resolve paths via shard_paths and run pytest."""
        shard_job = load_ci_workflow()["jobs"]["bdd-tests-shard"]
        rendered = steps_text(shard_job.get("steps", []), keys=("run", "uses", "with"))

        assert "shard_paths.py bdd" in rendered, (
            "bdd-tests-shard must resolve test files via scripts/ci/shard_paths.py bdd."
        )
        assert "./.github/actions/_pytest" in rendered or "pytest" in rendered, (
            "bdd-tests-shard must invoke pytest (via _pytest composite or explicit run)."
        )

    def test_bdd_shards_have_postgres_service(self):
        """BDD harnesses use the integration_db fixture (real PostgreSQL)."""
        services = load_ci_workflow()["jobs"]["bdd-tests-shard"].get("services", {})

        assert "postgres" in services, "bdd-tests-shard has no postgres service."

    def test_bdd_aggregate_is_status_proxy_only(self):
        """Aggregate BDD job must gate shard status, not merge coverage."""
        aggregate = load_ci_workflow()["jobs"]["bdd-tests"]
        steps_text = job_run_text(aggregate)
        assert "needs.bdd-tests-shard.result" in steps_text, "bdd-tests aggregate must fail when bdd-tests-shard fails."
        assert "coverage combine" not in steps_text, (
            "bdd-tests aggregate must not merge coverage; that belongs in the Coverage job."
        )

    def test_admin_job_has_postgres_service(self):
        """Admin blueprint tests use integration_db and require PostgreSQL."""
        admin_job = load_ci_workflow()["jobs"]["admin-ui-tests"]
        services = admin_job.get("services", {})

        assert "postgres" in services, (
            "The 'admin-ui-tests' job has no 'postgres' service. Admin tests use "
            "the integration_db fixture and require a real PostgreSQL instance."
        )

    def test_integration_job_uses_entity_shards(self):
        """Integration tests must run in parallel entity shards (legacy parity)."""
        integration_job = load_ci_workflow()["jobs"]["integration-tests"]
        matrix = integration_job.get("strategy", {}).get("matrix", {})
        include = matrix.get("include", [])
        groups = {row["group"] for row in include}

        assert groups == {"creative", "product", "media-buy", "infra", "other"}, (
            "integration-tests must shard by entity marker groups to keep CI wall time bounded."
        )

        rendered = steps_text(integration_job.get("steps", []), keys=("run", "with"))
        assert "matrix.marker" in rendered, (
            "integration-tests must filter pytest with matrix.marker, not run the full suite serially."
        )

    def test_integration_shards_use_strict_partition(self):
        """Shards 2–4 must exclude earlier shard markers to avoid duplicate runs."""
        integration_job = load_ci_workflow()["jobs"]["integration-tests"]
        markers = {row["group"]: row["marker"] for row in integration_job["strategy"]["matrix"]["include"]}

        assert markers["creative"] == "creative"
        assert "and not creative" in markers["product"]
        assert "and not creative" in markers["media-buy"]
        assert "and not product" in markers["media-buy"]
        assert "and not creative" in markers["infra"]
        assert "and not product" in markers["infra"]
        assert "and not media_buy" in markers["infra"]
        assert "and not delivery" in markers["infra"]

    def test_quality_gate_does_not_run_unit_tests(self):
        """Quality Gate runs static checks only; unit tests run once in unit-tests."""
        quality_job = load_ci_workflow()["jobs"]["quality-gate"]
        run_steps = job_run_text(quality_job)

        assert "make quality-ci" in run_steps, "quality-gate must invoke make quality-ci (no pytest)."
        assert "pytest" not in run_steps, "quality-gate must not re-run unit tests."

    def test_coverage_job_reuses_test_artifacts(self):
        """Coverage gate must not re-run pytest; it combines unit + BDD artifacts."""
        coverage_job = load_ci_workflow()["jobs"]["coverage"]
        needs = coverage_job.get("needs", [])
        rendered = steps_text(coverage_job.get("steps", []), keys=("name", "uses"))

        assert "unit-tests" in needs, "coverage job must depend on unit-tests."
        assert "bdd-tests-shard" in needs, "coverage job must depend on bdd-tests-shard."
        assert rendered.count("download-artifact") >= 2, (
            "coverage job must download unit and BDD shard coverage artifacts."
        )
        run_steps = job_run_text(coverage_job)
        assert "pytest" not in run_steps, "coverage job must not re-run tests."
        assert "coverage combine" in run_steps, "coverage job must combine unit and BDD coverage data."

    def test_coverage_gate_requires_all_bdd_shard_artifacts(self):
        """Coverage must fail on partial BDD shard artifact sets, not combine survivors."""
        coverage_job = load_ci_workflow()["jobs"]["coverage"]
        run_steps = job_run_text(coverage_job)

        assert "SHARD_COUNTS" in run_steps, "coverage gate must read SHARD_COUNTS from shard_split."
        assert "expected_bdd_shards" in run_steps, "coverage gate must bind BDD shard count to a variable."
        assert "Missing BDD coverage artifact for shard" in run_steps, (
            "coverage gate must fail when any shard's .coverage.bdd-N file is missing."
        )
        bdd_downloads = [
            step.get("with", {})
            for step in coverage_job.get("steps", [])
            if step.get("with", {}).get("pattern") == "bdd-shard-*-coverage"
        ]
        assert bdd_downloads, "coverage job must download BDD shard artifacts via bdd-shard-*-coverage pattern."
        assert bdd_downloads[0].get("path") == "bdd-coverage-shards", (
            "BDD shard coverage artifacts must land under bdd-coverage-shards/."
        )

    def test_ci_jobs_declare_permissions_and_timeout(self):
        """Every CI job must declare permissions and timeout (workflow hygiene)."""
        jobs = load_ci_workflow()["jobs"]
        missing: list[str] = []
        for job_name, job in jobs.items():
            if "permissions" not in job:
                missing.append(f"{job_name}: permissions")
            if "timeout-minutes" not in job:
                missing.append(f"{job_name}: timeout-minutes")
        assert not missing, "CI jobs missing hygiene fields:\n" + "\n".join(f"  - {m}" for m in missing)

    def test_local_suites_map_or_explicit_exclusion(self):
        """ALL_SUITES must map to summary gates or UNGATED_LOCAL_SUITES (no silent drops)."""
        suites = set(all_suites_from_runner())
        assert "ui" in suites, "run_all_tests.sh ALL_SUITES must still list ui (exclusion is at the gate map)"
        # Stale map keys only. A growth check here cannot fail: the intersection
        # of the map with ALL_SUITES is a subset of the map.
        missing_from_runner = [suite for suite in _LOCAL_SUITE_TO_SUMMARY_GATE if suite not in suites]
        assert not missing_from_runner, (
            "_LOCAL_SUITE_TO_SUMMARY_GATE names a suite ALL_SUITES no longer lists: "
            f"{missing_from_runner}. Drop the map entry in the same change, or restore the suite."
        )
        unmapped = {suite for suite in suites if suite not in _LOCAL_SUITE_TO_SUMMARY_GATE}
        assert_violations_match_allowlist(
            unmapped,
            set(UNGATED_LOCAL_SUITES),
            fix_hint=(
                "Map the suite to a Summary gate job in _LOCAL_SUITE_TO_SUMMARY_GATE, "
                "or add an UngatedSuite record with an open assigned owner issue."
            ),
        )
        assert _SUMMARY_GATE_FLOOR <= REQUIRED_SUMMARY_GATES, (
            f"Summary floor {sorted(_SUMMARY_GATE_FLOOR - REQUIRED_SUMMARY_GATES)} left the derived gate set. "
            "The derivation may add jobs. It may not drop a floor job."
        )
        floor_suites = {suite for suite, job in _LOCAL_SUITE_TO_SUMMARY_GATE.items() if job in _SUMMARY_GATE_FLOOR}
        parked_floor = floor_suites & set(UNGATED_LOCAL_SUITES)
        assert not parked_floor, (
            f"UNGATED_LOCAL_SUITES parks {sorted(parked_floor)}, whose Summary job is in the frozen floor."
        )
        owners = [record.owner_issue for record in UNGATED_LOCAL_SUITES.values()]
        assert len(owners) == len(set(owners)), f"owner issues are not distinct: {owners}"
        assert {suite: record.owner_issue for suite, record in UNGATED_LOCAL_SUITES.items()} == _UNGATED_OWNER_ISSUES
        jobs = load_ci_workflow()["jobs"]
        for suite, record in UNGATED_LOCAL_SUITES.items():
            assert record.why.strip(), f"{suite} needs a non-empty why"
            if record.state is SuiteGateState.NO_AUTOMATED_RUN:
                assert record.ci_job is None, f"{suite} has no CI job"
                continue
            assert record.ci_job in jobs, f"{suite} names missing job {record.ci_job!r}"

    def test_summary_gates_every_required_job(self):
        """summary must include every suite gate AND fail for every job in summary.needs."""
        workflow = load_ci_workflow()
        summary = workflow["jobs"]["summary"]
        needs = summary["needs"]
        check_text = job_run_text(summary)

        assert isinstance(needs, list) and needs, "summary.needs must list every upstream gate job."
        missing = REQUIRED_SUMMARY_GATES - set(needs)
        assert not missing, (
            f"summary.needs dropped required suite gate(s) {sorted(missing)}. A suite that runs but is "
            "absent from summary.needs leaves CI green on its failure."
        )
        for required in needs:
            assert required in workflow["jobs"], (
                f"summary.needs lists unknown job '{required}'. Update summary.needs or add the missing job definition."
            )
            token = f"needs.{required}.result"
            assert token in check_text, (
                f"summary's result-check step does not inspect '{token}'. "
                f"A failing {required} job would not fail CI even though it is listed in needs[]."
            )

    def test_type_check_uses_make_target(self):
        """Type Check job must invoke the same entrypoint as local make typecheck."""
        type_check = load_ci_workflow()["jobs"]["type-check"]
        run_steps = job_run_text(type_check)
        assert "make typecheck" in run_steps, "type-check job must run make typecheck for CI/local parity."

    def test_smoke_tests_do_not_duplicate_skip_guard(self):
        """Skip-decorator enforcement belongs in the smoke suite, not a workflow grep step."""
        smoke_job = load_ci_workflow()["jobs"]["smoke-tests"]
        rendered = steps_text(smoke_job.get("steps", []), keys=("name", "run"))
        skip_marker = "@" + "pytest" + ".mark.skip"
        assert skip_marker not in rendered, (
            "smoke-tests must not grep for skip decorators in a workflow step; "
            "TestNoSkippedTests is the single source of truth."
        )

    def test_skip_guard_single_source_of_truth_exists(self):
        """The SSoT the workflow delegates to must exist, or enforcement is unguarded."""
        from tests.smoke.test_smoke_basic import TestNoSkippedTests

        assert hasattr(TestNoSkippedTests, "test_no_skip_decorators"), (
            "TestNoSkippedTests.test_no_skip_decorators is the declared single source of truth for skip enforcement."
        )

    def test_e2e_job_runs_in_network(self):
        """E2E CI runs inside the compose network via ``./run_all_tests.sh e2e``.

        Host pytest cannot resolve ``counterparty.adcp-e2e.dev`` (compose-only
        DNS). ``run_all_tests.sh`` owns TLS, the creative-agent image, and compose.
        """
        workflow = load_ci_workflow()
        workflow_env = workflow.get("env") or {}
        assert _is_adcp_testing_true(workflow_env.get("ADCP_TESTING")), (
            "workflow env must set ADCP_TESTING true (compose defaults it for adcp-server)."
        )

        job = workflow["jobs"]["e2e-tests"]
        steps = job.get("steps", [])
        assert steps, "e2e-tests must declare steps (empty job is a vacuous pass)."
        run_body = job_run_text(job)
        assert "./run_all_tests.sh e2e" in run_body, "e2e-tests must invoke ./run_all_tests.sh e2e."
        assert "pytest" not in run_body, "e2e-tests must not invoke pytest on the host."
        assert re.search(r"docker compose\b[^\n]*\bup\b", run_body) is None, (
            "e2e-tests must not grow a host compose up. The runner owns the stack."
        )
        run_steps = [step for step in steps if "./run_all_tests.sh e2e" in str(step.get("run", ""))]
        assert len(run_steps) == 1, "e2e-tests must have one in-network run step."
        run_env = run_steps[0].get("env") or {}
        image = str(run_env.get("CREATIVE_AGENT_GHCR_IMAGE", ""))
        assert image.startswith("ghcr.io/"), (
            f"The in-network step must set CREATIVE_AGENT_GHCR_IMAGE to a ghcr.io image (got {image!r})."
        )
        assert "DELIVERY_WEBHOOK_INTERVAL" not in run_env, (
            "The in-network step must not set DELIVERY_WEBHOOK_INTERVAL. docker-compose.e2e.yml is the writer."
        )
        compose = load_yaml_mapping(repo_root() / "docker-compose.e2e.yml")
        for service in ("adcp-server", "tests"):
            environment = compose_service_environment(compose, service)
            assert environment.get("DELIVERY_WEBHOOK_INTERVAL") == _WEBHOOK_INTERVAL, (
                f"{service} must interpolate {_WEBHOOK_INTERVAL}."
            )

    @pytest.mark.parametrize("job_name", _jobs_that_run_the_runner())
    def test_runner_jobs_reclaim_disk_before_the_run(self, job_name: str):
        """Every ``./run_all_tests.sh`` job uses the shared reclaim composite first."""
        steps = load_ci_workflow()["jobs"][job_name].get("steps") or []
        located = find_step_index(steps, uses_equals=_FREE_DISK_USES)
        assert located is not None, f"{job_name} must use {_FREE_DISK_USES}."
        free_idx, step = located
        action, label = _free_disk_action_for_step(step)
        _assert_free_disk_action_reclaims_runner(action, label=label)
        runner_indexes = [
            index for index, candidate in enumerate(steps) if "./run_all_tests.sh" in str(candidate.get("run", ""))
        ]
        assert runner_indexes, f"{job_name} must invoke ./run_all_tests.sh."
        assert free_idx < min(runner_indexes), f"{job_name}: reclaim must precede the runner step."

    def test_bdd_in_network_caps_pgdata_tmpfs(self):
        """The serial in-network leg caps PGDATA tmpfs so the runner has RAM left."""
        job = load_ci_workflow()["jobs"]["bdd-in-network"]
        steps = job.get("steps", [])
        run_steps = [step for step in steps if "run_all_tests.sh bdd_e2e" in str(step.get("run", ""))]
        assert len(run_steps) == 1, "bdd-in-network must invoke ./run_all_tests.sh bdd_e2e."
        env = run_steps[0].get("env") or {}
        assert env.get("PGDATA_TMPFS_SIZE") == "2g", (
            "bdd-in-network must set PGDATA_TMPFS_SIZE=2g "
            "(serial leg; default 10g wastes RAM — tmpfs size= is a RAM ceiling)."
        )

    def test_bdd_and_e2e_run_on_pull_request(self):
        """The gate is worthless if it doesn't run on PRs.

        Cost-aware: it must reuse the existing pull_request trigger, not add
        new triggers.
        """
        workflow = load_ci_workflow()
        # PyYAML parses the bare ``on:`` key as the boolean True.
        triggers = workflow.get("on", workflow.get(True))

        assert "pull_request" in triggers, (
            "The CI workflow does not trigger on pull_request, so BDD/E2E gating would never run before merge."
        )
