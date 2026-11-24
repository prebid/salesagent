"""``scripts/audit/coverage_scoreboard.py`` grades each feature scenario from measured outcomes.

The fixture under ``tests/fixtures/coverage_scoreboard`` holds one feature file with a
scenario per status, plus the liveness session, collection manifest and storyboard
summary a CI run would upload. Every status the scoreboard can report is reached from
that data, so a change in how one is derived shows up here.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from scripts.audit import coverage_scoreboard as sb

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "coverage_scoreboard"
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
        "Fails": "failing",
        "Collected but never run": "skipped",
        "No module binds it": "not_collected",
        "Has no identity tag": "untagged",
    }


def test_per_transport_outcomes_and_xfail_detail(board: dict) -> None:
    scenarios = _by_title(board)
    assert scenarios["Passes everywhere"]["transports"] == {"mcp": "passed", "rest": "passed"}
    split = scenarios["Passes in-process, xfails in-network"]
    assert split["transports"] == {"mcp": "passed", "e2e_rest": "xfailed"}
    assert (split["xfail_reason"], split["xfail_category"], split["xfail_strict"]) == (
        "ledgered in-network",
        "ledgered",
        False,
    )
    gap = scenarios["Known gap on every transport"]
    assert (gap["tag"], gap["transports"], gap["xfail_strict"]) == ("T-UC-900-gap", {"mcp": "xfailed"}, True)
    assert scenarios["Collected but never run"]["transports"] == {"mcp": "not_run"}
    assert scenarios["No module binds it"]["transports"] == {}


def test_feature_rollup_and_totals_count_every_scenario_once(board: dict) -> None:
    (feature,) = board["features"]
    assert feature["feature"] == "BR-UC-900-demo"
    assert feature["use_case"] == "BR-UC-900"
    assert feature["file"] == "tests/bdd/features/BR-UC-900-demo.feature"
    assert feature["scenarios"] == 7
    assert all(feature[status] == 1 for status in sb.STATUS_IDS)
    assert board["totals"] == {k: v for k, v in feature.items() if k in board["totals"]}


def test_header_links_the_run_and_the_storyboard_score(board: dict) -> None:
    assert board["schema_version"] == sb.SCHEMA_VERSION
    assert board["generated_at"] == "2026-01-02T03:04:05Z"
    assert board["commit_url"] == "https://github.com/prebid/salesagent/commit/abc123"
    assert board["run_url"] == "https://github.com/prebid/salesagent/actions/runs/42"
    assert board["transports"] == ["mcp", "rest", "e2e_rest"]
    assert [s["id"] for s in board["statuses"]] == list(sb.STATUS_IDS)
    assert board["storyboard"]["floor"] == {"mcp": 53, "a2a": 53}
    (measured,) = board["storyboard"]["measured"]
    assert (measured["protocol"], measured["passed"], measured["storyboards"]) == ("mcp", 55, 2)


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
    assert written["totals"]["scenarios"] == 7
    markdown = (out / "scoreboard.md").read_text(encoding="utf-8")
    assert "| BR-UC-900-demo | 7 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |" in markdown
    assert "mcp 55 passed" in markdown


def test_no_liveness_sessions_is_refused(tmp_path: Path) -> None:
    """An empty artifact directory is a missing measurement, not a scoreboard of zeros."""
    with pytest.raises(SystemExit, match="nothing was measured"):
        sb.build(FIXTURE / "repo", tmp_path)
