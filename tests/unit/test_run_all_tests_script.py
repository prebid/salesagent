"""Regression tests for run_all_tests.sh.

The script's `collect_reports` and `print_summary` functions interact with
`set -eo pipefail` in subtle ways: a function whose last command is a `for`
loop whose last iteration's `[ -f ] && cp` returns 1 will return 1 itself,
tripping `set -e` at the standalone call site even though the function did
the right thing (skipped a missing file).

These tests source each function from the production script via `sed`
extraction and exercise it in a tmp dir under the same shell options as
the live script.
"""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "run_all_tests.sh"


def _setup_tox_dir(workdir: Path, names: list[str]) -> Path:
    """Create a fake .tox/ in workdir with empty JSON files for the named envs."""
    tox_dir = workdir / ".tox"
    tox_dir.mkdir(exist_ok=True)
    for name in names:
        (tox_dir / f"{name}.json").write_text("{}")
    return tox_dir


def _run_extracted_function(func_name: str, workdir: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    """Extract a shell function from run_all_tests.sh and invoke it under set -eo pipefail."""
    bash = textwrap.dedent(
        """
        set -eo pipefail
        eval "$(sed -n '/^{name}() {{/,/^}}/p' {script})"
        {name}
        """
    ).format(name=func_name, script=SCRIPT)
    return subprocess.run(
        ["bash", "-c", bash],
        cwd=workdir,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
    )


@pytest.mark.smoke
def test_collect_reports_returns_zero_when_only_unit_integration_jsons_exist(tmp_path):
    """Quick mode produces only unit/integration JSONs; the function must still return 0.

    Before the fix, the trailing loop iteration's `[ -f .tox/X.json ]` returned 1,
    making the function return 1, which tripped `set -e` at the standalone call
    site and aborted the script with exit 1 before the summary phase.
    """
    _setup_tox_dir(tmp_path, ["unit", "integration"])
    results = tmp_path / "results"

    rc = _run_extracted_function("collect_reports", tmp_path, env={"RESULTS_DIR": str(results)})

    assert rc.returncode == 0, f"rc={rc.returncode}\nstdout: {rc.stdout!r}\nstderr: {rc.stderr!r}"
    assert (results / "unit.json").exists()
    assert (results / "integration.json").exists()


@pytest.mark.smoke
def test_collect_reports_returns_zero_when_no_jsons_exist(tmp_path):
    """Tox crashed before writing any reports — the function must still return 0."""
    (tmp_path / ".tox").mkdir()
    results = tmp_path / "results"

    rc = _run_extracted_function("collect_reports", tmp_path, env={"RESULTS_DIR": str(results)})

    assert rc.returncode == 0, f"rc={rc.returncode}\nstdout: {rc.stdout!r}\nstderr: {rc.stderr!r}"


@pytest.mark.smoke
def test_collect_reports_copies_all_jsons_when_all_present(tmp_path):
    """Happy path: every tox env wrote a report; the function copies all six."""
    all_envs = ["unit", "integration", "e2e", "admin", "bdd", "ui"]
    _setup_tox_dir(tmp_path, all_envs)
    results = tmp_path / "results"

    rc = _run_extracted_function("collect_reports", tmp_path, env={"RESULTS_DIR": str(results)})

    assert rc.returncode == 0, f"rc={rc.returncode}\nstdout: {rc.stdout!r}\nstderr: {rc.stderr!r}"
    for name in all_envs:
        assert (results / f"{name}.json").exists()


@pytest.mark.smoke
def test_print_summary_returns_zero_when_failures_empty(tmp_path):
    """When all suites passed, print_summary must signal success."""
    results = tmp_path / "results"
    results.mkdir()
    (results / "unit.json").write_text("{}")

    rc = _run_extracted_function(
        "print_summary",
        tmp_path,
        env={
            "RESULTS_DIR": str(results),
            "FAILURES": "",
            "GREEN": "",
            "RED": "",
            "NC": "",
        },
    )

    assert rc.returncode == 0, f"rc={rc.returncode}\nstdout: {rc.stdout!r}\nstderr: {rc.stderr!r}"
    assert "ALL PASSED" in rc.stdout


@pytest.mark.smoke
def test_print_summary_returns_one_when_failures_set(tmp_path):
    """When any suite failed, print_summary must propagate that to the caller's set -e."""
    results = tmp_path / "results"
    results.mkdir()

    rc = _run_extracted_function(
        "print_summary",
        tmp_path,
        env={
            "RESULTS_DIR": str(results),
            "FAILURES": "tox",
            "GREEN": "",
            "RED": "",
            "NC": "",
        },
    )

    assert rc.returncode == 1, f"rc={rc.returncode}\nstdout: {rc.stdout!r}\nstderr: {rc.stderr!r}"
    assert "FAILED:tox" in rc.stdout
