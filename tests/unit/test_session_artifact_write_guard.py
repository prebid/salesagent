"""Where a BDD session's liveness record is written, and which variables a nested session drops.

A session over the whole tree writes the shared path and its own session copy. A session
handed a file list writes only its session copy, because the shared path's readers refuse
a narrowed scope. With no sessions directory named, no session copy is written. A pytest
that a test starts drops exactly the two variables that name the run's directories.

Driven through the plugin's real finish hook.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests._collection_manifest import unpublished_env
from tests.bdd import scenario_liveness

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BDD_TREE = _REPO_ROOT / "tests" / "bdd"


def _finished_session(*target: Path) -> SimpleNamespace:
    option = SimpleNamespace(keyword="", markexpr="", numprocesses=0, collectonly=False)
    config = SimpleNamespace(rootdir=str(_REPO_ROOT), args=[str(path) for path in target], option=option)
    return SimpleNamespace(config=config, testscollected=0, exitstatus=0, testsfailed=0)


@pytest.fixture
def liveness_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    shared, sessions = tmp_path / "liveness.json", tmp_path / "sessions"
    monkeypatch.setenv("BDD_LIVENESS_ARTIFACT", str(shared))
    monkeypatch.setenv("BDD_LIVENESS_SESSIONS", str(sessions))
    return shared, sessions


def test_a_whole_tree_session_writes_the_shared_path_and_its_session(liveness_paths: tuple[Path, Path]) -> None:
    shared, sessions = liveness_paths

    scenario_liveness.pytest_sessionfinish(_finished_session(_BDD_TREE))

    assert shared.is_file()
    assert len(list(sessions.glob("*.json"))) == 1


def test_a_session_handed_a_file_list_stays_off_the_shared_path(liveness_paths: tuple[Path, Path]) -> None:
    """load_artifact refuses that scope, so writing it would red the next gate."""
    shared, sessions = liveness_paths

    scenario_liveness.pytest_sessionfinish(_finished_session(_BDD_TREE / "test_uc005_discover_creative_formats.py"))

    assert not shared.exists()
    written = json.loads(next(sessions.glob("*.json")).read_text(encoding="utf-8"))
    assert written["run"]["target"] == ["tests/bdd/test_uc005_discover_creative_formats.py"]


def test_no_sessions_directory_means_no_session_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A nested or ad-hoc session has no run to publish into, and no reader could use the copy."""
    shared = tmp_path / "liveness.json"
    monkeypatch.setenv("BDD_LIVENESS_ARTIFACT", str(shared))
    monkeypatch.delenv("BDD_LIVENESS_SESSIONS", raising=False)

    scenario_liveness.pytest_sessionfinish(_finished_session(_BDD_TREE))

    assert [path.name for path in tmp_path.rglob("*") if path.is_file()] == [shared.name]


def test_unpublished_env_drops_only_what_names_the_runs_directories() -> None:
    env = {"PYTEST_COLLECTION_MANIFEST": "m", "BDD_LIVENESS_SESSIONS": "s", "BDD_LIVENESS_ARTIFACT": "a", "X": "1"}

    assert unpublished_env(env) == {"BDD_LIVENESS_ARTIFACT": "a", "X": "1"}
