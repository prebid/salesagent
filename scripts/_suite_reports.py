"""Shared walk over a run's per-suite JSON reports.

The report checkers ask the same question of a results directory -- "read every
suite's report, and tell me X" -- and differ only in X. The walk, and the rule
that an unreadable report is itself a finding rather than something to skip,
live here so they cannot drift apart.

Two questions are asked today: the summary counts (:func:`scan_suite_summaries`,
for the truncation and failure checkers) and the per-test outcomes
(:func:`scan_suite_outcomes`, for the run-to-run differ).
"""

from __future__ import annotations

import glob
import json
import os
from collections.abc import Callable, Iterable

from scripts.audit.run_identity import IDENTITY_FILE


def _reports(results_dir: str) -> list[str]:
    """Every per-suite report path in *results_dir*, and nothing else.

    ``IDENTITY_FILE`` lives beside the reports and is not one of them.

    A WHOLE record is inert, measured rather than assumed: it carries no
    ``summary``, so ``check_truncated_reports``'s predicate finds ``collected``
    and ``total`` both absent and returns nothing. The case that is not inert is
    the one that check exists for. A run killed mid-write leaves a HALF-WRITTEN
    ``meta.json``, and read as a report that is "unreadable" -- which the check
    reddens, exits 1 on, and explains as "the run is TRUNCATED" and "an xdist
    worker died". Both sentences would be about the identity record rather than
    any suite, failing a run whose suites all reported in full.
    """
    return [
        path
        for path in sorted(glob.glob(os.path.join(results_dir, "*.json")))
        if os.path.basename(path) != IDENTITY_FILE
    ]


def scan_suite_summaries(
    results_dir: str,
    inspect: Callable[[str, dict], Iterable[str]],
) -> list[str]:
    """Return one problem line per finding, empty when every suite is clean.

    ``inspect`` receives a suite's report name and its ``summary`` mapping, and
    returns any problem lines for that suite. A report that cannot be read is
    reported here rather than passed to ``inspect``: a run whose evidence is
    unreadable has not been shown to pass.
    """
    problems: list[str] = []
    for path in _reports(results_dir):
        name = os.path.basename(path)
        try:
            with open(path, encoding="utf-8") as handle:
                summary = json.load(handle).get("summary", {})
        except Exception as exc:  # noqa: BLE001 -- an unreadable report is itself a finding
            problems.append(f"  {name}: unreadable ({exc})")
            continue
        problems.extend(inspect(name, summary))
    return problems


def scan_suite_outcomes(results_dir: str) -> dict[tuple[str, str], str]:
    """Return ``{(suite, nodeid): outcome}`` for every test in every report.

    Same walk as :func:`scan_suite_summaries`, and the same rule about evidence
    that cannot be read -- but raising rather than collecting a line, because
    the caller is deriving what changed between two runs: a suite silently
    dropped from one side would show up as tests that "disappeared", which is
    precisely the wrong answer to the question being asked.

    Keyed by ``(suite, nodeid)`` and not by nodeid alone: the bdd suites are
    sharded across reports, and two suites may collect the same nodeid.
    """
    outcomes: dict[tuple[str, str], str] = {}
    for path in _reports(results_dir):
        suite = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8") as handle:
            report = json.load(handle)
        for test in report.get("tests", []):
            outcomes[(suite, test["nodeid"])] = test["outcome"]
    if not outcomes:
        missing = "" if os.path.isdir(results_dir) else " (no such directory)"
        raise ValueError(
            f"{results_dir}{missing}: no per-test outcomes in any *.json — not a pytest-json-report result set"
        )
    return outcomes
