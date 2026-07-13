"""Contract test for run_all_tests.sh argument handling.

Guards PR #1420 review finding #2: the in-network runner replaced the
historical MODE contract (``ci`` default / ``quick`` / targeted) with a raw tox
suite-list, so ``./run_all_tests.sh ci`` became ``tox -e ci`` ->
"provided environments not found: ci", breaking Makefile quality-full/test-full
and the documented commands.

Asserts the resolved contract via the ``RUN_ALL_TESTS_RESOLVE_ONLY`` seam so no
Docker stack is needed.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER = _REPO_ROOT / "run_all_tests.sh"
_HOST_RUNNER = _REPO_ROOT / "run_all_tests_host.sh"
_TOX_INI = _REPO_ROOT / "tox.ini"
_COMPOSE_FILE = _REPO_ROOT / "docker-compose.e2e.yml"
_ALL_SUITES = "unit,integration,bdd,admin,e2e,ui"


def _resolve(*args: str) -> str:
    proc = subprocess.run(
        ["bash", str(_RUNNER), *args],
        cwd=_REPO_ROOT,
        env={**os.environ, "RUN_ALL_TESTS_RESOLVE_ONLY": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"resolve-only exited {proc.returncode}: {proc.stderr}"
    return proc.stdout.strip()


@pytest.mark.parametrize(
    "args, expected",
    [
        ([], f"RESOLVED suites={_ALL_SUITES}"),  # bare == full in-network run
        (["ci"], f"RESOLVED suites={_ALL_SUITES}"),  # ci is the explicit alias (was broken)
        (["quick"], "RESOLVED delegate-host: quick"),  # no-Docker fast path -> host runner
        (["unit,integration"], "RESOLVED suites=unit,integration"),  # explicit tox env list
        (
            ["ci", "tests/integration/test_x.py", "-k", "foo"],  # targeted form -> host runner
            "RESOLVED delegate-host: ci tests/integration/test_x.py -k foo",
        ),
    ],
)
def test_run_all_tests_arg_contract(args, expected):
    assert _resolve(*args) == expected


def _tox_env_list() -> set[str]:
    """Canonical parallel suite set from tox.ini (coverage runs separately)."""
    m = re.search(r"^env_list\s*=\s*(.+)$", _TOX_INI.read_text(), re.MULTILINE)
    assert m, "tox.ini has no env_list"
    return {s.strip() for s in m.group(1).split(",") if s.strip()}


def _runner_all_suites() -> set[str]:
    m = re.search(r'ALL_SUITES="([^"]+)"', _RUNNER.read_text())
    assert m, "run_all_tests.sh has no ALL_SUITES"
    return {s.strip() for s in m.group(1).split(",") if s.strip()}


def _host_runner_collect_suites() -> set[str]:
    m = re.search(r"for name in ([a-z][a-z0-9 ]+?); do", _HOST_RUNNER.read_text())
    assert m, "run_all_tests_host.sh collect_reports loop not found"
    return set(m.group(1).split())


def test_six_suite_list_is_single_sourced():
    """All four materializations of the suite list must match tox.ini env_list.

    PR #1420 review nit: the six-suite list is duplicated in run_all_tests.sh,
    run_all_tests_host.sh, tox.ini, and this test's _ALL_SUITES, with nothing
    tying them — a 7th tox env added without updating the runners would silently
    not run in-network. Compare as sets (parallel-run order vs report-collection
    order legitimately differ); tox.ini env_list is the single source.
    """
    canonical = _tox_env_list()
    assert _runner_all_suites() == canonical, "run_all_tests.sh ALL_SUITES drifted from tox env_list"
    assert _host_runner_collect_suites() == canonical, "run_all_tests_host.sh collect_reports drifted from tox env_list"
    assert {s.strip() for s in _ALL_SUITES.split(",")} == canonical, "_ALL_SUITES constant drifted from tox env_list"


def test_e2e_test_control_secret_reaches_server_and_bdd_tox_env():
    """The per-run secret must survive both compose and tox isolation layers."""
    token_name = "ADCP_TEST_CONTROL_TOKEN"
    runner_text = _RUNNER.read_text()
    compose_text = _COMPOSE_FILE.read_text()
    tox_text = _TOX_INI.read_text()
    pass_env = tox_text.split("pass_env =", 1)[1].split("setenv =", 1)[0]

    assert token_name in runner_text, "run_all_tests.sh does not generate the E2E control secret"
    assert f'if [ -z "${{{token_name}:-}}" ]' not in runner_text, "runner must not reuse a caller's stale secret"
    assert compose_text.count(f"{token_name}:") >= 2, "compose must pass the secret to server and test runner"
    assert token_name in pass_env, "tox strips the E2E control secret before the BDD process starts"
