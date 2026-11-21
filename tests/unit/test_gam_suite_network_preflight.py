"""The GAM configuration preflight, through its two public surfaces: the command and run_all_tests.sh.

``scripts/gam_prerequisites_check.py --suite-network`` checks the configured Google Ad Manager
suite network read-only; ``run_all_tests.sh`` runs it before Docker or any suite when the run
selects a GAM leg. What is graded here needs no network: an unset setting is named, the
opt-out runs no check and says so, the network checks refuse a company that is no
advertiser and an ad unit that is not ACTIVE, and the runner stops before Docker on a
failed preflight. The live checks run where the network is configured.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from scripts.gam_prerequisites_check import SKIPPED, check_suite_network
from src.adapters.gam.utils.health_check import GAMHealthChecker, HealthStatus
from tests.helpers.bash_stubs import run_bash_with_stubs
from tests.unit._run_all_tests_helpers import REPO_ROOT, RUNNER

_GAM_SETTINGS = (
    "GAM_SERVICE_ACCOUNT_JSON",
    "GAM_SERVICE_ACCOUNT_KEY_FILE",
    "GAM_SUITE_NETWORK_CODE",
    "GAM_SUITE_ADVERTISER_ID",
    "GAM_SUITE_AD_UNIT_IDS",
    "GAM_SUITE_PREFLIGHT_SKIP",
)


@pytest.fixture
def unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _GAM_SETTINGS:
        monkeypatch.delenv(name, raising=False)


def _configure(monkeypatch: pytest.MonkeyPatch, tmp_path, *names: str) -> None:
    """Set the named GAM settings, each to a value of its kind; the credential is a key file."""
    values = {
        "GAM_SERVICE_ACCOUNT_KEY_FILE": str(tmp_path / "key.json"),
        "GAM_SUITE_NETWORK_CODE": "1",
        "GAM_SUITE_ADVERTISER_ID": "2",
        "GAM_SUITE_AD_UNIT_IDS": "3,4",
    }
    (tmp_path / "key.json").write_text("{}")
    for name in names:
        monkeypatch.setenv(name, values[name])


_EVERY_SETTING = (
    "GAM_SERVICE_ACCOUNT_KEY_FILE",
    "GAM_SUITE_NETWORK_CODE",
    "GAM_SUITE_ADVERTISER_ID",
    "GAM_SUITE_AD_UNIT_IDS",
)


def test_the_suite_network_is_none_partial_or_full(
    unconfigured: None, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """All or nothing: no GAM setting is no network; some is a refusal naming the rest; all is the network."""
    from src.core.config import GamSuiteNetworkUnconfigured, ToolingSettings

    assert ToolingSettings().gam_suite_network() is None

    _configure(monkeypatch, tmp_path, "GAM_SUITE_NETWORK_CODE")
    with pytest.raises(GamSuiteNetworkUnconfigured) as partial:
        ToolingSettings().gam_suite_network()
    assert partial.value.missing == (
        "GAM_SERVICE_ACCOUNT_JSON (or GAM_SERVICE_ACCOUNT_KEY_FILE)",
        "GAM_SUITE_ADVERTISER_ID",
        "GAM_SUITE_AD_UNIT_IDS",
    )

    _configure(monkeypatch, tmp_path, *_EVERY_SETTING)
    network = ToolingSettings().gam_suite_network()
    assert network is not None
    assert (network.network_code, network.advertiser_id, network.ad_unit_ids) == ("1", "2", ("3", "4"))


def test_preflight_says_not_configured_and_passes_with_no_gam_setting(
    unconfigured: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check_suite_network(skip=False) == 0
    assert capsys.readouterr().out.strip() == "GAM: not configured"


def test_preflight_names_every_unset_setting_of_a_partial_configuration(
    unconfigured: None, monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure(monkeypatch, tmp_path, "GAM_SUITE_AD_UNIT_IDS")
    assert check_suite_network(skip=False) == 1
    failed = [line.strip() for line in capsys.readouterr().out.splitlines() if "FAIL" in line]
    assert failed == [
        "FAIL settings present: GAM_SERVICE_ACCOUNT_JSON (or GAM_SERVICE_ACCOUNT_KEY_FILE)",
        "FAIL settings present: GAM_SUITE_NETWORK_CODE",
        "FAIL settings present: GAM_SUITE_ADVERTISER_ID",
    ]


def test_preflight_records_the_configured_network(
    unconfigured: None, monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A full configuration is recorded by its network code, before any check of it (this key does not parse)."""
    _configure(monkeypatch, tmp_path, *_EVERY_SETTING)
    monkeypatch.setenv("ENCRYPTION_KEY", "PEg0SNGQyvzi4Nft-ForSzK8AGXyhRtql1MgoUsfUHk=")
    assert check_suite_network(skip=False) == 1
    assert "GAM: configured, network 1" in capsys.readouterr().out.splitlines()


