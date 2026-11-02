"""Publish the check set the conformance session actually collected.

WHY THIS EXISTS. ``storyboard_check_index`` needs to answer "did a real in-network run
REACH this storyboard?". Before this artifact it answered from
``tests/storyboard/known_failures.txt``, a ledger of FAILURES whose own header says the
absence of a row is not evidence of a pass. That inference is an over-count at storyboard
grain: a runner that aborts a storyboard part-way leaves its later steps unreached, and
``prerequisite_failed`` is a native pytest skip that is never ledgered, so those steps
read as exercised while nothing graded them.

The session already computes the exact set -- ``pytest_generate_tests`` builds it for the
stale-entry join and then discards it. Persisting it replaces the inference with a
measurement, and moves the grain from storyboard to CHECK.

REFUSAL IS PART OF THE CONTRACT. ``write`` does nothing when handed an empty set. The
bundle-missing path parametrizes a single synthetic skip, and a collect-only or aborted
session collects nothing real; publishing either would say "the run reached these checks"
about a run that never started. A consumer cannot distinguish a phantom artifact from a
truthful one, so the artifact is either a measurement or it is absent -- the same rule the
liveness emitter follows, and the same rule ``compare_runs`` learned when a vanished suite
read CLEAN.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.audit import ledger, storyboard_spec

#: Key under which the collected set is stashed on ``config`` between
#: ``pytest_generate_tests`` (which computes it) and ``pytest_sessionfinish`` (which
#: writes it). An attribute rather than a module global so a nested session cannot
#: inherit another run's set.
STASH_ATTR = "_storyboard_collected_checks"


def artifact_path(repo: Path) -> Path:
    return repo / "test-results" / storyboard_spec.COLLECTED_ARTIFACT_PATH


def check_id(check: dict[str, Any]) -> str:
    """The check's id in the SHARED grammar, asked of ``ledger`` rather than formatted here.

    ``pytest_generate_tests`` builds its parametrize ids the same way. Spelling the
    grammar twice is how the producer and the ledger's parsers drift apart.
    """
    return ledger.LedgerCheckId(
        check["protocol"], check.get("track"), check["storyboard_id"], check["step_id"]
    ).format()


def write(path: Path, checks: list[dict[str, Any]]) -> None:
    """Write the collected set, or REFUSE when there is nothing real to publish."""
    if not checks:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "checks": [
            {
                "check_id": check_id(c),
                "protocol": c["protocol"],
                "track": c.get("track"),
                "storyboard_id": c["storyboard_id"],
                "step_id": c["step_id"],
            }
            for c in checks
        ]
    }
    path.write_text(json.dumps(artifact, indent=2, sort_keys=False) + "\n", encoding="utf-8")
