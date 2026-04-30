"""End-to-end regression tests for run_all_tests.sh.

The unit tests in test_run_all_tests_script.py source individual functions
via sed extraction. Those tests cannot catch a re-emergence of the original
bug class (set -e fragility from a function-tail command returning 1) at
some new site in the script. These tests invoke the actual orchestrator
end-to-end with stubbed external tools and assert the user-facing contract:
exit code and the FAILED:<suite> / ALL PASSED stdout marker.

Note: tests/CLAUDE.md mandates harness/factory usage for new tests; that
guidance applies to Python production code under src/. This module tests
a shell script and has no Python under test, so it is necessarily exempt.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "run_all_tests.sh"


def _make_stub_bin(stub_dir: Path, name: str, rc: int, body: str = "") -> None:
    """Write an executable shell stub to stub_dir/name that exits with rc.

    `body` runs before the exit and may emit JSON files into .tox/ or echo
    diagnostic text. The stub inherits the script's cwd, so relative paths
    in `body` resolve against the test's tmp dir.
    """
    path = stub_dir / name
    path.write_text(f"#!/bin/bash\n{body}\nexit {rc}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_script(
    workdir: Path,
    *,
    tox_rc: int,
    uvx_rc: int,
    write_jsons: list[str],
) -> subprocess.CompletedProcess:
    """Invoke `bash run_all_tests.sh quick` in workdir with stubbed PATH.

    The stub PATH provides:
      - tox: writes the named JSONs into .tox/, exits with tox_rc
      - uv:  no-op, exits 0 (used for `uv run python -c '...'` import check)
      - uvx: no-op, exits with uvx_rc (used for `uvx uv-secure ...`)
    Real /usr/bin and /bin remain on PATH after the stub dir for `mkdir`,
    `cp`, `date`, `ls`, `tee`, etc.
    """
    stub_dir = workdir / "_stubs"
    stub_dir.mkdir()
    json_writes = "\n".join(f'mkdir -p .tox && echo "{{}}" > .tox/{name}.json' for name in write_jsons)
    _make_stub_bin(stub_dir, "tox", tox_rc, body=json_writes)
    _make_stub_bin(stub_dir, "uv", 0, body="exit 0")
    _make_stub_bin(stub_dir, "uvx", uvx_rc)

    script_copy = workdir / "run_all_tests.sh"
    script_copy.write_bytes(SCRIPT.read_bytes())
    script_copy.chmod(0o755)

    return subprocess.run(
        ["bash", str(script_copy), "quick"],
        cwd=workdir,
        env={
            **os.environ,
            "PATH": f"{stub_dir}:{os.environ.get('PATH', '')}",
        },
        capture_output=True,
        text=True,
    )


@pytest.mark.smoke
@pytest.mark.parametrize(
    "scenario,tox_rc,uvx_rc,write_jsons,expect_rc,expect_stdout",
    [
        ("quick-all-pass", 0, 0, ["unit", "integration"], 0, "ALL PASSED"),
        ("quick-tox-fails", 1, 0, ["unit"], 1, "FAILED:tox"),
        ("quick-security-fails", 0, 1, ["unit", "integration"], 1, "FAILED:security"),
    ],
    ids=lambda x: x if isinstance(x, str) and x.startswith("quick-") else "",
)
def test_script_exit_contract(tmp_path, scenario, tox_rc, uvx_rc, write_jsons, expect_rc, expect_stdout):
    """run_all_tests.sh quick must exit expect_rc and print expect_stdout.

    Catches the regression class from #1232: any future set -e fragility
    anywhere in the quick-mode code path (collect_reports, print_summary,
    security audit, or new sites) that causes the script to exit with the
    wrong code or skip the summary phase will fail one of these scenarios.

    The original bug surfaced as quick-all-pass returning 1 instead of 0
    AND missing the ALL PASSED marker (script aborted before summary).
    Both assertions matter: rc alone misses an early-exit bug that happens
    to also fail; stdout alone misses a wrong-exit-code bug.
    """
    rc = _run_script(
        tmp_path,
        tox_rc=tox_rc,
        uvx_rc=uvx_rc,
        write_jsons=write_jsons,
    )

    assert rc.returncode == expect_rc, (
        f"scenario={scenario}\n"
        f"expected rc={expect_rc}, got rc={rc.returncode}\n"
        f"stdout: {rc.stdout!r}\nstderr: {rc.stderr!r}"
    )
    assert (
        expect_stdout in rc.stdout
    ), f"scenario={scenario}\nexpected {expect_stdout!r} in stdout\nstdout: {rc.stdout!r}\nstderr: {rc.stderr!r}"
