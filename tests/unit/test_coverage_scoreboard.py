"""``scripts/audit/coverage_scoreboard.py`` grades each feature scenario from measured outcomes.

The fixture under ``tests/fixtures/coverage_scoreboard`` holds one feature file with a
scenario per status, laid out the way a CI run uploads its artifacts: a shard's
unfiltered liveness session and collection manifest, the filtered ``bdd_e2e`` session
and manifest that carry the ``e2e_rest`` rows, and a stray ``-k`` session that must not
change any outcome. Every status the scoreboard can report is reached from that data,
so a change in how one is derived shows up here.
"""

from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

import pytest

from scripts.audit import coverage_scoreboard as sb
from scripts.audit import run_identity
from tests._collection_manifest import IncompleteLivenessRun, ManifestNotFound
from tests.helpers.run_identity_record import write_run_identity

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "coverage_scoreboard"
SHARD_SESSION = Path("bdd-shard-1-coverage") / "liveness-sessions" / "s1.json"
E2E_SESSION = Path("bdd-in-network-collection") / "innet_1" / "liveness-sessions" / "e2e.json"
NOW = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC)


@pytest.fixture
def board() -> dict:
    return sb.build(
        FIXTURE / "repo",
        FIXTURE / "artifacts",
        commit="abc123",
        run_id="42",
        repository="prebid/salesagent",
        now=NOW,
    )


def _by_title(board: dict) -> dict[str, dict]:
    return {s["title"]: s for s in board["scenarios"]}


def test_every_status_is_derived_from_the_artifacts(board: dict) -> None:
    statuses = {title: s["status"] for title, s in _by_title(board).items()}
    assert statuses == {
        "Passes everywhere": "passing",
        "Passes in-process, xfails in-network": "partial",
        "Known gap on every transport": "xfail",
        "Has an unbound step": "dormant",
        "One example runs, one is parked": "partial",
        "Fails": "failing",
        "Collected but never run": "skipped",
        "No module binds it": "not_collected",
        "Has no identity tag": "untagged",
    }


def test_per_transport_outcomes_and_xfail_detail(board: dict) -> None:
    scenarios = _by_title(board)
    # The stray `-k green` session recorded a failure; only the in-network rows of a
    # filtered session are admitted, so it changes nothing.
    assert scenarios["Passes everywhere"]["transports"] == {"mcp": "passed", "rest": "passed"}
    split = scenarios["Passes in-process, xfails in-network"]
    # e2e_rest comes from the filtered bdd_e2e session, beside the shard's mcp row.
    assert split["transports"] == {"mcp": "passed", "e2e_rest": "xfailed"}
    assert (split["xfail_reason"], split["xfail_strict"]) == ("ledgered in-network", False)
    gap = scenarios["Known gap on every transport"]
    assert (gap["tag"], gap["transports"], gap["xfail_strict"]) == ("T-UC-900-gap", {"mcp": "xfailed"}, True)
    # Selected but unrun on both the shard manifest and the in-network manifest.
    assert scenarios["Collected but never run"]["transports"] == {"mcp": "not_run", "e2e_rest": "not_run"}
    # The parked example is not dropped because its sibling on the same transport ran.
    assert scenarios["One example runs, one is parked"]["transports"] == {"mcp": "mixed"}
    assert scenarios["No module binds it"]["transports"] == {}


def test_feature_rollup_and_totals_count_every_scenario_once(board: dict) -> None:
    (feature,) = board["features"]
    assert feature["feature"] == "BR-UC-900-demo"
    assert feature["use_case"] == "BR-UC-900"
    assert feature["file"] == "tests/bdd/features/BR-UC-900-demo.feature"
    assert feature["scenarios"] == 9
    assert {status: feature[status] for status in sb.STATUS_IDS} == {**dict.fromkeys(sb.STATUS_IDS, 1), "partial": 2}
    assert board["totals"] == {k: v for k, v in feature.items() if k in board["totals"]}


def test_header_links_the_run(board: dict) -> None:
    assert board["schema_version"] == sb.SCHEMA_VERSION
    assert board["generated_at"] == "2026-01-02T03:04:05Z"
    assert board["commit_url"] == "https://github.com/prebid/salesagent/commit/abc123"
    assert board["run_url"] == "https://github.com/prebid/salesagent/actions/runs/42"
    assert board["transports"] == ["mcp", "rest", "e2e_rest"]
    assert [s["id"] for s in board["statuses"]] == list(sb.STATUS_IDS)
    assert "storyboard" not in board


@pytest.mark.parametrize(
    ("feature", "expected"),
    [
        ("BR-UC-002-create-media-buy", "BR-UC-002"),
        ("BR-ADMIN-ACCOUNTS", "BR-ADMIN-ACCOUNTS"),
        ("BR-UC-GET-PRODUCTS-format-id-filter", "BR-UC-GET-PRODUCTS"),
        ("local-tls-ask", "local"),
    ],
)
def test_use_case_is_the_upper_case_prefix(feature: str, expected: str) -> None:
    assert sb.use_case(feature) == expected