def test_preflight_names_a_key_file_that_is_not_a_file(
    unconfigured: None, monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GAM_SERVICE_ACCOUNT_KEY_FILE", str(tmp_path / "absent.json"))
    monkeypatch.setenv("GAM_SUITE_NETWORK_CODE", "1")
    monkeypatch.setenv("GAM_SUITE_ADVERTISER_ID", "2")
    monkeypatch.setenv("GAM_SUITE_AD_UNIT_IDS", "3")
    assert check_suite_network(skip=False) == 1
    assert f"FAIL settings present: GAM_SERVICE_ACCOUNT_KEY_FILE (names {tmp_path / 'absent.json'}" in (
        capsys.readouterr().out
    )


@pytest.mark.parametrize("opt_out", ["flag", "setting"])
def test_preflight_opt_out_runs_no_check_and_says_so(
    unconfigured: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], opt_out: str
) -> None:
    if opt_out == "setting":
        monkeypatch.setenv("GAM_SUITE_PREFLIGHT_SKIP", "1")
    assert check_suite_network(skip=opt_out == "flag") == 0
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


def _run_runner(
    tmp_path, *args: str, uv_exit: int, gam_setting: bool, stop_after_preflight: bool = True
) -> tuple[int, str, str, str]:
    """Run run_all_tests.sh with ``uv`` and ``docker`` stood in; return (exit, output, uv argv, docker argv).

    The environment holds no GAM_* variable but, with *gam_setting*, ``GAM_SUITE_NETWORK_CODE``.
    ``unset`` is applied after ``env``, so it names every inherited GAM_* variable except the one
    set here: the in-network ``tests`` container exports each GAM_* setting, empty when unset.
    """
    gam = {"GAM_SUITE_NETWORK_CODE": "1"} if gam_setting else {}
    proc = run_bash_with_stubs(
        [str(RUNNER), *args],
        stub_dir=tmp_path / "bin",
        stubs={
            tool: f'#!/usr/bin/env bash\necho "$*" >>"$STUB_ARGV_DIR/{tool}.argv"\nexit ${{STUB_{tool.upper()}_EXIT:-0}}\n'
            for tool in ("uv", "docker")
        },
        env={
            "STUB_ARGV_DIR": str(tmp_path),
            "STUB_UV_EXIT": str(uv_exit),
            **({"RUN_ALL_TESTS_STOP_AFTER_GAM_PREFLIGHT": "1"} if stop_after_preflight else {}),
            **gam,
        },
        unset=["E2E_WORKERS", *(name for name in os.environ if name.upper().startswith("GAM_") and name not in gam)],
        cwd=REPO_ROOT,
    )

    def argv(tool: str) -> str:
        path = tmp_path / f"{tool}.argv"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    return proc.returncode, proc.stdout + proc.stderr, argv("uv"), argv("docker")


def test_runner_stops_before_docker_when_the_preflight_fails(tmp_path) -> None:
    code, output, uv_argv, docker_argv = _run_runner(
        tmp_path, "e2e", uv_exit=1, gam_setting=True, stop_after_preflight=False
    )
    assert code == 1, output
    assert "scripts/gam_prerequisites_check.py --suite-network" in uv_argv
    assert "GAM preflight FAILED" in output
    assert "compose" not in docker_argv, f"Docker started after a failed preflight: {docker_argv}"


def test_runner_passes_the_opt_out_flag_to_the_preflight_and_goes_on(tmp_path) -> None:
    code, output, uv_argv, _ = _run_runner(tmp_path, "--skip-gam-preflight", "bdd_e2e", uv_exit=0, gam_setting=True)
    assert code == 0, output
    assert uv_argv.split().count("--skip") == 1, uv_argv


@pytest.mark.parametrize(
    "suites, gam_setting, statement",
    [
        ("unit,integration", True, "not checked, this run selects no GAM leg (e2e, bdd, bdd_e2e)"),
        # The CI in-network job: a GAM leg, no GAM setting, and no uv installed.
        ("bdd_e2e", False, "not configured"),
    ],
    ids=["no-gam-leg", "no-gam-setting"],
)
def test_runner_runs_no_preflight_without_a_gam_leg_or_a_gam_setting(
    tmp_path, suites: str, gam_setting: bool, statement: str
) -> None:
    """Neither needs the preflight, so neither needs uv: the runner states why and goes on."""
    code, output, uv_argv, _ = _run_runner(tmp_path, suites, uv_exit=1, gam_setting=gam_setting)
    assert code == 0, output
    assert uv_argv == ""
    assert f"GAM network: {statement}" in output.splitlines(), output
