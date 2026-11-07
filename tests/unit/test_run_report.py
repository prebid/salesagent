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
from tests.helpers.run_identity_record import FIXTURE_SHA, write_run_identity


def _write_run(
    directory: pathlib.Path,
    reports: dict[str, dict[str, tuple[str, str]]],
    storyboard: dict[str, dict] | None = None,
    commit: str | None = FIXTURE_SHA,
) -> pathlib.Path:
    """``reports``: suite -> {nodeid: (outcome, longrepr)}; ``storyboard``: protocol -> runner summary."""
    directory.mkdir(parents=True, exist_ok=True)
    if commit is not None:
        write_run_identity(directory, commit=commit)
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


# ── A skip count without its causes is how 31 silent guards read as green ──
#
# `unit 6646 passed / 3 failed / 40 skipped` was compared against another run's
# `6677 / 3 / 9` and called "holding steady". The two differ by THIRTY-ONE guards
# that did not ask their question: the pinned AdCP bundle was absent in one tree,
# `requires_pinned_bundle` skipped them, and the smaller graded set produced the
# better-looking number (salesagent-13wao).
#
# The skip is correct and stays — a guard with no valid input must refuse, never
# pass. The AGGREGATE was the defect, so the fix is here rather than in any guard.
# These three cases pin the property that makes the misreading impossible: the
# three states must not render the same.


_BANNER = "[gw3] linux -- Python 3.12.15 /app/.tox/unit/bin/python"


def _skipped(cause: str | None) -> tuple[str, str]:
    """A skipped item whose longrepr has pytest's real shape under xdist.

    The worker BANNER comes first and the cause sits inside the
    ``(file, lineno, "Skipped: …")`` triple behind it. Reading the first line
    instead of the skipping phase yields a histogram by worker — which is what
    both of this row's own extraction attempts did before using the repo's
    ``outcomes()`` helper, so the shape is reproduced here rather than simplified.
    """
    if cause is None:
        return ("skipped", _BANNER)
    return ("skipped", f"{_BANNER}\n('/app/tests/unit/test_x.py', 12, \"Skipped: {cause}\")")


def test_a_skip_is_reported_with_its_cause_not_only_its_count(tmp_path) -> None:
    """The bundle-absent run must name the bundle, so no reader calls it an improvement."""
    bundle = "pinned AdCP compliance tree not provisioned under /app/tests/storyboard/runner"
    run = _write_run(
        tmp_path / "absent",
        {
            "unit": {
                "tests/unit/test_a.py::test_one": _skipped(bundle),
                "tests/unit/test_b.py::test_two": _skipped(bundle),
                "tests/unit/test_c.py::test_three": _skipped("permission bits do not constrain root"),
                "tests/unit/test_d.py::test_four": ("passed", ""),
            }
        },
    )
    out = io.StringIO()
    report(run, {}, limit=5, out=out)
    text = out.getvalue()

    assert "unit: 3 skipped" in text, f"the per-suite skip total is missing:\n{text}"
    assert f"2  {bundle}" in text, (
        f"the two bundle-absent skips are not attributed to the bundle, so a reader sees a "
        f"count with no cause — which is the whole defect:\n{text}"
    )
    assert "1  permission bits do not constrain root" in text


def test_a_skip_with_no_recorded_cause_is_counted_rather_than_blank(tmp_path) -> None:
    """An unattributable skip must occupy a named bucket, not vanish into an empty row."""
    run = _write_run(tmp_path / "nocause", {"unit": {"tests/unit/test_a.py::test_one": _skipped(None)}})
    out = io.StringIO()
    report(run, {}, limit=5, out=out)
    text = out.getvalue()

    assert "unit: 1 skipped" in text
    assert "1  (no cause recorded)" in text, (
        f"a skip whose cause pytest did not record rendered as a blank row, which reads as "
        f"'nothing to see' rather than 'we cannot tell':\n{text}"
    )
    assert _BANNER not in text.split("SKIPS", 1)[1].split("STORYBOARD", 1)[0], (
        "the xdist worker banner leaked into the skip causes — grouping on it gives a "
        "histogram by worker, which looks like an answer and is not"
    )


def test_a_run_with_no_skips_says_so_rather_than_printing_nothing(tmp_path) -> None:
    """Silence must not be reusable as 'nothing was excluded'. Third state, stated."""
    run = _write_run(tmp_path / "clean", {"unit": {"tests/unit/test_a.py::test_one": ("passed", "")}})
    out = io.StringIO()
    report(run, {}, limit=5, out=out)
    text = out.getvalue()

    assert "no suite in this run recorded a skip" in text, (
        f"a run with no skips printed an empty SKIPS section, which is indistinguishable "
        f"from a run whose skips were not recorded:\n{text}"
    )


# --------------------------------------------------------------------------
# A baseline this run cannot be compared against.
#
# This tool issues no verdict, and that is no protection: the 36 "regressions"
# that started salesagent-dxb3j were read off the shape of the section above,
# and a labelled table of nodeids is exactly as quotable as a verdict. So the
# cross-tree claim takes the same refusals the gate does.
# --------------------------------------------------------------------------


