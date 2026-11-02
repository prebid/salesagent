"""The conformance run PUBLISHES what it collected, instead of it being inferred.

``_exercised_storyboards`` used to answer "did a run reach this storyboard?" from
``tests/storyboard/known_failures.txt`` — a ledger of FAILURES. Its own header says
absence of a row is not evidence of a pass, so the answer was an over-count by an
amount nothing in the repo could bound: a runner that aborts a storyboard part-way
leaves later steps unreached (``prerequisite_failed`` is a native skip and is never
ledgered) while the inference reads them as exercised.

The conformance session already computes the exact collected set in-process for its
stale-entry check and threw it away. These tests pin that it is persisted, that it is
per-CHECK rather than per-storyboard, and — the part that makes it a measurement rather
than a nicer-looking inference — that a session which collected NOTHING REAL refuses to
write, so a phantom artifact can never read as "the run reached nothing".
"""

from __future__ import annotations

import json

from scripts.audit import storyboard_check_index, storyboard_spec
from tests.storyboard import collected


def _check(protocol: str = "mcp", track: str = "happy", storyboard: str = "sb_one", step: str = "s1") -> dict:
    return {"protocol": protocol, "track": track, "storyboard_id": storyboard, "step_id": step, "status": "pass"}


class TestTheArtifactRecordsWhatWasCollected:
    def test_records_every_collected_check_at_check_grain(self, tmp_path):
        checks = [_check(step="s1"), _check(step="s2"), _check(storyboard="sb_two", step="s1")]
        path = tmp_path / storyboard_spec.COLLECTED_ARTIFACT_PATH
        collected.write(path, checks)

        artifact = json.loads(path.read_text())
        assert [c["check_id"] for c in artifact["checks"]] == [
            "mcp::happy::sb_one::s1",
            "mcp::happy::sb_one::s2",
            "mcp::happy::sb_two::s1",
        ]

    def test_a_session_that_collected_nothing_refuses_to_write(self, tmp_path):
        """The bundle-missing path parametrizes ONE skip. Writing it would publish
        'the run reached one check', which is worse than publishing nothing: the
        consumer cannot tell a real empty run from a run that never started."""
        path = tmp_path / storyboard_spec.COLLECTED_ARTIFACT_PATH
        collected.write(path, [])
        assert not path.exists()


class TestTheIndexPrefersTheMeasurementOverTheInference:
    def test_reads_the_artifact_when_present(self, tmp_path, monkeypatch):
        path = tmp_path / storyboard_spec.COLLECTED_ARTIFACT_PATH
        collected.write(path, [_check(storyboard="sb_measured")])
        monkeypatch.setattr(storyboard_check_index, "_collected_artifact_path", lambda _repo: path)

        # The ledger names a DIFFERENT storyboard; the artifact must win, because it
        # is what the run collected rather than what it failed.
        assert storyboard_check_index._exercised_storyboards(tmp_path) == {"sb_measured"}

    def test_falls_back_to_the_ledger_when_no_artifact_exists(self, tmp_path, monkeypatch):
        """Absent artifact must NOT read as 'nothing was exercised' — that would turn a
        missing measurement into a confident zero, which is the defect one level up."""
        monkeypatch.setattr(storyboard_check_index, "_collected_artifact_path", lambda _repo: tmp_path / "absent.json")
        monkeypatch.setattr(storyboard_check_index, "_ledger_storyboards", lambda _repo: {"sb_from_ledger"})
        assert storyboard_check_index._exercised_storyboards(tmp_path) == {"sb_from_ledger"}


class TestTheSessionHookIsWired:
    """The plumbing between the hook that COMPUTES the set and the one that WRITES it.

    Proven here because the live emission needs the full storyboard stack (Docker + the
    npm runner + the pinned bundle). What a local run DOES prove is the refusal half:
    `pytest tests/storyboard --collect-only` takes the bundle-missing path, sets no
    stash, and leaves no artifact — verified by hand at ad3129221.
    """

    def test_writes_when_the_session_collected_checks(self, tmp_path, monkeypatch):
        from tests.storyboard import conftest as storyboard_conftest

        monkeypatch.setattr(collected, "artifact_path", lambda _repo: tmp_path / "out.json")

        class _Config:
            pass

        class _Session:
            config = _Config()

        setattr(_Session.config, collected.STASH_ATTR, [_check(storyboard="sb_live")])
        storyboard_conftest.pytest_sessionfinish(_Session())

        payload = json.loads((tmp_path / "out.json").read_text())
        assert [c["storyboard_id"] for c in payload["checks"]] == ["sb_live"]

    def test_writes_nothing_when_no_checks_were_stashed(self, tmp_path, monkeypatch):
        """The bundle-missing path. An artifact here would claim a run that never started."""
        from tests.storyboard import conftest as storyboard_conftest

        monkeypatch.setattr(collected, "artifact_path", lambda _repo: tmp_path / "out.json")

        class _Session:
            config = type("_C", (), {})()

        storyboard_conftest.pytest_sessionfinish(_Session())
        assert not (tmp_path / "out.json").exists()
