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
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER = _REPO_ROOT / "run_all_tests.sh"
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
