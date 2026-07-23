"""Guard: every test suite run_all_tests.sh exercises must gate CI.

Regression coverage for the post-PR-#1299 silent-breakage gap: PR #1299
merged with CI green, yet ``./run_all_tests.sh`` immediately showed 12 BDD +
32 E2E failures on main. Root cause: the BDD suite had **zero** CI coverage
(no job in ``.github/workflows/ci.yml``), and the aggregation/gating job
must propagate BDD/E2E failures so a red suite turns CI red.

This is a configuration-contract assertion: the workflow file is parsed as
YAML data and the job graph is inspected. It is NOT a source-code AST scan.

No allowlist — zero tolerance for mapped suites. Every locally-run suite in
``run_all_tests.sh`` ALL_SUITES must map to a summary gate **or** an explicit
documented exclusion (currently ``ui`` — Playwright / full-stack cost, no CI
job yet). CI-only gates (smoke, bdd-in-network, ipr-gate) are listed separately.
"""

import re
from pathlib import Path

import pytest

from scripts.ci.workflow_helpers import load_ci_workflow
from tests.unit._architecture_helpers import load_yaml_mapping, repo_root

# Local suite → summary.needs job. Derived from run_all_tests.sh ALL_SUITES;
# unmapped suites must be listed in UNGATED_LOCAL_SUITES with a documented reason.
_LOCAL_SUITE_TO_SUMMARY_GATE: dict[str, str] = {
    "unit": "unit-tests",
    "integration": "integration-tests",
    "bdd": "bdd-tests",
    "admin": "admin-ui-tests",
    "e2e": "e2e-tests",
}
# Intentionally ungated in CI (no job yet). Soften the "every suite" claim:
# these are excluded at the source map, not silently omitted from a hand list.
UNGATED_LOCAL_SUITES = frozenset({"ui"})

# CI-only gates that are not ALL_SUITES entries but must still floor Summary.
_EXTRA_SUMMARY_GATES = frozenset(
    {
        "bdd-in-network",
        "smoke-tests",
        "ipr-gate",  # merge-gate IPR via Summary (org ruleset does not require ipr-check)
    }
)


def _all_suites_from_runner() -> list[str]:
    runner = repo_root() / "run_all_tests.sh"
    m = re.search(r'ALL_SUITES="([^"]+)"', runner.read_text(encoding="utf-8"))
    assert m, "run_all_tests.sh must define ALL_SUITES"
    return [s.strip() for s in m.group(1).split(",") if s.strip()]


def _required_summary_gates() -> frozenset[str]:
    suites = _all_suites_from_runner()
    unknown = [s for s in suites if s not in _LOCAL_SUITE_TO_SUMMARY_GATE and s not in UNGATED_LOCAL_SUITES]
    assert not unknown, (
        f"ALL_SUITES entries {unknown} are neither mapped to a summary gate nor listed in "
        f"UNGATED_LOCAL_SUITES — add a CI job map entry or an explicit exclusion."
    )
    mapped = {_LOCAL_SUITE_TO_SUMMARY_GATE[s] for s in suites if s in _LOCAL_SUITE_TO_SUMMARY_GATE}
    return frozenset(mapped | _EXTRA_SUMMARY_GATES)


# Back-compat name used by tests below (computed once at import).
REQUIRED_SUMMARY_GATES = _required_summary_gates()

_FREE_DISK_USES = "./.github/actions/_free-disk"
_FREE_DISK_ACTION = repo_root() / ".github" / "actions" / "_free-disk" / "action.yml"
_E2E_COMPOSE = repo_root() / "docker-compose.e2e.yml"


def _is_adcp_testing_true(value: object) -> bool:
    """GHA env is always a string at runtime; YAML may parse unquoted ``true`` as bool."""
    return value is True or str(value).lower() == "true"


def _shell_has_flag(script: str, flag: str) -> bool:
    """True if ``flag`` appears as its own argv token on a non-comment code line.

    Substring checks like ``\"up -d --wait\" in run`` are vacuous against
    ``up -d --wait-timeout 600`` — ``--wait`` is a prefix of ``--wait-timeout``.
    Shell comments (``# --wait gates ...``) must not count as the flag either.
    """
    for line in script.splitlines():
        code = line.split("#", 1)[0]
        if flag in code.split():
            return True
    return False


