"""`summarise` groups by suite and keeps the xdist controller out of the averages.

Nothing executed this function for as long as it existed, and the invariants
below were not hypothetical: grouping by suite was added after two consecutive
runs of an unchanged tree reported a 10/6 and then an 8/8 split between the only
two suites whose records survived, and `collected_values` replaced a
`workers[0]["collected"]` read that answered for records it had never seen.

The controller discriminator keys on xdist behaviour under a floor-only pin
(`pytest-xdist>=3.5.0`), so an upgrade could silently restore the double-count.
These tests are where that would surface.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests._worker_profile import summarise


def _write(directory: Path, suite: str, worker: str, **over: float) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "suite": suite,
        "worker": worker,
        "collected": 100,
        "import_s": 1.0,
        "collect_s": 2.0,
        "tests_s": 10.0,
        "idle_s": 0.5,
        "worker_wall_s": 14.0,
    }
    record.update(over)
    (directory / f"{suite}-{worker}.json").write_text(json.dumps(record), encoding="utf-8")


def test_two_suites_in_one_directory_stay_separate(tmp_path: Path) -> None:
    """`tox -p` points every suite at one directory; a mean across them describes nothing."""
    _write(tmp_path, "unit", "gw0", tests_s=10.0)
    _write(tmp_path, "unit", "gw1", tests_s=10.0)
    _write(tmp_path, "integration", "gw0", tests_s=90.0)

    suites = summarise(tmp_path)["suites"]

    assert sorted(suites) == ["integration", "unit"]
    assert suites["unit"]["tests_s_total"] == 20.0
    assert suites["integration"]["tests_s_total"] == 90.0


def test_the_controller_is_excluded_from_the_worker_totals(tmp_path: Path) -> None:
    """`main` alongside workers is the xdist controller: it executes nothing."""
    _write(tmp_path, "unit", "gw0", tests_s=10.0)
    _write(tmp_path, "unit", "gw1", tests_s=10.0)
    _write(tmp_path, "unit", "main", tests_s=0.0, collected=0, worker_wall_s=25.0)

    unit = summarise(tmp_path)["suites"]["unit"]

    assert unit["workers"] == 2
    assert unit["tests_s_total"] == 20.0
    assert unit["controller_wall_s"] == 25.0
    assert "controller_collect_s" not in unit


def test_a_serial_suite_is_the_executing_process_not_a_controller(tmp_path: Path) -> None:
    """e2e, admin and ui declare no `-n`, so their single record is also named `main`.

    Reporting it as both the worker aggregate and the controller would double it
    under two names.
    """
    _write(tmp_path, "e2e", "main", tests_s=42.0)

    e2e = summarise(tmp_path)["suites"]["e2e"]

    assert e2e["workers"] == 1
    assert e2e["tests_s_total"] == 42.0
    assert "controller_wall_s" not in e2e


def test_workers_disagreeing_on_collection_report_no_single_value(tmp_path: Path) -> None:
    """Under `--dist load` every worker collects the whole suite, so these must agree."""
    _write(tmp_path, "unit", "gw0", collected=100)
    _write(tmp_path, "unit", "gw1", collected=97)

    unit = summarise(tmp_path)["suites"]["unit"]

    assert unit["collected_values"] == [97, 100]
    assert unit["collected_per_worker"] is None


def test_agreeing_workers_report_the_agreed_value(tmp_path: Path) -> None:
    _write(tmp_path, "unit", "gw0", collected=100)
    _write(tmp_path, "unit", "gw1", collected=100)

    assert summarise(tmp_path)["suites"]["unit"]["collected_per_worker"] == 100


def test_startup_is_per_worker_and_wall_is_the_slowest(tmp_path: Path) -> None:
    """Startup does not shrink with more workers; the suite waits for the slowest one."""
    _write(tmp_path, "unit", "gw0", import_s=1.0, collect_s=2.0, worker_wall_s=14.0)
    _write(tmp_path, "unit", "gw1", import_s=3.0, collect_s=2.0, worker_wall_s=20.0)

    unit = summarise(tmp_path)["suites"]["unit"]

    assert unit["startup_per_worker_s"] == 4.0
    assert unit["slowest_worker_wall_s"] == 20.0


def test_an_empty_directory_reports_no_suites(tmp_path: Path) -> None:
    assert summarise(tmp_path) == {"suites": {}}
