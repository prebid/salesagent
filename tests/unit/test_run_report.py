"""``scripts/audit/run_report.py`` shows what a run holds, not what pytest's totals imply.

Three readings it exists to make impossible: a storyboard suite read as "0 passed"
off its pytest items when the runner scored 30; a failure count with no nodeid or
reason behind it; and a delta against a baseline that hides pass -> xfail behind a
green total. Each test builds run directories on disk and reads the tool's stdout,
because that text is the deliverable.
"""

from __future__ import annotations

import io
import json
import pathlib

from scripts.audit.run_report import STORYBOARD_SUBDIR, main, report


def _write_run(
    directory: pathlib.Path,
    reports: dict[str, dict[str, tuple[str, str]]],
    storyboard: dict[str, dict] | None = None,
) -> pathlib.Path:
    """``reports``: suite -> {nodeid: (outcome, longrepr)}; ``storyboard``: protocol -> runner summary."""
    directory.mkdir(parents=True, exist_ok=True)
    for suite, tests in reports.items():
        payload = {
            "tests": [
                {"nodeid": nodeid, "outcome": outcome, "call": {"longrepr": reason}}
                for nodeid, (outcome, reason) in tests.items()
            ]
        }
        (directory / f"{suite}.json").write_text(json.dumps(payload))
    for protocol, summary in (storyboard or {}).items():
        dest = directory / STORYBOARD_SUBDIR / f"summary_{protocol}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(summary))
    return directory


def _summary(passed: int, failures: list[str]) -> dict:
    return {
        "overall_status": "partial",
        "passed": passed,
        "failed": len(failures),
        "skipped": 4,
        "storyboards_executed": ["a", "b"],
        "sdk_version": "14.0.0-rc.35",
        "adcp_version": "3.1.1",
        "skipped_by_reason": {"prerequisite_failed": 4},
        "failures": [
            {"track": "core", "storyboard_id": sb, "step_id": step, "reason_kind": "validation", "reason": "why"}
            for sb, step in (f.split("/") for f in failures)
        ],
    }


def _render(run: pathlib.Path, baselines: dict[str, pathlib.Path] | None = None) -> str:
    out = io.StringIO()
    report(run, baselines or {}, limit=10, out=out)
    return out.getvalue()


def test_storyboard_score_comes_from_the_runner_not_from_pytest_items(tmp_path: pathlib.Path) -> None:
    run = _write_run(
        tmp_path / "run",
        {"storyboard": {"test_storyboard_check[mcp::core::x::y]": ("failed", "storyboard check failed")}},
        storyboard={"mcp": _summary(30, ["x/y"]), "a2a": _summary(30, ["x/y"])},
    )
    text = _render(run)
    assert "mcp  partial: passed=30 failed=1" in text
    assert "a2a  partial: passed=30 failed=1" in text
    assert "pytest items only" in text
    assert "DISPARITY between protocols: 0 checks" in text


def test_a_check_failing_on_one_protocol_only_is_a_disparity_row(tmp_path: pathlib.Path) -> None:
    run = _write_run(
        tmp_path / "run",
        {"unit": {"t.py::test_ok": ("passed", "")}},
        storyboard={"mcp": _summary(33, []), "a2a": _summary(3, ["signed/positive-001", "signed/negative-002"])},
    )
    text = _render(run)
    assert "DISPARITY between protocols: 2 checks" in text
    assert "core/signed/positive-001  fails only on a2a" in text


def test_a_run_without_runner_summaries_says_so_instead_of_scoring_zero(tmp_path: pathlib.Path) -> None:
    run = _write_run(tmp_path / "run", {"storyboard": {"test_storyboard_check[mcp::core::x::y]": ("failed", "")}})
    text = _render(run)
    assert "NOT MEASURED: no storyboard/summary_<protocol>.json" in text
    assert "passed=" not in text


def test_every_failure_is_listed_with_its_last_traceback_line(tmp_path: pathlib.Path) -> None:
    run = _write_run(
        tmp_path / "run",
        {
            "integration": {
                "t.py::test_red": (
                    "failed",
                    "Traceback\n  ...\nE   AssertionError: expected AUTH_MISSING, got INVALID_REQUEST",
                ),
                "t.py::test_fixture_died": ("error", "E   DeadlockDetected"),
                "t.py::test_green": ("passed", ""),
            }
        },
    )
    text = _render(run)
    assert "integration: 2" in text
    assert "t.py::test_red\n        E   AssertionError: expected AUTH_MISSING, got INVALID_REQUEST" in text
    assert "t.py::test_fixture_died\n        E   DeadlockDetected" in text
    assert "t.py::test_green" not in text.split("FAILURES")[1]


