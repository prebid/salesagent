"""Guard: a run's records come from the run's own sessions.

A pytest that a test starts inherits the variables naming the run's directories, so unless
it goes through ``unpublished_env`` it adds a narrow slice to the records every reader folds.
Its record is stamped with its parent's suite and its own target, which is how it shows.
"""

from __future__ import annotations

from tests._collection_manifest import manifest_dir, read_artifacts

# Only the suites that start nested sessions. An unset suite is CI's direct pytest job.
_OWN_TARGETS = {"unit": ("tests/unit", "tests/harness"), "integration": ("tests/integration",)}


def test_no_record_comes_from_a_session_a_test_started() -> None:
    strays = [
        (path.name, record["suite"], record["target"])
        for path, record in read_artifacts(manifest_dir())
        if record.get("suite") in _OWN_TARGETS and set(record["target"]) - set(_OWN_TARGETS[record["suite"]])
    ]

    assert not strays, f"a nested session published into the run; give it unpublished_env(): {strays}"