def test_a_baseline_with_no_identity_is_refused_and_named(tmp_path: pathlib.Path) -> None:
    base = _write_run(tmp_path / "base", {"unit": {"t.py::one": ("failed", "boom")}}, commit=None)
    run = _write_run(tmp_path / "run", {"unit": {"t.py::one": ("passed", "")}})

    text = _render(run, {"exec": base})

    assert "REFUSED" in text, f"a baseline nothing attributes to a tree must be refused:\n{text}"
    assert "exec" in text, f"the refusal must name WHICH baseline:\n{text}"
    assert "FIXED (failed there, passes here)" not in text, (
        f"the delta must not be printed over an unattributable baseline:\n{text}"
    )


def test_a_refused_baseline_does_not_exit_zero(tmp_path: pathlib.Path) -> None:
    """A section somebody asked for and did not get is not a successful report.

    Same class as the unreadable run directory this tool already exits 2 for.
    """
    base = _write_run(tmp_path / "base", {"unit": {"t.py::one": ("passed", "")}}, commit=None)
    run = _write_run(tmp_path / "run", {"unit": {"t.py::one": ("passed", "")}})

    assert main([str(run), "--baseline", f"exec={base}"]) == 2
    assert main([str(run), "--baseline", f"exec={base}", "--allow-unidentified"]) == 0
    assert main([str(run)]) == 0, "a report with no baseline makes no cross-tree claim and is unaffected"


def test_one_unattributable_baseline_does_not_suppress_a_comparable_one(tmp_path: pathlib.Path) -> None:
    """Refused per baseline, not once for the batch."""
    anonymous = _write_run(tmp_path / "anon", {"unit": {"t.py::one": ("passed", "")}}, commit=None)
    known = _write_run(tmp_path / "known", {"unit": {"t.py::one": ("failed", "boom")}})
    run = _write_run(tmp_path / "run", {"unit": {"t.py::one": ("passed", "")}})

    text = _render(run, {"anon": anonymous, "known": known})

    assert "REFUSED" in text.split("AGAINST BASELINE known")[0], "the anonymous one is refused"
    assert "FIXED (failed there, passes here): 1" in text.split("AGAINST BASELINE known")[1], (
        f"the comparable one must still be graded:\n{text}"
    )


def test_the_run_states_its_own_tree_even_with_no_baseline(tmp_path: pathlib.Path) -> None:
    """Printed, not remembered. The attribution belongs beside the numbers."""
    run = _write_run(tmp_path / "run", {"unit": {"t.py::one": ("passed", "")}})

    assert "1111111111 (work/lane, clean, 9.0.0b3+sa.test.0)" in _render(run)

    anonymous = _write_run(tmp_path / "anon", {"unit": {"t.py::one": ("passed", "")}}, commit=None)
    assert "IDENTITY UNKNOWN" in _render(anonymous)


def test_creative_agent_429s_are_counted_and_the_tests_that_saw_one_are_named(tmp_path) -> None:
    """A rate-limited agent must read as an environment limit, not as scenario failures."""
    limited = '"POST https://creative-agent.adcp.test:8443/api/creative-agent/mcp "HTTP/1.1 429 Too Many Requests"'
    served = '"POST https://creative-agent.adcp.test:8443/api/creative-agent/mcp "HTTP/1.1 200 OK"'
    run = tmp_path / "run"
    _write_run(run, {})
    (run / "integration.json").write_text(
        json.dumps(
            {
                "tests": [
                    {"nodeid": "t.py::test_limited", "outcome": "failed", "call": {"stderr": limited}},
                    {"nodeid": "t.py::test_unrelated", "outcome": "failed", "call": {"stderr": served}},
                    {"nodeid": "t.py::test_green", "outcome": "passed", "call": {"stderr": limited}},
                ]
            }
        )
    )
    (run / "containers").mkdir()
    (run / "containers" / "sa-1-tls-proxy-1.log").write_text(
        '1.2.3.4 - - [x] "POST /api/creative-agent/mcp HTTP/1.1" 429 108 "-"\n'
        '1.2.3.4 - - [x] "POST /api/creative-agent/mcp HTTP/1.1" 200 512 "-"\n'
    )
    out = io.StringIO()
    report(run, {}, limit=5, out=out)
    section = out.getvalue().split("CREATIVE AGENT RATE LIMITS", 1)[1].split("STORYBOARD", 1)[0]

    assert "access log: 1" in section, section
    assert "carries one: 1" in section and "integration::t.py::test_limited" in section, section
    assert "test_unrelated" not in section and "test_green" not in section, section

    quiet = _write_run(tmp_path / "quiet", {"unit": {"t.py::test_a": ("failed", "boom")}})
    out = io.StringIO()
    report(quiet, {}, limit=5, out=out)
    assert "none recorded" in out.getvalue().split("CREATIVE AGENT RATE LIMITS", 1)[1].split("STORYBOARD", 1)[0]
