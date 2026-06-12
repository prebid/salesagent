"""Guard: every test suite run_all_tests.sh exercises must gate CI.

Regression coverage for the post-PR-#1299 silent-breakage gap: PR #1299
merged with CI green, yet ``./run_all_tests.sh`` immediately showed 12 BDD +
32 E2E failures on main. Root cause: the BDD suite had **zero** CI coverage
(no job in ``.github/workflows/ci.yml``), and the aggregation/gating job
must propagate BDD/E2E failures so a red suite turns CI red.

This is a configuration-contract assertion: the workflow file is parsed as
YAML data and the job graph is inspected. It is NOT a source-code AST scan.

No allowlist — zero tolerance. Every locally-run suite must have a gating
CI job, or a broken suite can silently land on main again.
"""

import pytest

from tests.unit.workflow_helpers import load_ci_workflow


class TestCISuiteCoverage:
    """BDD and E2E suites must run in CI and gate the test summary."""

    def test_bdd_job_exists(self):
        """BDD aggregate + parallel shard jobs must exist in CI."""
        jobs = load_ci_workflow()["jobs"]

        assert "bdd-tests" in jobs, "No 'bdd-tests' aggregate job in .github/workflows/ci.yml."
        assert "bdd-tests-shard" in jobs, "No 'bdd-tests-shard' matrix job in .github/workflows/ci.yml."

    def test_bdd_shards_run_the_bdd_suite(self):
        """Each BDD shard must resolve paths via shard_paths and run pytest."""
        shard_job = load_ci_workflow()["jobs"]["bdd-tests-shard"]
        steps_text = " ".join(
            str(step.get("run", "")) + " " + str(step.get("uses", "")) + " " + str(step.get("with", {}))
            for step in shard_job.get("steps", [])
        )

        assert "shard_paths.py bdd" in steps_text, (
            "bdd-tests-shard must resolve test files via scripts/ci/shard_paths.py bdd."
        )
        assert "./.github/actions/_pytest" in steps_text or "pytest" in steps_text, (
            "bdd-tests-shard must invoke pytest (via _pytest composite or explicit run)."
        )

    def test_bdd_shards_have_postgres_service(self):
        """BDD harnesses use the integration_db fixture (real PostgreSQL)."""
        services = load_ci_workflow()["jobs"]["bdd-tests-shard"].get("services", {})

        assert "postgres" in services, "bdd-tests-shard has no postgres service."

    def test_bdd_aggregate_is_status_proxy_only(self):
        """Aggregate BDD job must gate shard status, not merge coverage."""
        aggregate = load_ci_workflow()["jobs"]["bdd-tests"]
        steps_text = " ".join(str(step.get("run", "")) for step in aggregate.get("steps", []))
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

        step_text = " ".join(
            str(step.get("run", "")) + " " + str(step.get("with", {}).get("extra_args", ""))
            for step in integration_job.get("steps", [])
        )
        assert "matrix.marker" in step_text, (
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
        run_steps = " ".join(str(step.get("run", "")) for step in quality_job.get("steps", []))

        assert "make quality-ci" in run_steps, "quality-gate must invoke make quality-ci (no pytest)."
        assert "pytest" not in run_steps, "quality-gate must not re-run unit tests."

    def test_coverage_job_reuses_test_artifacts(self):
        """Coverage gate must not re-run pytest; it combines unit + BDD artifacts."""
        coverage_job = load_ci_workflow()["jobs"]["coverage"]
        needs = coverage_job.get("needs", [])
        steps_text = " ".join(
            str(step.get("name", "")) + " " + str(step.get("uses", "")) for step in coverage_job.get("steps", [])
        )

        assert "unit-tests" in needs, "coverage job must depend on unit-tests."
        assert "bdd-tests-shard" in needs, "coverage job must depend on bdd-tests-shard."
        assert steps_text.count("download-artifact") >= 2, (
            "coverage job must download unit and BDD shard coverage artifacts."
        )
        run_steps = " ".join(str(step.get("run", "")) for step in coverage_job.get("steps", []))
        assert "pytest" not in run_steps, "coverage job must not re-run tests."
        assert "coverage combine" in run_steps, "coverage job must combine unit and BDD coverage data."

    @pytest.mark.arch_guard
    def test_coverage_gate_requires_all_bdd_shard_artifacts(self):
        """Coverage must fail on partial BDD shard artifact sets, not combine survivors."""
        coverage_job = load_ci_workflow()["jobs"]["coverage"]
        run_steps = " ".join(str(step.get("run", "")) for step in coverage_job.get("steps", []))

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

    @pytest.mark.arch_guard
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

    def test_summary_gates_bdd_and_e2e(self):
        """summary must depend on AND fail for bdd + e2e.

        A job that runs but isn't in ``needs`` (or whose failure isn't
        checked in the aggregation step) leaves the gate soft — exactly the
        leak that let PR #1299 land red.
        """
        workflow = load_ci_workflow()
        summary = workflow["jobs"]["summary"]
        needs = summary["needs"]

        for required in ("bdd-tests", "e2e-tests"):
            assert required in needs, (
                f"summary.needs is missing '{required}'. CI will report green even when the {required} suite fails."
            )

        # The aggregation step must actually check the result of each suite.
        check_text = " ".join(str(step.get("run", "")) for step in summary.get("steps", []))
        for required in ("bdd-tests", "e2e-tests"):
            token = f"needs.{required}.result"
            assert token in check_text, (
                f"summary's result-check step does not inspect "
                f"'{token}'. A failing {required} suite would not fail CI "
                f"even though it is listed in needs[]."
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
