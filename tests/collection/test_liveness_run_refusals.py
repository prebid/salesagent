"""``load_run`` refuses a partial measurement, graded against the real run's own sessions.

``test_bdd_scenario_liveness_real_run.py`` reads the whole run through ``load_run`` and
grades two scenario slices, so it reddens only when the sessions holding those slices are
missing. These cases take the run's session files as they are, remove or mark one, and
assert the refusal that keeps a partial run from being graded as the suite.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from tests._collection_manifest import read_artifacts
from tests.bdd.scenario_liveness import IncompleteLivenessRun, load_run, sessions_dir


def _is_filtered(payload: dict[str, Any]) -> bool:
    return bool(payload["run"].get("selection") or payload["run"].get("markers"))


def _sessions() -> list[tuple[Path, dict[str, Any]]]:
    return read_artifacts(sessions_dir())


def _shard_sessions() -> list[tuple[Path, dict[str, Any]]]:
    shards = [(path, payload) for path, payload in _sessions() if not _is_filtered(payload)]
    assert len(shards) >= 2, f"the run published {len(shards)} unfiltered session(s); there is no shard to remove"
    return shards


def _copy(directory: Path, sessions: list[tuple[Path, dict[str, Any]]]) -> Path:
    directory.mkdir()
    for path, _ in sessions:
        shutil.copy(path, directory / path.name)
    return directory


def test_the_run_as_published_is_accepted() -> None:
    """The cases below remove something; this is the run they remove it from."""
    load_run(sessions_dir())


def test_a_run_missing_any_shard_is_refused(tmp_path: Path) -> None:
    for dropped, _ in _shard_sessions():
        rest = [(path, payload) for path, payload in _sessions() if path != dropped]
        with pytest.raises(IncompleteLivenessRun, match="does not account for every BDD module"):
            load_run(_copy(tmp_path / dropped.stem, rest))


def test_a_session_that_lost_a_worker_is_refused(tmp_path: Path) -> None:
    sharded = [(path, payload) for path, payload in _shard_sessions() if payload["run"].get("workers")]
    assert sharded, "no session in the run ran under xdist, so none can have lost a worker"
    marked_path, marked = sharded[0]

    directory = _copy(tmp_path / "run", [(path, payload) for path, payload in _sessions() if path != marked_path])
    marked["run"]["workers_missing"] = ["gw0"]
    (directory / marked_path.name).write_text(json.dumps(marked), encoding="utf-8")

    with pytest.raises(IncompleteLivenessRun, match="gw0"):
        load_run(directory)


def test_a_filtered_session_is_not_counted_as_coverage(tmp_path: Path) -> None:
    """The filtered session's target is the whole tree, so counting it would cover any gap."""
    filtered = [(path, payload) for path, payload in _sessions() if _is_filtered(payload)]
    assert filtered, "the run published no filtered session"
    assert all(payload["run"]["target"] == ["tests/bdd"] for _, payload in filtered)

    with pytest.raises(IncompleteLivenessRun, match="does not account for every BDD module"):
        load_run(_copy(tmp_path / "run", filtered + _shard_sessions()[1:]))