def test_the_reason_comes_from_the_failing_phase_not_the_xdist_banner(tmp_path: pathlib.Path) -> None:
    """Under xdist a PASSED setup carries a longrepr too: the worker banner. It is not the reason."""
    run = tmp_path / "run"
    run.mkdir()
    (run / "unit.json").write_text(
        json.dumps(
            {
                "tests": [
                    {
                        "nodeid": "t.py::test_red",
                        "outcome": "failed",
                        "setup": {
                            "outcome": "passed",
                            "longrepr": "[gw3] linux -- Python 3.12.14 /app/.tox/unit/bin/python3",
                        },
                        "call": {
                            "outcome": "failed",
                            "longrepr": "[gw3] linux -- ...\n    assert 1 == 2\nE   AssertionError: 1 != 2",
                        },
                    }
                ]
            }
        )
    )
    text = _render(run)
    assert "t.py::test_red\n        E   AssertionError: 1 != 2" in text
    assert "[gw3]" not in text.split("FAILURES")[1]


def test_the_baseline_delta_names_fixed_regressed_new_and_gone_per_nodeid(tmp_path: pathlib.Path) -> None:
    base = _write_run(
        tmp_path / "base",
        {
            "unit": {
                "t.py::fixed": ("failed", "boom"),
                "t.py::regressed_to_xfail": ("passed", ""),
                "t.py::regressed_to_fail": ("passed", ""),
                "t.py::gone": ("failed", "boom"),
                "t.py::stable": ("passed", ""),
            }
        },
        storyboard={"mcp": _summary(33, ["x/old"])},
    )
    run = _write_run(
        tmp_path / "run",
        {
            "unit": {
                "t.py::fixed": ("passed", ""),
                "t.py::regressed_to_xfail": ("xfailed", "parked"),
                "t.py::regressed_to_fail": ("failed", "boom"),
                "t.py::new_failure": ("failed", "boom"),
                "t.py::stable": ("passed", ""),
            }
        },
        storyboard={"mcp": _summary(30, ["x/new"])},
    )
    text = _render(run, {"exec": base})
    assert "AGAINST BASELINE exec" in text
    assert "FIXED (failed there, passes here): 1" in text and "passed   unit::t.py::fixed" in text
    assert "REGRESSED (passed there, not passing here): 2" in text
    assert "xfailed  unit::t.py::regressed_to_xfail" in text, "pass -> xfail removes grading and must be listed"
    assert "NEW FAILURE (absent there, failing here): 1" in text and "unit::t.py::new_failure" in text
    assert "GONE (failed there, absent here): 1" in text and "unit::t.py::gone" in text
    assert "storyboard mcp: passed 33 -> 30, failed 1 -> 1; checks fixed 1, newly failing 1" in text
    assert "fixed        core/x/old" in text and "newly failing core/x/new" in text


def test_failures_are_grouped_by_signature_with_instance_values_blanked(tmp_path: pathlib.Path) -> None:
    """Two failures that differ only in a quoted name or a number share one group; a storyboard
    check is grouped by the runner's reason, and its pytest twin is not counted a second time."""
    run = _write_run(
        tmp_path / "run",
        {
            "integration": {
                "a.py::t1": (
                    "failed",
                    "E   TypeError: invoke_tool() missing 1 required positional argument: 'credential'",
                ),
                "a.py::t2": (
                    "failed",
                    "E   TypeError: invoke_tool() missing 2 required positional argument: 'protocol'",
                ),
                "b.py::t3": (
                    "failed",
                    "E   AssertionError: adcp_error.code='CREATIVE_NOT_FOUND', expected 'CREATIVE_REJECTED'",
                ),
            },
            "storyboard": {
                "test_storyboard_check[mcp::core::x::y]": ("failed", "E   assert 'fail' != 'fail'"),
                "test_runner_sdk_pin.py::test_pin": ("failed", "E   assert '3.2.0-rc.1' == '3.1.1'"),
            },
        },
        storyboard={"mcp": _summary(30, ["x/y"]), "a2a": _summary(30, ["x/y"])},
    )
    text = _render(run)
    section = text.split("FAILURE GROUPS")[1].split("FAILURES (nodeid")[0]
    assert "FAILURE GROUPS (by signature): 4" in text
    assert "g00     2  integration  E   TypeError: invoke_tool() missing # required positional argument: …" in section
    assert "storyboard-runner  storyboard validation: why" in section
    assert "assert … != …" not in section, "the pytest twin of a runner check must not be a second group"
    assert "test_runner_sdk_pin" in section


def test_an_absent_run_directory_is_not_measured(tmp_path: pathlib.Path, capsys) -> None:
    assert main([str(tmp_path / "nowhere")]) == 2
    assert "NOT MEASURED" in capsys.readouterr().err
