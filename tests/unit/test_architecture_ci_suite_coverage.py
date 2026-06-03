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

import pathlib

import yaml

WORKFLOW_PATH = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text())


class TestCISuiteCoverage:
    """BDD and E2E suites must run in CI and gate the test summary."""

    def test_bdd_job_exists(self):
        """A dedicated BDD job must exist in the CI workflow.

        Before this guard, ``tests/bdd/`` was never executed in CI, so any
        PR could break every BDD scenario and still show green.
        """
        jobs = _load_workflow()["jobs"]

        assert "bdd-tests" in jobs, (
            "No 'bdd-tests' job in .github/workflows/ci.yml. The BDD suite "
            "(tests/bdd/) is run by ./run_all_tests.sh but has zero CI "
            "coverage — a broken BDD suite can silently land on main. Add a "
            "'bdd-tests' job mirroring 'integration-tests' (Postgres service "
            "+ migrations + pytest tests/bdd/)."
        )

    def test_bdd_job_runs_the_bdd_suite(self):
        """The BDD job must actually invoke ``pytest tests/bdd/``.

        A job that exists but doesn't run the suite is worse than no job —
        it gives false confidence.
        """
        bdd_job = _load_workflow()["jobs"]["bdd-tests"]
        run_steps = " ".join(str(step.get("run", "")) for step in bdd_job.get("steps", []))

        assert "pytest tests/bdd/" in run_steps, (
            "The 'bdd-tests' job does not run 'pytest tests/bdd/'. The job "
            "must execute the BDD suite, not merely exist."
        )

    def test_bdd_job_has_postgres_service(self):
        """BDD harnesses use the integration_db fixture (real PostgreSQL).

        Without a Postgres service the BDD job cannot run, so the gate would
        be hollow.
        """
        bdd_job = _load_workflow()["jobs"]["bdd-tests"]
        services = bdd_job.get("services", {})

        assert "postgres" in services, (
            "The 'bdd-tests' job has no 'postgres' service. BDD scenarios use "
            "the integration_db fixture and require a real PostgreSQL "
            "instance (mirror the integration-tests job)."
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

        run_steps = " ".join(str(step.get("run", "")) for step in integration_job.get("steps", []))
        assert "matrix.marker" in run_steps, (
            "integration-tests must filter pytest with matrix.marker, not run the full suite serially."
        )

    def test_quality_gate_does_not_run_unit_tests(self):
        """Quality Gate runs static checks only; unit tests run once in unit-tests."""
        quality_job = load_ci_workflow()["jobs"]["quality-gate"]
        run_steps = " ".join(str(step.get("run", "")) for step in quality_job.get("steps", []))

        assert "make quality-ci" in run_steps, "quality-gate must invoke make quality-ci (no pytest)."
        assert "pytest" not in run_steps, "quality-gate must not re-run unit tests."

    def test_coverage_job_reuses_unit_test_artifact(self):
        """Coverage gate must not re-run pytest; it consumes unit-tests artifacts."""
        coverage_job = load_ci_workflow()["jobs"]["coverage"]
        needs = coverage_job.get("needs", [])
        steps_text = " ".join(
            str(step.get("name", "")) + " " + str(step.get("uses", "")) for step in coverage_job.get("steps", [])
        )

        assert "unit-tests" in needs, "coverage job must depend on unit-tests."
        assert "download-artifact" in steps_text, "coverage job must download unit test coverage data."
        run_steps = " ".join(str(step.get("run", "")) for step in coverage_job.get("steps", []))
        assert "pytest" not in run_steps, "coverage job must not re-run unit tests."

    def test_summary_gates_bdd_and_e2e(self):
        """summary must depend on AND fail for bdd + e2e.

        A job that runs but isn't in ``needs`` (or whose failure isn't
        checked in the aggregation step) leaves the gate soft — exactly the
        leak that let PR #1299 land red.
        """
        workflow = _load_workflow()
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
        workflow = _load_workflow()
        # PyYAML parses the bare ``on:`` key as the boolean True.
        triggers = workflow.get("on", workflow.get(True))

        assert "pull_request" in triggers, (
            "The CI workflow does not trigger on pull_request, so BDD/E2E gating would never run before merge."
        )