def _shell_flag_value(script: str, flag: str) -> str | None:
    """Return the argv token immediately after ``flag`` on a non-comment line."""
    for line in script.splitlines():
        tokens = line.split("#", 1)[0].split()
        for i, token in enumerate(tokens):
            if token == flag and i + 1 < len(tokens):
                return tokens[i + 1]
    return None


def _shell_has_non_comment_substr(script: str, *needles: str) -> bool:
    """True if every needle appears on some non-comment code line (not only comments)."""
    code = "\n".join(line.split("#", 1)[0] for line in script.splitlines())
    return all(n in code for n in needles)


def _parse_compose_duration_seconds(value: object) -> float:
    """Parse compose duration strings like ``60s`` / ``1m`` / ints into seconds."""
    if value is None:
        raise AssertionError("duration value is missing")
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        text = str(value).strip().lower()
        if text.endswith("ms"):
            seconds = float(text[:-2]) / 1000.0
        elif text.endswith("s"):
            seconds = float(text[:-1])
        elif text.endswith("m"):
            seconds = float(text[:-1]) * 60.0
        elif text.endswith("h"):
            seconds = float(text[:-1]) * 3600.0
        else:
            seconds = float(text)
    if seconds <= 0:
        raise AssertionError(f"duration must be positive (got {value!r})")
    return seconds


def _find_free_disk_step(steps: list) -> tuple[int, dict]:
    """Locate the shared _free-disk composite step (name may be present for CI UI)."""
    for i, step in enumerate(steps):
        uses = str(step.get("uses", "")).rstrip("/")
        if uses.endswith("_free-disk"):
            return i, step
    raise AssertionError(f"job must include uses: {_FREE_DISK_USES} (single source for runner reclaim).")


def _assert_free_disk_action_reclaims_runner(action: dict | Path | None = None) -> None:
    """Contract lives in a composite *run* step body — not description-only tokens.

    Pass a parsed action mapping (or path) to exercise fixtures; default reads the
    production ``.github/actions/_free-disk/action.yml``.
    """
    label: str
    if action is None:
        data = load_yaml_mapping(_FREE_DISK_ACTION)
        label = str(_FREE_DISK_ACTION)
    elif isinstance(action, Path):
        data = load_yaml_mapping(action)
        label = str(action)
    else:
        data = action
        label = "parsed free-disk action"
    assert isinstance(data, dict), f"{label} must be a YAML mapping."
    runs = data.get("runs") or {}
    steps = runs.get("steps") if isinstance(runs, dict) else None
    assert isinstance(steps, list) and steps, f"{label} must declare ≥1 composite step."
    run_bodies = [str(step.get("run", "")) for step in steps if isinstance(step, dict)]
    assert run_bodies, f"{label} must include a step with a run body."
    combined = "\n".join(run_bodies)
    assert "/usr/share/dotnet" in combined, "_free-disk run step must remove /usr/share/dotnet."
    assert "docker builder prune" in combined, "_free-disk run step must prune the Docker builder cache."


def _load_e2e_compose() -> dict:
    data = load_yaml_mapping(_E2E_COMPOSE)
    assert data.get("services"), f"{_E2E_COMPOSE} must declare services."
    return data


