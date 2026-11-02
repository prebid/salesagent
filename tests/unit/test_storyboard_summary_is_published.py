"""The runner's summary must leave the box, and its pass count must be reported.

WHY THIS EXISTS. The storyboard suite turns only FAILURES and SKIPS into pytest items
(``_collect_checks``), so its outcome line cannot express a pass: a protocol passing 33
of its checks and a protocol passing none both read as zero passed. The runner does
report the number — ``_graded_total`` already reads ``summary["passed"]`` — but nothing
displayed it, and the summary file itself lives under ``tests/storyboard/runner/results/``,
outside the three paths a remote run copies home (``test-results/``, ``coverage.json``,
``htmlcov/``). The score was therefore unreadable without an ssh to the runner box, and a
run where MCP passed 33 checks was reported, in this repo, as one where nothing passed.

Both halves are graded here because either one alone restores nothing: publishing the
file without reporting the count leaves the suite line still saying zero, and reporting
the count without publishing the file leaves it stranded on the box.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.storyboard.test_storyboard_conformance import _publish_summary, _scoreboard

# A real summary shape, trimmed to the fields the scoreboard reads. The counts are the
# ones measured on run sa-0c74d963, where the MCP axis passed 33 checks while the suite
# line said 1 passed (that 1 being the SDK-pin test in another module).
_SUMMARY: dict[str, object] = {
    "overall_status": "partial",
    "passed": 33,
    "failed": 17,
    "skipped": 140,
    "not_selected_count": 0,
    "storyboards_executed": ["capability_discovery", "error_compliance"],
    "agent_url": "http://adcp-server-storyboard:8080/mcp/",
    "failures": [],
}


@pytest.fixture
def published(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the publish destination so the test never writes the real test-results/."""
    monkeypatch.setattr("tests.storyboard.test_storyboard_conformance._REPO_ROOT", tmp_path, raising=True)
    return tmp_path


class TestSummaryRidesHome:
    """The file lands under ``test-results/``, which is what a remote run copies."""

    def test_summary_is_written_into_test_results(self, published: Path) -> None:
        _publish_summary("mcp", _SUMMARY)

        dest = published / "test-results" / "storyboard_summary_mcp.json"
        assert dest.is_file(), (
            f"the runner summary was not published under test-results/: {sorted(published.rglob('*'))}"
        )
        assert json.loads(dest.read_text())["passed"] == 33

    def test_each_protocol_gets_its_own_file(self, published: Path) -> None:
        """One shared name would have A2A's summary overwrite MCP's — the asymmetry
        between the two axes (33 passed vs 3) is only visible while both survive."""
        _publish_summary("mcp", _SUMMARY)
        _publish_summary("a2a", {**_SUMMARY, "passed": 3, "storyboards_executed": []})

        results = published / "test-results"
        assert json.loads((results / "storyboard_summary_mcp.json").read_text())["passed"] == 33
        assert json.loads((results / "storyboard_summary_a2a.json").read_text())["passed"] == 3


class TestScoreboardNamesThePassCount:
    """The number the outcome line structurally cannot carry."""

    def test_pass_count_is_reported(self) -> None:
        line = _scoreboard("mcp", _SUMMARY)

        assert "passed=33" in line, f"the scoreboard hid the pass count: {line!r}"

    def test_every_denominator_is_reported(self) -> None:
        """passed/failed/skipped/not_selected each appear, because quoting one without
        the others is how four different totals got reported as one number."""
        line = _scoreboard("mcp", _SUMMARY)

        for expected in ("passed=33", "failed=17", "skipped=140", "not_selected=0"):
            assert expected in line, f"{expected} missing from {line!r}"

    def test_storyboards_executed_is_reported(self) -> None:
        """The count that explains an axis grading fewer checks than its sibling: MCP
        executed 44 storyboards where A2A executed 25, which is why their totals
        differed by 64 checks."""
        line = _scoreboard("mcp", _SUMMARY)

        assert "storyboards_executed=2" in line, line

    def test_overall_status_and_agent_url_are_reported(self) -> None:
        """``partial`` is the runner's own verdict, and the URL says which surface it
        graded — the A2A axis fails because of the endpoint it was pointed at."""
        line = _scoreboard("mcp", _SUMMARY)

        assert "partial" in line
        assert "http://adcp-server-storyboard:8080/mcp/" in line
