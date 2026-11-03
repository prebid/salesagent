"""A failed per-session artifact write reports itself; it does not fail the run.

`pytest_sessionfinish` raising is an unhandled error, so an unguarded write turns a session
whose tests all passed into a nonzero exit with no summary line. Each plugin below runs for
every session of its suite, so that is the ordinary case rather than a corner.

Driven through each plugin's real finish hook, so an unguarded write re-inlined at a call
site reddens here. Swallowing the write is safe because every reader fails closed on a
missing record.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import _collection_manifest
from tests._collection_manifest import unpublished_env, write_artifact
from tests.bdd import payload_capture, scenario_liveness

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BDD_TREE = _REPO_ROOT / "tests" / "bdd"


def _finished_session(*target: Path) -> SimpleNamespace:
    option = SimpleNamespace(keyword="", markexpr="", numprocesses=0, collectonly=False)
    config = SimpleNamespace(rootdir=str(_REPO_ROOT), args=[str(path) for path in target], option=option)
    return SimpleNamespace(config=config, testscollected=0, exitstatus=0, testsfailed=0)


@pytest.fixture
def blocker(tmp_path: Path) -> Path:
    """A regular file where a parent directory belongs: mkdir raises OSError."""
    path = tmp_path / "occupied"
    path.write_text("not a directory", encoding="utf-8")
    return path


@pytest.mark.parametrize("unwritable", ["BDD_LIVENESS_ARTIFACT", "BDD_LIVENESS_SESSIONS"])
def test_liveness_finish_survives_either_unwritable_path(
    unwritable: str,
    tmp_path: Path,
    blocker: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("BDD_LIVENESS_ARTIFACT", str(tmp_path / "liveness.json"))
    monkeypatch.setenv("BDD_LIVENESS_SESSIONS", str(tmp_path / "sessions"))
    monkeypatch.setenv(unwritable, str(blocker / "sub"))

    scenario_liveness.pytest_sessionfinish(_finished_session(_BDD_TREE))

    assert f"could not write {blocker}" in capsys.readouterr().err


def test_payload_finish_survives_an_unwritable_path(
    tmp_path: Path, blocker: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(payload_capture.ARTIFACT_ENV_VAR, str(blocker / "payloads.json"))

    payload_capture.pytest_sessionfinish(_finished_session(_BDD_TREE))

    assert f"could not write {blocker}" in capsys.readouterr().err


def test_manifest_finish_survives_an_unwritable_path(
    blocker: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(_collection_manifest, "enabled", True)
    monkeypatch.setattr(_collection_manifest, "_OUT_DIR", str(blocker / "sub"))
    monkeypatch.setattr(_collection_manifest, "_record", {"target": [], "filter": "", "worker": "main", "rows": []})

    _collection_manifest.on_session_finish()

    assert f"could not write {blocker}" in capsys.readouterr().err


def test_the_write_still_happens_when_it_can(tmp_path: Path) -> None:
    """The guard must not have turned the writer into a no-op."""
    target = tmp_path / "sessions" / "run.json"

    write_artifact(target, '{"scenarios": []}\n')

    assert target.read_text(encoding="utf-8") == '{"scenarios": []}\n'


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
