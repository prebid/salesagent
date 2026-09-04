"""A failed liveness-artifact write reports itself; it does not fail the run.

`pytest_sessionfinish` raising is an unhandled error, so an unguarded write
turns a session whose scenarios all passed into a nonzero exit with no summary
line. The plugin is registered for every BDD session, so that is the ordinary
case rather than a corner.

Swallowing the write is safe because losing the artifact is not silent: every
consumer fails closed on a missing or empty artifact. The absence is caught
where the artifact is read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.bdd.scenario_liveness import _write


def test_a_write_failure_does_not_raise(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A regular file where the parent directory belongs: mkdir raises OSError."""
    blocker = tmp_path / "occupied"
    blocker.write_text("not a directory", encoding="utf-8")

    _write(blocker / "run.json", '{"scenarios": []}\n')

    assert "could not write the artifact" in capsys.readouterr().err


def test_the_write_still_happens_when_it_can(tmp_path: Path) -> None:
    """The guard must not have turned the writer into a no-op."""
    target = tmp_path / "sessions" / "run.json"

    _write(target, '{"scenarios": []}\n')

    assert target.read_text(encoding="utf-8") == '{"scenarios": []}\n'