def test_cli_writes_json_and_markdown(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert sb.main(["--repo", str(FIXTURE / "repo"), "--artifacts", str(FIXTURE / "artifacts"), "--out", str(out)]) == 0
    written = json.loads((out / "scoreboard.json").read_text(encoding="utf-8"))
    assert written["totals"]["scenarios"] == 9
    markdown = (out / "scoreboard.md").read_text(encoding="utf-8")
    assert "| BR-UC-900-demo | 9 | 1 | 2 | 1 | 1 | 1 | 1 | 1 | 1 |" in markdown


def test_no_liveness_sessions_is_refused(tmp_path: Path) -> None:
    """An empty artifact directory is a missing measurement, not a scoreboard of zeros."""
    with pytest.raises(IncompleteLivenessRun, match="nothing was measured"):
        sb.build(FIXTURE / "repo", tmp_path)


def test_a_shared_identity_tag_is_refused(tmp_path: Path) -> None:
    """Two scenarios on one @T-* tag would each show the pair's pooled outcome."""
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE / "repo", repo)
    feature = repo / sb.FEATURES_SUBDIR / "BR-UC-900-demo.feature"
    text = feature.read_text(encoding="utf-8")
    feature.write_text(text.replace("@T-UC-900-red\n", "@T-UC-900-green\n"), encoding="utf-8")
    with pytest.raises(ValueError, match="T-UC-900-green"):
        sb.feature_scenarios(repo)


def _copy_artifacts(tmp_path: Path, session: Path, edit) -> Path:
    """The fixture artifacts, with one liveness session rewritten by ``edit``."""
    artifacts = tmp_path / "artifacts"
    shutil.copytree(FIXTURE / "artifacts", artifacts)
    path = artifacts / session
    path.write_text(json.dumps(edit(json.loads(path.read_text(encoding="utf-8")))), encoding="utf-8")
    return artifacts


def _lose_worker(session: dict) -> dict:
    session["run"]["workers_missing"] = ["gw1"]
    return session


@pytest.mark.parametrize("session", [SHARD_SESSION, E2E_SESSION], ids=["shard", "filtered-e2e"])
def test_a_session_that_lost_a_worker_is_refused(tmp_path: Path, session: Path) -> None:
    """A partial measurement would turn the lost worker's scenarios into not_collected."""
    with pytest.raises(IncompleteLivenessRun, match="gw1"):
        sb.build(FIXTURE / "repo", _copy_artifacts(tmp_path, session, _lose_worker))


def test_a_call_phase_skip_is_not_a_failure(tmp_path: Path) -> None:
    def skip_red(session: dict) -> dict:
        for record in session["scenarios"]:
            for obs in record["observations"]:
                if obs["outcome"] == "failed":
                    obs["outcome"] = "skipped"
        return session

    board = sb.build(FIXTURE / "repo", _copy_artifacts(tmp_path, SHARD_SESSION, skip_red))
    fails = _by_title(board)["Fails"]
    assert (fails["status"], fails["transports"]) == ("skipped", {"mcp": "skipped"})


def test_a_missing_collection_manifest_is_refused(tmp_path: Path) -> None:
    """Without it every collected-but-unrun scenario would read as not_collected."""
    artifacts = tmp_path / "artifacts"
    shutil.copytree(FIXTURE / "artifacts", artifacts)
    shutil.rmtree(artifacts / "bdd-shard-1-coverage" / "collection-manifest")
    with pytest.raises(ManifestNotFound):
        sb.build(FIXTURE / "repo", artifacts)


def test_default_artifacts_are_the_newest_run_by_start_time(tmp_path: Path) -> None:
    """Run names are day-first and mtimes move, so the run is chosen by meta.json started_utc."""
    runs = {name: tmp_path / name for name in ("311225_2359", "010126_0000", "020126_0000")}
    for run in runs.values():
        (run / "liveness-sessions").mkdir(parents=True)
        write_run_identity(run)
    for name, started in (("311225_2359", "2025-12-31T23:59:00+00:00"), ("010126_0000", "2026-01-01T00:00:00+00:00")):
        meta = runs[name] / run_identity.IDENTITY_FILE
        meta.write_text(
            json.dumps({**json.loads(meta.read_text(encoding="utf-8")), "started_utc": started}), encoding="utf-8"
        )
    (runs["020126_0000"] / run_identity.IDENTITY_FILE).unlink()  # cannot be placed in time
    assert run_identity.newest_run(tmp_path, "liveness-sessions") == runs["010126_0000"]