class TestCISuiteCoverage:
    """BDD and E2E suites must run in CI and gate the test summary."""

    @pytest.mark.arch_guard
    def test_shell_has_flag_rejects_wait_timeout_prefix(self):
        """``--wait`` must not match as a prefix of ``--wait-timeout`` (vacuous guard class)."""
        wait_timeout_only = "docker compose up -d --wait-timeout 600"
        assert not _shell_has_flag(wait_timeout_only, "--wait")
        assert _shell_has_flag(wait_timeout_only, "--wait-timeout")
        both = "docker compose up -d --wait --wait-timeout 600"
        assert _shell_has_flag(both, "--wait")
        assert _shell_has_flag(both, "--wait-timeout")
        # Comments must not satisfy the flag contract (CI pre-start run has `# --wait gates…`).
        comment_only = "# --wait gates postgres\nup -d --wait-timeout 600"
        assert not _shell_has_flag(comment_only, "--wait")
        assert _shell_has_flag(comment_only, "--wait-timeout")
        # Adjacent argv after --wait-timeout — comment token "600" must not satisfy.
        assert _shell_flag_value("up -d --wait-timeout 600", "--wait-timeout") == "600"
        assert _shell_flag_value("# budget 600\nup -d --wait-timeout 120", "--wait-timeout") == "120"
        assert _shell_flag_value("# --wait-timeout 600\nup -d --wait-timeout 120", "--wait-timeout") == "120"
        # curl /health must be on a non-comment line.
        assert _shell_has_non_comment_substr("curl -sf http://127.0.0.1:8080/health", "curl -sf", "/health")
        assert not _shell_has_non_comment_substr("# curl -sf /health\necho ok", "curl -sf", "/health")

    @pytest.mark.arch_guard
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
            _assert_free_disk_action_reclaims_runner(vacuous)
        # Production composite still passes the real assert.
        _assert_free_disk_action_reclaims_runner()

    @pytest.mark.arch_guard
    def test_bdd_job_exists(self):
        """BDD aggregate + parallel shard jobs must exist in CI."""
        jobs = load_ci_workflow()["jobs"]

        assert "bdd-tests" in jobs, "No 'bdd-tests' aggregate job in .github/workflows/ci.yml."
        assert "bdd-tests-shard" in jobs, "No 'bdd-tests-shard' matrix job in .github/workflows/ci.yml."

    @pytest.mark.arch_guard
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

    @pytest.mark.arch_guard
    def test_bdd_shards_have_postgres_service(self):
        """BDD harnesses use the integration_db fixture (real PostgreSQL)."""
        services = load_ci_workflow()["jobs"]["bdd-tests-shard"].get("services", {})

        assert "postgres" in services, "bdd-tests-shard has no postgres service."

    @pytest.mark.arch_guard
    def test_bdd_aggregate_is_status_proxy_only(self):
        """Aggregate BDD job must gate shard status, not merge coverage."""
        aggregate = load_ci_workflow()["jobs"]["bdd-tests"]
        steps_text = " ".join(str(step.get("run", "")) for step in aggregate.get("steps", []))
        assert "needs.bdd-tests-shard.result" in steps_text, "bdd-tests aggregate must fail when bdd-tests-shard fails."
        assert "coverage combine" not in steps_text, (
            "bdd-tests aggregate must not merge coverage; that belongs in the Coverage job."
        )

    @pytest.mark.arch_guard
    def test_admin_job_has_postgres_service(self):
        """Admin blueprint tests use integration_db and require PostgreSQL."""
        admin_job = load_ci_workflow()["jobs"]["admin-ui-tests"]
        services = admin_job.get("services", {})

        assert "postgres" in services, (
            "The 'admin-ui-tests' job has no 'postgres' service. Admin tests use "
            "the integration_db fixture and require a real PostgreSQL instance."
        )

    @pytest.mark.arch_guard
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

    @pytest.mark.arch_guard
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

    @pytest.mark.arch_guard
    def test_quality_gate_does_not_run_unit_tests(self):
        """Quality Gate runs static checks only; unit tests run once in unit-tests."""
        quality_job = load_ci_workflow()["jobs"]["quality-gate"]
        run_steps = " ".join(str(step.get("run", "")) for step in quality_job.get("steps", []))

        assert "make quality-ci" in run_steps, "quality-gate must invoke make quality-ci (no pytest)."
        assert "pytest" not in run_steps, "quality-gate must not re-run unit tests."

    @pytest.mark.arch_guard
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

    @pytest.mark.arch_guard
    @pytest.mark.arch_guard
    def test_local_suites_map_or_explicit_exclusion(self):
        """ALL_SUITES must map to summary gates or UNGATED_LOCAL_SUITES (no silent drops)."""
        suites = _all_suites_from_runner()
        assert "ui" in UNGATED_LOCAL_SUITES, "ui must stay an explicit ungated exclusion until a CI job exists"
        assert "ui" in suites, "run_all_tests.sh ALL_SUITES must still list ui (exclusion is at the gate map)"
        assert "ipr-gate" in REQUIRED_SUMMARY_GATES, "Summary must gate IPR (merge-block via required Summary check)"

    def test_summary_gates_every_required_job(self):
        """summary must include every suite gate AND fail for every job in summary.needs."""
        workflow = load_ci_workflow()
        summary = workflow["jobs"]["summary"]
        needs = summary["needs"]
        check_text = " ".join(str(step.get("run", "")) for step in summary.get("steps", []))

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

    @pytest.mark.arch_guard
    def test_type_check_uses_make_target(self):
        """Type Check job must invoke the same entrypoint as local make typecheck."""
        type_check = load_ci_workflow()["jobs"]["type-check"]
        run_steps = " ".join(str(step.get("run", "")) for step in type_check.get("steps", []))
        assert "make typecheck" in run_steps, "type-check job must run make typecheck for CI/local parity."

    @pytest.mark.arch_guard
    def test_smoke_tests_do_not_duplicate_skip_guard(self):
        """Skip-decorator enforcement belongs in the smoke suite, not a workflow grep step."""
        smoke_job = load_ci_workflow()["jobs"]["smoke-tests"]
        step_text = " ".join(f"{step.get('name', '')} {step.get('run', '')}" for step in smoke_job.get("steps", []))
        skip_marker = "@" + "pytest" + ".mark.skip"
        assert skip_marker not in step_text, (
            "smoke-tests must not grep for skip decorators in a workflow step; "
            "TestNoSkippedTests is the single source of truth."
        )

    @pytest.mark.arch_guard
    def test_skip_guard_single_source_of_truth_exists(self):
        """The SSoT the workflow delegates to must exist, or enforcement is unguarded."""
        from tests.smoke.test_smoke_basic import TestNoSkippedTests

        assert hasattr(TestNoSkippedTests, "test_no_skip_decorators"), (
            "TestNoSkippedTests.test_no_skip_decorators is the declared single source of truth for skip enforcement."
        )

    @pytest.mark.arch_guard
    def test_e2e_job_prestarts_stack_with_adcp_testing(self):
        """E2E CI must pre-start compose; pytest must not cold-build under --timeout.

        Regression: clearing ADCP_TESTING forced docker_services_e2e into the
        standalone build+up path inside pytest setup, which pytest-timeout=300
        killed on cold runners (~40 setup ERRORs). Inheritance alone is not
        durable — the pytest step must pin ADCP_TESTING=true.
        """
        workflow = load_ci_workflow()
        workflow_env = workflow.get("env") or {}
        assert _is_adcp_testing_true(workflow_env.get("ADCP_TESTING")), (
            "workflow env must set ADCP_TESTING true (pytest inherits when step omits it)."
        )

        job = workflow["jobs"]["e2e-tests"]
        steps = job.get("steps", [])
        assert steps, "e2e-tests must declare steps (empty job is a vacuous pass)."

        step_names = [s.get("name") for s in steps]
        assert "Build and start E2E stack" in step_names, "e2e-tests must pre-start the compose stack before pytest."
        free_idx, _ = _find_free_disk_step(steps)
        _assert_free_disk_action_reclaims_runner()

        prestart = next(s for s in steps if s.get("name") == "Build and start E2E stack")
        prestart_run = str(prestart.get("run", ""))
        prestart_env = prestart.get("env") or {}
        job_env = job.get("env") or {}
        assert "creative-agent-stack.sh build" in prestart_run, "Pre-start must build the pinned creative-agent image."
        # Overlay may be literal -f flags in the run script OR job-level COMPOSE_FILE
        # (docker compose native multi-file, colon-separated).
        compose_file = str(job_env.get("COMPOSE_FILE", "") or prestart_env.get("COMPOSE_FILE", ""))
        assert "docker-compose.e2e.ports.yml" in prestart_run or "docker-compose.e2e.ports.yml" in compose_file, (
            "Pre-start must overlay docker-compose.e2e.ports.yml via run -f flags or "
            "job/step COMPOSE_FILE (host curl/pytest ports)."
        )
        assert prestart_env.get("ADCP_SALES_PORT"), "Pre-start must set ADCP_SALES_PORT for host health curl."
        assert "CREATIVE_AGENT_GHCR_IMAGE" in prestart_env, (
            "Pre-start must set CREATIVE_AGENT_GHCR_IMAGE (CI owns the only cold-build path)."
        )
        assert "ghcr.io" in str(prestart_env.get("CREATIVE_AGENT_GHCR_IMAGE", "")), (
            "CREATIVE_AGENT_GHCR_IMAGE must point at ghcr.io for pin-keyed pull preference."
        )
        # Token match — substring "up -d --wait" falsely matches "--wait-timeout" alone.
        assert _shell_has_flag(prestart_run, "--wait"), (
            "Pre-start must use compose up --wait as its own flag (healthcheck gate)."
        )
        assert _shell_has_flag(prestart_run, "--wait-timeout"), "Pre-start must budget --wait-timeout for migrations."
        assert _shell_flag_value(prestart_run, "--wait-timeout") == "600", (
            "Pre-start --wait-timeout must be 600 (adjacent argv; comment tokens do not count)."
        )
        assert _shell_has_non_comment_substr(prestart_run, "curl -sf", "/health"), (
            "Pre-start must curl /health after up --wait on a non-comment line."
        )
        assert _is_adcp_testing_true(prestart_env.get("ADCP_TESTING")), "Pre-start must set ADCP_TESTING=true."
        assert int(job.get("timeout-minutes") or 0) >= 40, (
            f"e2e-tests timeout-minutes must be >= 40 to cover --wait-timeout 600 (got {job.get('timeout-minutes')!r})."
        )

        pytest_steps = [
            s for s in steps if "pytest" in str(s.get("uses", "")).lower() or "tests/e2e" in str(s.get("with", {}))
        ]
        assert pytest_steps, "e2e-tests must invoke pytest on tests/e2e/."
        pytest_env = pytest_steps[0].get("env") or {}
        # Explicit pin — do not treat "key absent → inherits workflow" as sufficient.
        assert "ADCP_TESTING" in pytest_env, (
            "e2e-tests pytest must set ADCP_TESTING explicitly "
            "(absent key makes the inheritance path untested / vacuous)."
        )
        assert _is_adcp_testing_true(pytest_env["ADCP_TESTING"]), (
            f"e2e-tests pytest must keep ADCP_TESTING=true (got {pytest_env['ADCP_TESTING']!r}); "
            "empty/false forces fixture cold-build under pytest-timeout."
        )
        # GHCR preference belongs on pre-start build, not verify-only pytest.
        assert "CREATIVE_AGENT_GHCR_IMAGE" not in pytest_env, (
            "CREATIVE_AGENT_GHCR_IMAGE must not sit only on verify-only pytest "
            "(move/keep it on the pre-start build step)."
        )
        extra = str(pytest_steps[0].get("with", {}).get("extra_args", ""))
        assert "--timeout=300" in extra, "e2e-tests must keep per-test --timeout=300 on test bodies."
        assert "timeout_func_only=true" in extra, (
            "e2e-tests must keep timeout_func_only=true (300s applies to bodies, not fixtures)."
        )

        pre_idx = step_names.index("Build and start E2E stack")
        pytest_idx = next(
            i
            for i, s in enumerate(steps)
            if "pytest" in str(s.get("uses", "")).lower() or "tests/e2e" in str(s.get("with", {}))
        )
        assert free_idx < pre_idx < pytest_idx, (
            f"Order must be Free disk → pre-start → pytest (got {free_idx}, {pre_idx}, {pytest_idx})."
        )

    @pytest.mark.arch_guard
    def test_e2e_compose_proxy_waits_for_adcp_healthy(self):
        """Proxy must not race adcp start_period under ``compose up --wait``.

        Regression: proxy healthcheck (no start_period) could fail with 502
        while adcp was still inside its start_period; ``up --wait`` flaked.
        """
        services = _load_e2e_compose()["services"]
        assert "adcp-server" in services and "proxy" in services, (
            "docker-compose.e2e.yml must declare adcp-server and proxy."
        )
        assert "creative-agent" in services, "docker-compose.e2e.yml must declare creative-agent."
        adcp_hc = services["adcp-server"].get("healthcheck") or {}
        proxy = services["proxy"]
        proxy_deps = proxy.get("depends_on") or {}
        proxy_hc = proxy.get("healthcheck") or {}
        creative_hc = services["creative-agent"].get("healthcheck") or {}

        # Positive concrete durations — truthy "0s" must not pass.
        assert _parse_compose_duration_seconds(adcp_hc.get("start_period")) >= 60, (
            f"adcp-server start_period must be >= 60s (got {adcp_hc.get('start_period')!r})."
        )
        assert _parse_compose_duration_seconds(adcp_hc.get("interval")) == 5, (
            f"adcp-server interval must be 5s (got {adcp_hc.get('interval')!r})."
        )
        assert int(str(adcp_hc.get("retries", 0))) >= 36, (
            f"adcp-server retries must be >= 36 (got {adcp_hc.get('retries')!r})."
        )
        # Mapping form with condition — list form ``- adcp-server`` is the race.
        assert isinstance(proxy_deps, dict), (
            f"proxy depends_on must be a mapping with condition: service_healthy (got {type(proxy_deps).__name__})."
        )
        adcp_dep = proxy_deps.get("adcp-server") or {}
        assert isinstance(adcp_dep, dict) and adcp_dep.get("condition") == "service_healthy", (
            f"proxy must depends_on adcp-server with condition: service_healthy (got {adcp_dep!r})."
        )
        assert _parse_compose_duration_seconds(proxy_hc.get("start_period")) >= 15, (
            f"proxy start_period must be >= 15s (got {proxy_hc.get('start_period')!r})."
        )
        assert _parse_compose_duration_seconds(proxy_hc.get("interval")) == 5, (
            f"proxy interval must be 5s (got {proxy_hc.get('interval')!r})."
        )
        assert int(str(proxy_hc.get("retries", 0))) >= 12, (
            f"proxy healthcheck retries must be >= 12 (got {proxy_hc.get('retries')!r})."
        )
        # compose --wait only waits services that declare healthchecks (creative-agent hard gate).
        creative_test = creative_hc.get("test")
        assert creative_test, "creative-agent healthcheck.test must be non-empty (compose --wait hard gate)."
        assert _parse_compose_duration_seconds(creative_hc.get("start_period")) > 0, (
            f"creative-agent start_period must be positive (got {creative_hc.get('start_period')!r})."
        )

    @pytest.mark.arch_guard
    def test_bdd_in_network_frees_disk_before_compose(self):
        """In-network e2e_rest must reclaim runner disk before image build.

        Regression: ``uv sync`` into ``tox_data`` hit ENOSPC on ubuntu-latest
        (image + /opt/venv + second full tox env). The job must free
        preinstalled toolchains and cap PGDATA tmpfs for the serial leg.
        """
        job = load_ci_workflow()["jobs"]["bdd-in-network"]
        steps = job.get("steps", [])
        assert steps, "bdd-in-network must declare steps (empty job is a vacuous pass)."

        step_names = [s.get("name") for s in steps]
        assert "Run BDD suite in-network" in step_names, "bdd-in-network must run ./run_all_tests.sh bdd_e2e."
        free_idx, _ = _find_free_disk_step(steps)
        _assert_free_disk_action_reclaims_runner()
        run_idx = step_names.index("Run BDD suite in-network")
        assert free_idx < run_idx, (
            "Free disk space must run before 'Run BDD suite in-network' "
            f"(found Free disk at {free_idx}, run at {run_idx})."
        )

        run_step = steps[run_idx]
        env = run_step.get("env") or {}
        assert env.get("PGDATA_TMPFS_SIZE") == "2g", (
            "bdd-in-network must set PGDATA_TMPFS_SIZE=2g "
            "(serial leg; default 10g wastes RAM — tmpfs size= is a RAM ceiling)."
        )
        assert "run_all_tests.sh bdd_e2e" in str(run_step.get("run", "")), (
            "bdd-in-network must invoke ./run_all_tests.sh bdd_e2e."
        )

    @pytest.mark.arch_guard
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
