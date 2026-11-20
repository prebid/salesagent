"""The GAM configuration preflight, through its two public surfaces: the command and run_all_tests.sh.

``scripts/gam_prerequisites_check.py --test-network`` checks the configured Google Ad Manager
test network read-only; ``run_all_tests.sh`` runs it before Docker or any suite when the run
selects a GAM leg. What is graded here needs no network: an unset setting is named, the
opt-out runs no check and says so, the network checks refuse a company that is no
advertiser and an ad unit that is not ACTIVE, and the runner stops before Docker on a
failed preflight. The live checks run where the network is configured.
"""

from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock

import pytest

from scripts.gam_prerequisites_check import SKIPPED, check_test_network
from src.adapters.gam.utils.health_check import GAMHealthChecker, HealthStatus
from tests.unit._run_all_tests_helpers import REPO_ROOT, RUNNER

_GAM_SETTINGS = (
    "GAM_SERVICE_ACCOUNT_JSON",
    "GAM_SERVICE_ACCOUNT_KEY_FILE",
    "GAM_TEST_NETWORK_CODE",
    "GAM_TEST_ADVERTISER_ID",
    "GAM_TEST_AD_UNIT_IDS",
    "GAM_TEST_PREFLIGHT_SKIP",
)


@pytest.fixture
def unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _GAM_SETTINGS:
        monkeypatch.delenv(name, raising=False)


def test_preflight_names_every_unset_setting(unconfigured: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert check_test_network(skip=False) == 1
    failed = [line.strip() for line in capsys.readouterr().out.splitlines() if "FAIL" in line]
    assert failed == [
        "FAIL settings present: GAM_SERVICE_ACCOUNT_JSON (or GAM_SERVICE_ACCOUNT_KEY_FILE)",
        "FAIL settings present: GAM_TEST_NETWORK_CODE",
        "FAIL settings present: GAM_TEST_ADVERTISER_ID",
        "FAIL settings present: GAM_TEST_AD_UNIT_IDS",
    ]


def test_preflight_names_a_key_file_that_is_not_a_file(
    unconfigured: None, monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GAM_SERVICE_ACCOUNT_KEY_FILE", str(tmp_path / "absent.json"))
    monkeypatch.setenv("GAM_TEST_NETWORK_CODE", "1")
    monkeypatch.setenv("GAM_TEST_ADVERTISER_ID", "2")
    monkeypatch.setenv("GAM_TEST_AD_UNIT_IDS", "3")
    assert check_test_network(skip=False) == 1
    assert f"FAIL settings present: GAM_SERVICE_ACCOUNT_KEY_FILE (names {tmp_path / 'absent.json'}" in (
        capsys.readouterr().out
    )


@pytest.mark.parametrize("opt_out", ["flag", "setting"])
def test_preflight_opt_out_runs_no_check_and_says_so(
    unconfigured: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], opt_out: str
) -> None:
    if opt_out == "setting":
        monkeypatch.setenv("GAM_TEST_PREFLIGHT_SKIP", "1")
    assert check_test_network(skip=opt_out == "flag") == 0
    assert capsys.readouterr().out.strip() == SKIPPED


def _checker(*, company_type: str, ad_unit_status: str) -> GAMHealthChecker:
    """A health checker whose client answers one company and one ad unit of these kinds."""
    client = MagicMock()
    services = {
        "CompanyService": MagicMock(**{"getCompaniesByStatement.return_value": {"results": [{"type": company_type}]}}),
        "UserService": MagicMock(
            **{"getCurrentUser.return_value": {"isActive": True, "id": 1, "email": "sa@example.com"}}
        ),
        "InventoryService": MagicMock(
            **{"getAdUnitsByStatement.return_value": {"results": [{"status": ad_unit_status}]}}
        ),
    }
    client.GetService.side_effect = services.__getitem__
    checker = GAMHealthChecker({})
    checker.client = client
    return checker


@pytest.mark.parametrize(
    "company_type, ad_unit_status, expected",
    [
        ("ADVERTISER", "ACTIVE", (HealthStatus.HEALTHY, HealthStatus.HEALTHY)),
        ("AGENCY", "INACTIVE", (HealthStatus.UNHEALTHY, HealthStatus.UNHEALTHY)),
    ],
)
def test_network_checks_require_an_advertiser_and_an_active_ad_unit(
    company_type: str, ad_unit_status: str, expected: tuple[HealthStatus, HealthStatus]
) -> None:
    checker = _checker(company_type=company_type, ad_unit_status=ad_unit_status)
    graded = (checker.check_permissions("2").status, checker.check_inventory_access(["3"]).status)
    assert graded == expected


def _run_runner(tmp_path, *args: str, uv_exit: int, stop_after_preflight: bool) -> tuple[int, str, str, str]:
    """Run run_all_tests.sh with ``uv`` and ``docker`` stood in; return (exit, output, uv argv, docker argv)."""
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    for tool in ("uv", "docker"):
        stub = stub_bin / tool
        stub.write_text(
            f'#!/usr/bin/env bash\necho "$*" >>"{tmp_path}/{tool}.argv"\nexit ${{STUB_{tool.upper()}_EXIT:-0}}\n'
        )
        stub.chmod(0o755)
    env = {
        **{k: v for k, v in os.environ.items() if k not in ("E2E_WORKERS", "GAM_TEST_PREFLIGHT_SKIP")},
        "PATH": f"{stub_bin}:{os.environ.get('PATH', '')}",
        "STUB_UV_EXIT": str(uv_exit),
        "STUB_DOCKER_EXIT": "0",
    }
    if stop_after_preflight:
        env["RUN_ALL_TESTS_STOP_AFTER_GAM_PREFLIGHT"] = "1"
    proc = subprocess.run(
        ["bash", str(RUNNER), *args], cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60
    )

    def argv(tool: str) -> str:
        path = tmp_path / f"{tool}.argv"
        return path.read_text() if path.exists() else ""

    return proc.returncode, proc.stdout + proc.stderr, argv("uv"), argv("docker")


def test_runner_stops_before_docker_when_the_preflight_fails(tmp_path) -> None:
    code, output, uv_argv, docker_argv = _run_runner(tmp_path, "e2e", uv_exit=1, stop_after_preflight=False)
    assert code == 1, output
    assert "scripts/gam_prerequisites_check.py --test-network" in uv_argv
    assert "GAM preflight FAILED" in output
    assert "compose" not in docker_argv, f"Docker started after a failed preflight: {docker_argv}"


def test_runner_passes_the_opt_out_flag_to_the_preflight_and_goes_on(tmp_path) -> None:
    code, output, uv_argv, _ = _run_runner(
        tmp_path, "--skip-gam-preflight", "bdd_e2e", uv_exit=0, stop_after_preflight=True
    )
    assert code == 0, output
    assert uv_argv.split().count("--skip") == 1, uv_argv


def test_runner_runs_no_preflight_for_a_run_without_a_gam_leg(tmp_path) -> None:
    code, output, uv_argv, _ = _run_runner(tmp_path, "unit,integration", uv_exit=1, stop_after_preflight=True)
    assert code == 0, output
    assert uv_argv == ""
