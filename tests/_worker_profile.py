"""Opt-in per-worker timing profile, to explain where a parallel run's wall clock goes.

Motivation: after #2006 made the unit suite parallel, the largest remaining cost
in every suite was NOT test execution. Derived from the run reports, per-worker
"busy" time (the sum of each test's setup+call+teardown) accounts for only
42--73% of a suite's wall clock:

    suite            box   wall     busy/worker   unaccounted
    bdd_inprocess    A     640.4s   392.5s        247.9s  (39%)
    bdd_inprocess    B     652.4s   401.4s        251.0s  (38%)
    bdd_e2e          B     353.3s   147.4s        205.9s  (58%)
    unit             B     215.2s    99.7s        115.5s  (54%)

That residual is the thing to attack, but "wall minus busy" is a subtraction, not
a measurement -- it lumps interpreter start, application import, collection and
end-of-run scheduling idle into one number and cannot say which dominates. Since
every xdist worker imports and collects INDEPENDENTLY, the residual plausibly
scales with worker count, which would mean adding workers is negative-yield past
some point. That is a claim worth measuring rather than assuming.

This plugin measures it directly, per worker:

    import    plugin import -> pytest_configure   (interpreter + plugin/conftest
                                                   imports, incl. the app)
    collect   collection start -> finish          (+ the item count)
    idle      time inside the test loop NOT spent in a test  (scheduling gaps,
                                                   and the tail while other
                                                   workers still have work)
    tests     sum of this worker's own test durations

Off unless ``PYTEST_WORKER_PROFILE`` names a directory, and when off costs one
environment lookup at import. Each process writes ``<dir>/<suite>-<workerid>.json``
-- the suite qualifies the name because ``tox -p`` points every suite at ONE
directory and xdist worker ids restart at ``gw0`` in each of them.
``summarise()`` returns one per-phase aggregate per suite.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Import time is the earliest point this plugin can observe. Everything before it
# -- interpreter boot, pytest's own bootstrap, and every plugin imported ahead of
# the rootdir conftest -- is invisible to every number below, including
# `worker_wall_s`, which starts here rather than at process start.
#
# So a reader comparing `worker_wall_s` against the suite's wall clock must not
# read the remainder as scheduling idle: it also holds the startup this module
# cannot see. Measuring it would need psutil (not a dependency) or /proc (Linux
# only, so absent on a developer's Mac and present on the box) -- a number that
# exists on one machine and not the other is worse than a stated gap.
_T_IMPORT = time.time()

_OUT_DIR = os.environ.get("PYTEST_WORKER_PROFILE")

# The suite a record came from. Without it, every suite's gw0 is the same file:
# xdist worker ids restart at gw0 in each suite, `tox -p` runs the suites
# concurrently, and run_all_tests.sh points all of them at ONE profile directory,
# so the suites overwrite each other and the survivor is whichever finished last.
# Measured on two consecutive runs of an unchanged tree, the split between the
# two suites that happened to survive was 10/6 and then 8/8 -- the same run,
# profiled twice, disagreeing with itself.
#
# tox sets TOX_ENV_NAME for the commands it runs, and sets it AFTER pass_env and
# set_env are applied, so it needs no passenv entry and no testenv can shadow it.
# Outside tox -- a hand-run pytest with PYTEST_WORKER_PROFILE exported -- there
# is no suite to name and only one pytest writes the directory, so a constant is
# enough. It is named here rather than left implicit because it is the one place
# where two concurrent writers could still land on the same path.
_ADHOC_SUITE = "adhoc"

# Records written before the suite field existed. Distinct from `adhoc`, which is
# a real hand-run process that simply had no tox env to name.
_UNATTRIBUTED_SUITE = "<unattributed>"

_marks: dict[str, float] = {}
_counts: dict[str, int] = {}
_test_seconds = 0.0

enabled = bool(_OUT_DIR)


def _mark(name: str) -> None:
    _marks[name] = time.time()


def on_collection_start() -> None:
    if enabled:
        _mark("collect_start")


def on_collection_finish(item_count: int) -> None:
    if not enabled:
        return
    _mark("collect_finish")
    _counts["collected"] = item_count


def on_loop_start() -> None:
    if enabled:
        _mark("loop_start")


def record_test_duration(report: Any) -> None:
    """Accumulate THIS process's own execution time, phase by phase.

    Takes the report rather than a duration so the rule about whose work counts
    lives here, with the record it shapes, instead of in the conftest hook.

    Under ``-n`` the controller re-emits every worker's report through
    ``pytest_runtest_logreport``. Counting those made the controller's record
    report the suite's entire test time while carrying ``collected: 0`` and
    ``import_s: 0.0`` -- so a reader comparing per-worker busy time against
    suite wall time saw a ratio of 1.0 and read fully saturated workers.
    Measured on 105 tests: at ``-n 4`` the four workers summed to 2.191s and
    ``unit-main`` also reported 2.191s.

    ``worker_id`` is the exact discriminator, verified rather than assumed --
    xdist attaches it when serializing a report to the controller, so:

    ======================  ===========
    context                 worker_id
    ======================  ===========
    controller, parallel    present
    worker, own report      absent
    serial run              absent
    ======================  ===========

    Dropping reports that carry one keeps a worker's own work and a serial
    run's, and drops only the duplicates.
    """
    if not enabled:
        return
    if getattr(report, "worker_id", None) is not None:
        return

    global _test_seconds
    _test_seconds += getattr(report, "duration", 0.0) or 0.0


def on_session_finish() -> None:
    if not enabled:
        return
    _mark("finish")
    out = Path(_OUT_DIR)

    suite = os.environ.get("TOX_ENV_NAME") or _ADHOC_SUITE
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    collect_start = _marks.get("collect_start", _T_IMPORT)
    collect_finish = _marks.get("collect_finish", collect_start)
    loop_start = _marks.get("loop_start", collect_finish)
    finish = _marks["finish"]

    # `idle` is the residual inside the test loop: scheduling gaps plus the tail
    # this worker spends with nothing left to run while others finish. It is the
    # part a better DISTRIBUTION fixes; `import`/`collect` are the parts only a
    # cheaper startup fixes. Keeping them apart is the whole point.
    record = {
        "suite": suite,
        "worker": worker,
        "collected": _counts.get("collected", 0),
        "import_s": round(collect_start - _T_IMPORT, 3),
        "collect_s": round(collect_finish - collect_start, 3),
        "tests_s": round(_test_seconds, 3),
        # No max(0.0, ...) clamp. The residual cannot legitimately go negative:
        # a worker runs its tests one at a time, so their summed durations fit
        # inside the loop that contains them. Flooring it would therefore only
        # ever hide an accounting defect -- the controller double-count above was
        # exactly such a defect, and the clamp turned it into a plausible 0.0
        # rather than the negative number that would have exposed it.
        "idle_s": round((finish - loop_start) - _test_seconds, 3),
        "worker_wall_s": round(finish - _T_IMPORT, 3),
    }
    # A diagnostic never changes the outcome of the thing it measures. Both the
    # mkdir and the write raise on an unwritable parent, on a path occupied by a
    # regular file, and on a full disk, and an exception out of
    # pytest_sessionfinish is an unhandled error: measured, a session with all
    # six tests passing and a clean json-report exited 1 with no summary line.
    # The profile is default-on for the in-network path, which CI takes twice,
    # so this is the ordinary case rather than a corner.
    #
    # OSError, not Exception: PermissionError, FileExistsError, IsADirectoryError
    # and ENOSPC are all subclasses of it, while a defect in the record dict
    # above stays loud instead of being hidden by the guard that tolerates a
    # read-only disk.
    try:
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{record['suite']}-{record['worker']}.json").write_text(json.dumps(record), encoding="utf-8")
    except OSError as exc:
        print(f"[worker-profile] could not write the profile to {out}: {exc}", file=sys.stderr)


def summarise(directory: str | Path) -> dict[str, Any]:
    """Aggregate a profile directory into one entry per suite.

    A directory holds every suite in the run, not one. ``tox -p`` runs the suites
    concurrently against a single ``PYTEST_WORKER_PROFILE``, and they collect
    different item counts and run for different lengths, so a single mean across
    all of them describes no process that actually ran. Group first, then average
    within a group.
    """
    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(directory).glob("*.json"))]
    if not records:
        return {"suites": {}}

    # A record with no `suite` predates this field, so it came from a directory
    # written when every suite shared one filename. It is NOT an ad-hoc run, and
    # bucketing it as one would silently merge unattributable records with
    # attributable ones. Give it a bucket that names what it actually is.
    by_suite: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_suite.setdefault(record.get("suite") or _UNATTRIBUTED_SUITE, []).append(record)
    return {"suites": {suite: _summarise_suite(rs) for suite, rs in sorted(by_suite.items())}}


def _summarise_suite(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate one suite's records.

    Under xdist the CONTROLLER writes a record too (``main``), and it must not be
    averaged with the workers: it collects like they do but executes nothing, so
    folding it in would understate per-worker test time and overstate idle.

    A suite with NO ``-n`` -- e2e, admin and ui declare none -- runs as a single
    process with ``PYTEST_XDIST_WORKER`` unset, so its one record is also named
    ``main``. That record is the executing process, not a controller. Grouping by
    suite made this the normal case for three of the seven buckets, where before
    it was folded in with the parallel suites and never appeared alone; reporting
    the same record as both the worker aggregate and the controller would double
    it under two names.
    """
    parallel = [r for r in records if r["worker"] != "main"]
    workers = parallel or records
    controller = next((r for r in records if r["worker"] == "main"), None) if parallel else None
    n = len(workers)

    def total(key: str) -> float:
        return round(sum(r[key] for r in workers), 1)

    # Under `--dist load` every worker collects the WHOLE suite, so these agree
    # and the set holds one value. Reporting the set rather than the first
    # record's value means a disagreement shows up as a disagreement. Indexing
    # the first record answered for records it had never read, which is how a
    # directory holding two suites reported one suite's count for both of them.
    #
    # `collected_values` always carries the set, so its type never depends on the
    # data; `collected_per_worker` is the single agreed value, or None when the
    # workers disagree and there is no such number to report.
    collected = sorted({r["collected"] for r in workers})

    summary = {
        "workers": n,
        "collected_values": collected,
        "collected_per_worker": collected[0] if len(collected) == 1 else None,
        # Summed across workers: what the machine actually spent on each phase.
        "import_s_total": total("import_s"),
        "collect_s_total": total("collect_s"),
        "tests_s_total": total("tests_s"),
        "idle_s_total": total("idle_s"),
        # Per worker: this is what does NOT shrink by adding workers, and is the
        # number that decides whether more workers still pay for themselves.
        "startup_per_worker_s": round(sum(r["import_s"] + r["collect_s"] for r in workers) / n, 1),
        # The suite waits for its slowest worker, not for the average.
        "slowest_worker_wall_s": round(max(r["worker_wall_s"] for r in workers), 1),
    }
    if controller is not None:
        # Only the wall time. `controller["collect_s"]` is 0.0 by construction --
        # `DSession.pytest_collection` returns True and, registered at configure
        # time, runs ahead of the rootdir conftest under pluggy's LIFO
        # firstresult, so the collection hooks never fire on the controller.
        # A field that is always the same constant reports nothing.
        summary["controller_wall_s"] = controller["worker_wall_s"]
    return summary
