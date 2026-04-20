"""L0-20 obligation test: the `scripts/codemod_script_root_to_url_for.py`
codemod rewrites `{{ request.script_root }}` / `{{ script_root }}` / Flask
dotted-name `url_for()` references to the FastAPI/Starlette-native
`{{ url_for('admin_<blueprint>_<endpoint>', ...) }}` form described in
CLAUDE.md Critical Invariant #1.

Three assertions, three modes:

1. **Correctness** — applying the codemod `--write` to a COPY of the frozen
   ``input/`` fixture produces output byte-identical to ``expected/``.
2. **Idempotency** — running the codemod again on the output of (1) produces
   zero diff (the second application is a no-op).
3. **Check mode** — invoking the codemod with ``--check`` against the
   already-migrated ``expected/`` fixture exits 0 (nothing to change).

The fixture is a FROZEN golden file set (per L0-implementation-plan-v2 §7.3
RATIFIED): 5 small templates covering each rewrite pass (dynamic script_root
paths, bare script_root, static assets, Flask-dotted `url_for`, mixed).
Editing any fixture is a deliberate contract change and MUST be accompanied
by a codemod behavior update and a manifest-diff snapshot in the PR body.

The codemod is **authored at L0 but executed at L1a** — this test only
exercises the codemod against its frozen test fixtures; it does NOT touch
any real template under ``templates/``.
"""

from __future__ import annotations

import filecmp
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CODEMOD_SCRIPT = REPO_ROOT / "scripts" / "codemod_script_root_to_url_for.py"
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "codemod_golden"
INPUT_DIR = FIXTURE_ROOT / "input"
EXPECTED_DIR = FIXTURE_ROOT / "expected"


def _run_codemod(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Invoke the codemod as a subprocess using the active Python interpreter.

    A subprocess boundary ensures that the codemod's own sys.argv parsing and
    exit-code contract are exercised end-to-end — not just its Python API.
    """
    cmd = [sys.executable, str(CODEMOD_SCRIPT), *args]
    return subprocess.run(  # noqa: S603 — trusted args, controlled inputs
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )


def _copy_fixture_tree(src: Path, dst: Path) -> None:
    """Copy every ``*.html`` file under ``src`` into ``dst``, flat."""
    dst.mkdir(parents=True, exist_ok=True)
    for entry in sorted(src.iterdir()):
        if entry.is_file() and entry.suffix == ".html":
            shutil.copy2(entry, dst / entry.name)


def _diff_tree(left: Path, right: Path) -> list[str]:
    """Return a sorted list of filenames that differ between the two trees.

    Files present in only one side are reported. Files that exist in both but
    have different bytes are reported. Identical files are NOT reported.
    """
    diffs: list[str] = []
    left_names = {p.name for p in left.iterdir() if p.is_file() and p.suffix == ".html"}
    right_names = {p.name for p in right.iterdir() if p.is_file() and p.suffix == ".html"}
    for name in sorted(left_names | right_names):
        if name not in left_names:
            diffs.append(f"only in {right}: {name}")
        elif name not in right_names:
            diffs.append(f"only in {left}: {name}")
        elif not filecmp.cmp(left / name, right / name, shallow=False):
            diffs.append(f"bytes differ: {name}")
    return diffs


@pytest.fixture
def scratch_input() -> Path:
    """Yield a writable temp directory preloaded with the frozen input fixtures."""
    with TemporaryDirectory(prefix="codemod_scratch_") as tmp:
        scratch = Path(tmp) / "templates"
        _copy_fixture_tree(INPUT_DIR, scratch)
        yield scratch


class TestCodemodCorrectness:
    """First application of the codemod produces the expected golden output."""

    def test_write_produces_expected_output(self, scratch_input: Path) -> None:
        result = _run_codemod(["--write", "--templates-dir", str(scratch_input)])
        assert (
            result.returncode == 0
        ), f"codemod --write exited {result.returncode}.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        diffs = _diff_tree(scratch_input, EXPECTED_DIR)
        assert not diffs, (
            "codemod output does not match frozen golden fixture.\n"
            "Differences (scratch vs expected):\n  - " + "\n  - ".join(diffs)
        )


class TestCodemodIdempotency:
    """Running the codemod a second time on already-migrated output is a no-op."""

    def test_second_run_produces_zero_diff(self, scratch_input: Path) -> None:
        # First application: rewrite input → expected.
        first = _run_codemod(["--write", "--templates-dir", str(scratch_input)])
        assert first.returncode == 0, f"first --write failed: {first.stderr}"

        # Snapshot the first-run output into a sibling directory.
        with TemporaryDirectory(prefix="codemod_snapshot_") as snap_tmp:
            snapshot = Path(snap_tmp)
            _copy_fixture_tree(scratch_input, snapshot)

            # Second application: should be a no-op.
            second = _run_codemod(["--write", "--templates-dir", str(scratch_input)])
            assert second.returncode == 0, f"second --write failed: {second.stderr}"

            diffs = _diff_tree(scratch_input, snapshot)
            assert not diffs, (
                "codemod is NOT idempotent — second run mutated already-migrated output.\n"
                "Differences (snapshot vs scratch after second run):\n  - " + "\n  - ".join(diffs)
            )


class TestCodemodCheckMode:
    """``--check`` exits 0 on an already-migrated tree and nonzero on one needing work."""

    def test_check_on_expected_exits_zero(self) -> None:
        with TemporaryDirectory(prefix="codemod_check_expected_") as tmp:
            scratch = Path(tmp) / "templates"
            _copy_fixture_tree(EXPECTED_DIR, scratch)
            result = _run_codemod(["--check", "--templates-dir", str(scratch)])
        assert result.returncode == 0, (
            "--check on the expected (post-codemod) fixture should exit 0 (no work needed). "
            f"Got {result.returncode}.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_check_on_input_exits_nonzero(self) -> None:
        with TemporaryDirectory(prefix="codemod_check_input_") as tmp:
            scratch = Path(tmp) / "templates"
            _copy_fixture_tree(INPUT_DIR, scratch)
            result = _run_codemod(["--check", "--templates-dir", str(scratch)])
        assert (
            result.returncode != 0
        ), "--check on the pre-codemod input should fail (work is pending). Got returncode 0."


class TestCodemodDryRun:
    """``--dry-run`` never mutates disk but still reports pending rewrites."""

    def test_dry_run_does_not_write(self, scratch_input: Path) -> None:
        # Snapshot the pre-run state.
        with TemporaryDirectory(prefix="codemod_dry_snap_") as snap_tmp:
            snapshot = Path(snap_tmp)
            _copy_fixture_tree(scratch_input, snapshot)

            result = _run_codemod(["--dry-run", "--templates-dir", str(scratch_input)])
            assert result.returncode == 0, f"--dry-run failed: {result.stderr}"

            diffs = _diff_tree(scratch_input, snapshot)
            assert not diffs, (
                "--dry-run mutated files on disk — the dry-run contract is broken.\n"
                "Differences:\n  - " + "\n  - ".join(diffs)
            )
