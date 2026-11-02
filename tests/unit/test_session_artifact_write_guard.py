"""A failed per-session artifact write reports itself; it does not fail the run.

`pytest_sessionfinish` raising is an unhandled error, so an unguarded write turns a session
whose tests all passed into a nonzero exit with no summary line. Each plugin below runs for
every session of its suite, so that is the ordinary case rather than a corner.

Driven through each plugin's real finish hook, so an unguarded write re-inlined at a call
site reddens here. Swallowing the write is safe because every reader fails closed on a
missing record.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import _collection_manifest
from tests._collection_manifest import write_artifact
from tests.bdd import payload_capture, scenario_liveness


def _finished_session(tmp_path: Path) -> SimpleNamespace:
    option = SimpleNamespace(keyword="", markexpr="", numprocesses=0, collectonly=False)
    config = SimpleNamespace(rootdir=str(tmp_path), args=[str(tmp_path)], option=option)
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

    scenario_liveness.pytest_sessionfinish(_finished_session(tmp_path))

    assert f"could not write {blocker}" in capsys.readouterr().err


def test_payload_finish_survives_an_unwritable_path(
    tmp_path: Path, blocker: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(payload_capture.ARTIFACT_ENV_VAR, str(blocker / "payloads.json"))

    payload_capture.pytest_sessionfinish(_finished_session(tmp_path))

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
