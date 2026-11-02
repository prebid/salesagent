"""Collecting the test suite must not write anything into the working directory.

pytest imports every test module during COLLECTION, so anything a module does at
import time runs before a single test does -- and runs even for a `--collect-only`
listing, or a run filtered down to one unrelated test.

A module that writes into the CWD at that moment is a landmine rather than a
nuisance. Under docker-compose.e2e.yml the CWD is ``/app`` on a bind mount shared
by containers running as different uids, which is precisely the ownership race
that made ``logs/audit.log`` EACCES-kill entire suites at collection: whichever
uid creates a file first owns it, and the other one is locked out of it forever
after.

Scoped to ``tests/integration`` because that is where the known offender lived
and because collecting it needs no database, no Docker and under two seconds.
Breadth across the whole tree is covered statically and for free by
``test_architecture_no_import_time_fs_io.py``; this test is the behavioural half
that proves the invariant on the real collector rather than on an AST.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.arch_guard
def test_collecting_the_integration_suite_writes_nothing_into_the_cwd(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(REPO_ROOT / "tests" / "integration"),
            "--collect-only",
            "-q",
            # Deterministic: keep pytest's own cache out of the directory under test.
            "-p",
            "no:cacheprovider",
            "-p",
            "no:randomly",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=600,
    )

    assert result.returncode == 0, f"collection itself failed:\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}"
    strays = sorted(p.name for p in tmp_path.iterdir())
    assert strays == [], (
        f"collecting tests/integration created {strays} in the working directory. "
        "A test module is doing filesystem I/O at import time; move it into a test "
        "body or a fixture so it runs only when that test runs."
    )
