"""A suite whose only problem is `error` must not read as clean.

pytest counts an item that died in SETUP or TEARDOWN under ``summary.error``,
never under ``summary.failed``. Two consecutive full in-network runs of bdd_e2e
therefore reported ``failed 0`` while tox exited 1, and the rotating fixture
deadlock behind it (#2048) stayed invisible to anyone reading the
failure count. ``scripts/report_suite_failures.py`` is the predicate that names
those suites; both runner paths call it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from report_suite_failures import failure_report, main  # noqa: E402

pytestmark = [pytest.mark.unit]


def _write(results_dir: Path, name: str, **summary: object) -> None:
    (results_dir / name).write_text(json.dumps({"summary": summary}), encoding="utf-8")


def test_errors_only_suite_is_reported(tmp_path: Path):
    """The exact prkv.48 shape: failed=0, error=2, and it is NOT clean."""
    _write(tmp_path, "bdd_e2e.json", passed=610, failed=0, error=2)

    report = failure_report(str(tmp_path))

    assert len(report) == 1, report
    assert "bdd_e2e.json" in report[0]
    assert "error=2" in report[0]
    assert "0 failed" in report[0], "an errors-only suite must say why its failure count is misleading"


def test_clean_suites_produce_no_report(tmp_path: Path):
    _write(tmp_path, "unit.json", passed=5846, failed=0, error=0)
    _write(tmp_path, "integration.json", passed=900, failed=0)

    assert failure_report(str(tmp_path)) == []
    assert main(["report_suite_failures.py", str(tmp_path)]) == 0


def test_failures_and_errors_are_both_counted(tmp_path: Path):
    _write(tmp_path, "admin.json", passed=10, failed=3, error=1)
    _write(tmp_path, "unit.json", passed=10, failed=0, error=0)

    report = failure_report(str(tmp_path))

    assert len(report) == 1, report
    assert "failed=3" in report[0] and "error=1" in report[0]
    # Only the errors-only case gets the explanatory tail; a suite with real
    # failures is already legible from its failure count.
    assert "0 failed" not in report[0]


def test_unreadable_report_is_a_finding(tmp_path: Path):
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")

    report = failure_report(str(tmp_path))

    assert len(report) == 1, report
    assert "unreadable" in report[0]


def test_main_exits_nonzero_when_any_suite_is_dirty(tmp_path: Path, capsys):
    _write(tmp_path, "bdd_e2e.json", passed=610, failed=0, error=1)

    assert main(["report_suite_failures.py", str(tmp_path)]) == 1
    assert "bdd_e2e.json" in capsys.readouterr().out
