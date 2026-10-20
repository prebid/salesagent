"""The profile reporter gates on one thing: workers disagreeing about collection.

Under `--dist load` every worker collects the whole suite, so the counts must
agree. A disagreement means the workers did not see the same suite, which is a
collection or sharding defect. That is a self-contradiction in the data, so it
needs no baseline — unlike a startup or idle threshold, which the reporter
deliberately does not set.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.ci.report_worker_profile import main


def _record(path: Path, worker: str, collected: int) -> None:
    path.write_text(
        json.dumps(
            {
                "suite": "probe",
                "worker": worker,
                "collected": collected,
                "import_s": 0.1,
                "collect_s": 0.1,
                "tests_s": 0.8,
                "idle_s": 0.0,
                "worker_wall_s": 1.0,
            }
        ),
        encoding="utf-8",
    )


def test_agreeing_workers_pass(tmp_path: Path) -> None:
    _record(tmp_path / "probe-gw0.json", "gw0", 6)
    _record(tmp_path / "probe-gw1.json", "gw1", 6)
    assert main(str(tmp_path)) == 0


def test_disagreeing_workers_fail(tmp_path: Path) -> None:
    """The one condition the reporter exists to catch."""
    _record(tmp_path / "probe-gw0.json", "gw0", 6)
    _record(tmp_path / "probe-gw1.json", "gw1", 3)
    assert main(str(tmp_path)) == 1


def test_a_missing_profile_is_not_a_failure(tmp_path: Path) -> None:
    """The profile is a diagnostic; its absence must not fail a run."""
    assert main(str(tmp_path / "never-written")) == 0
