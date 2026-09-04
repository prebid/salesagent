"""The profile must never change the outcome of the run it measures.

`tests/_worker_profile.py` is default-on for the in-network path
(`run_all_tests.sh`), which CI takes twice. Before the guard, an unwritable
profile path raised out of `pytest_sessionfinish`: a session with every test
passing and a clean json-report exited 1 with no summary line.

These call `on_session_finish()` directly, because "this function raises" is the
whole defect. Driving a nested pytest session to observe an exit code would cost
a subprocess to learn the same fact.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def _profile_module(monkeypatch: pytest.MonkeyPatch, out_dir: Path):
    """A fresh, enabled copy of the module pointed at *out_dir*."""
    monkeypatch.setenv("PYTEST_WORKER_PROFILE", str(out_dir))
    module = importlib.reload(importlib.import_module("tests._worker_profile"))
    assert module.enabled, "the module reads its directory at import; the reload did not take"
    return module


def test_unwritable_parent_does_not_raise(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """A read-only parent is reported, not raised."""
    parent = Path(tmp_path) / "readonly"
    parent.mkdir()
    parent.chmod(0o500)
    module = _profile_module(monkeypatch, parent / "profile")
    try:
        module.on_session_finish()
    finally:
        parent.chmod(0o700)


def test_path_occupied_by_a_regular_file_does_not_raise(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file where the directory should be is reported, not raised."""
    occupied = Path(tmp_path) / "profile"
    occupied.write_text("not a directory", encoding="utf-8")
    module = _profile_module(monkeypatch, occupied)
    module.on_session_finish()


def test_a_writable_directory_still_gets_the_record(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard must not swallow the success path it wraps."""
    out = Path(tmp_path) / "profile"
    module = _profile_module(monkeypatch, out)
    module.on_session_finish()
    assert list(out.glob("*.json")), "the guard suppressed a write that should have succeeded"


class _Report:
    """The two attributes `record_test_duration` reads off a pytest report."""

    def __init__(self, duration: float, worker_id: str | None = None) -> None:
        self.duration = duration
        if worker_id is not None:
            self.worker_id = worker_id


def test_a_reemitted_worker_report_is_not_counted(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under -n the controller re-emits every worker's report.

    Counting those made the controller's record carry the suite's whole test
    time with `collected: 0`, so the busy-vs-wall ratio read as 1.0. xdist
    attaches `worker_id` when it serializes a report to the controller, and
    attaches it nowhere else — measured: present on the controller under -n,
    absent on a worker's own report and absent in a serial run.
    """
    module = _profile_module(monkeypatch, Path(tmp_path) / "profile")
    module.record_test_duration(_Report(1.5, worker_id="gw0"))
    assert module._test_seconds == 0.0, "the controller counted a worker's work as its own"


def test_this_process_own_report_is_counted(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """The filter must not drop the reports the profile exists to measure."""
    module = _profile_module(monkeypatch, Path(tmp_path) / "profile")
    module.record_test_duration(_Report(1.5))
    module.record_test_duration(_Report(0.5))
    assert module._test_seconds == 2.0
