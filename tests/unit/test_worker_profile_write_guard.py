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

import importlib.util
import sys
from pathlib import Path

import pytest

_SOURCE = Path(__file__).resolve().parents[1] / "_worker_profile.py"


def _profile_module(monkeypatch: pytest.MonkeyPatch, out_dir: Path):
    """A genuinely separate module object pointed at *out_dir*.

    NOT `importlib.reload`. Reload re-executes into the SAME object in
    `sys.modules` -- the one `tests/conftest.py` imported at collection time and
    keeps feeding hooks for the rest of the session. It left the live `_OUT_DIR`
    pointing at this test's `tmp_path` and wiped `_marks`, `_counts` and
    `_test_seconds`, so every worker that ran one of these tests dropped out of
    the profile: measured at `-n 2` over these three files, the directory held
    only `unit-main.json` and the reader printed a plausible one-worker row and
    exited 0.

    That is the failure this file exists to prevent, committed by the file
    itself -- and silently, where the defect it grades at least crashed. Hence
    the two assertions below: the module under test must not BE the live one,
    and the live one must not have been repointed.
    """
    monkeypatch.setenv("PYTEST_WORKER_PROFILE", str(out_dir))
    spec = importlib.util.spec_from_file_location("_worker_profile_under_test", _SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.enabled, "the module reads its directory at import; the env var did not take"

    live = sys.modules["tests._worker_profile"]
    assert module is not live, "loaded the live module, not a separate copy"
    assert live._OUT_DIR != str(out_dir), "the live module was repointed at the test directory"
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
