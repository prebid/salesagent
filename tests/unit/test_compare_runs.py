"""The run comparator must state what it compared before it states a verdict.

``scripts/audit/compare_runs.py`` is the tool every regression claim in
salesagent-v03pe rested on, and it enumerated suites from the NEW directory
only. A suite that ran in the baseline and died in the new run produced no
report, was therefore never enumerated, was never mentioned in the output, and
``CLEAN: no pre-existing test changed outcome`` printed over its absence.

That is not hypothetical: ``test-results/innet_080926_1108/`` is missing
``bdd_inprocess.json`` -- 8182 nodeids, more than every other suite except
integration -- because the suite was SIGKILLed at 957s. Both that run's
``.suites`` manifest and the baseline's declare it. The old tool named it
nowhere.

So the property under test is not "the verdict is right"; it is that the
verdict CANNOT be issued over a denominator the tool never stated. Every test
here builds two run directories on disk and reads the tool's own stdout and
exit status, because those two are what a gate consumes.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[2]


def _load_compare_runs():
    path = _REPO / "scripts" / "audit" / "compare_runs.py"
    spec = importlib.util.spec_from_file_location("compare_runs", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compare_runs = _load_compare_runs()


def _write_run(
    directory: pathlib.Path,
    reports: dict[str, dict[str, str]],
    declared: list[str] | None = None,
) -> pathlib.Path:
    """One run directory: ``<suite>.json`` reports plus the ``.suites`` manifest.

    ``reports`` maps suite name -> {nodeid: outcome}. ``declared`` is what the
    run SAID it would execute (``run_all_tests.sh`` writes it); pass ``None`` to
    model an older run directory that carries no manifest.
    """
    directory.mkdir(parents=True, exist_ok=True)
    for suite, tests in reports.items():
        payload = {"tests": [{"nodeid": nodeid, "outcome": outcome} for nodeid, outcome in tests.items()]}
        (directory / f"{suite}.json").write_text(json.dumps(payload))
    if declared is not None:
        (directory / ".suites").write_text(",".join(declared) + "\n")
    return directory


def _run(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    argv = ["compare_runs.py", str(tmp_path / "old"), str(tmp_path / "new"), *args]
    original, sys.argv = sys.argv, argv
    try:
        status = compare_runs.main()
    finally:
        sys.argv = original
    return status, capsys.readouterr().out


# --------------------------------------------------------------------------
# The defect: a whole vanished suite reads clean.
# --------------------------------------------------------------------------


def test_a_suite_that_vanished_from_the_new_run_fails_and_is_named(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The innet_080926_0943 -> innet_080926_1108 reproduction, in miniature.

    Both runs declare ``bdd_inprocess``; only the baseline produced a report.
    Every other suite is byte-identical, so the old tool printed CLEAN and
    exited 0 without ever writing the string ``bdd_inprocess``.
    """
    unit = {"tests/unit/test_a.py::test_one": "passed"}
    bdd = {f"tests/bdd/test_uc001.py::test_{i}": "passed" for i in range(8182)}
    _write_run(tmp_path / "old", {"unit": unit, "bdd_inprocess": bdd}, declared=["unit", "bdd_inprocess"])
    _write_run(tmp_path / "new", {"unit": unit}, declared=["unit", "bdd_inprocess"])

    status, out = _run(tmp_path, capsys)

    assert status != 0, f"a suite present in the baseline and absent from the new run must fail:\n{out}"
    assert "bdd_inprocess" in out, f"the vanished suite must be NAMED, not silently dropped:\n{out}"
    assert "CLEAN" not in out, f"nothing may print CLEAN while a suite went unmeasured:\n{out}"


def test_a_vanished_suite_fails_even_when_neither_run_has_a_manifest(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Older run directories carry no ``.suites``; the reports themselves still do.

    Enumerating from the new directory alone is the defect. The union of both
    sides is what makes a disappearance representable at all.
    """
    unit = {"tests/unit/test_a.py::test_one": "passed"}
    _write_run(tmp_path / "old", {"unit": unit, "integration": unit}, declared=None)
    _write_run(tmp_path / "new", {"unit": unit}, declared=None)

    status, out = _run(tmp_path, capsys)

    assert status != 0, f"a suite that ran in the baseline and not in the new run must fail:\n{out}"
    assert "integration" in out, f"the vanished suite must be named:\n{out}"


def test_a_declared_suite_missing_from_both_runs_is_not_measured(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The union of reports cannot see a suite that died in BOTH runs.

    Only the manifest can. ``run_all_tests.sh`` writes it precisely because
    "consumers cannot infer it" -- and this consumer did not read it.
    """
    unit = {"tests/unit/test_a.py::test_one": "passed"}
    _write_run(tmp_path / "old", {"unit": unit}, declared=["unit", "e2e"])
    _write_run(tmp_path / "new", {"unit": unit}, declared=["unit", "e2e"])

    status, out = _run(tmp_path, capsys)

    assert status != 0, f"a suite both runs declared and neither produced must fail:\n{out}"
    assert "e2e" in out, f"the undeclared-but-unmeasured suite must be named:\n{out}"


def test_a_suite_only_in_the_new_run_is_reported_but_not_fatal(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A new suite has no pre-existing tests, so it cannot falsify the claim.

    Failing on it would fire every time a suite is added, and a gate that cries
    wolf is how ``--update-baseline`` habits start. It is still named: a verdict
    that quietly excludes part of the run is the disease this ticket is about.
    """
    unit = {"tests/unit/test_a.py::test_one": "passed"}
    _write_run(tmp_path / "old", {"unit": unit}, declared=["unit"])
    _write_run(tmp_path / "new", {"unit": unit, "storyboard": unit}, declared=["unit", "storyboard"])

    status, out = _run(tmp_path, capsys)

    assert status == 0, f"a brand-new suite is not a regression:\n{out}"
    assert "storyboard" in out, f"it must still be named and counted:\n{out}"
    assert "NOT COMPARABLE" in out, out


def test_a_suite_named_on_the_command_line_but_absent_refuses(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unit = {"tests/unit/test_a.py::test_one": "passed"}
    _write_run(tmp_path / "old", {"unit": unit}, declared=["unit"])
    _write_run(tmp_path / "new", {"unit": unit}, declared=["unit"])

    status, out = _run(tmp_path, capsys, "bdd_inprocess.json")

    assert status != 0, f"an explicitly named suite that does not exist must refuse, not return 0:\n{out}"
    assert "bdd_inprocess" in out


# --------------------------------------------------------------------------
# The denominator: every run states what it covered.
# --------------------------------------------------------------------------


def test_the_verdict_states_how_many_suites_and_nodeids_it_covered(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bare CLEAN is a claim with no stated scope, which is the disease."""
    unit = {f"tests/unit/test_a.py::test_{i}": "passed" for i in range(7)}
    integration = {f"tests/integration/test_b.py::test_{i}": "passed" for i in range(3)}
    for side in ("old", "new"):
        _write_run(tmp_path / side, {"unit": unit, "integration": integration}, declared=["unit", "integration"])

    status, out = _run(tmp_path, capsys)

    assert status == 0, out
    assert "2 of 2 suites" in out, f"the suite denominator must be printed:\n{out}"
    assert "10" in out, f"the nodeid denominator (7 + 3 shared) must be printed:\n{out}"
    assert "CLEAN" in out
    assert out.rstrip().splitlines()[-1] != "CLEAN: no pre-existing test changed outcome", (
        f"the verdict line must name its own scope, not stand alone:\n{out}"
    )


def test_an_outcome_change_is_still_a_regression(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The safety claim the tool exists for must survive the denominator work."""
    _write_run(tmp_path / "old", {"unit": {"tests/unit/test_a.py::test_one": "passed"}}, declared=["unit"])
    _write_run(tmp_path / "new", {"unit": {"tests/unit/test_a.py::test_one": "failed"}}, declared=["unit"])

    status, out = _run(tmp_path, capsys)

    assert status != 0, out
    assert "passed -> failed" in out, out


# --------------------------------------------------------------------------
# The documented noise floor, computed instead of held in the reader's head.
# --------------------------------------------------------------------------


def test_transport_reparametrization_is_reported_as_such_not_as_a_defect(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Measured on real data: 19 of 19 removed bdd nodeids were re-parametrized.

    The docstring told the reader to discount ~19 removed nodeids as selection
    instability while the code printed every one of them under "always a
    defect" and exited 1. The pairing is derivable -- same base nodeid, different
    ``[transport-example]`` suffix -- so it is derived, not tolerated by count.
    """
    base = "tests/bdd/test_uc010.py::test_sections_absent"
    # Shaped like the real suite -- 8163 stable nodeids around 19 that flap --
    # rather than a bare pair, which would grade zero pre-existing tests and be
    # refused as an absent run on those grounds instead of these.
    stable = {f"tests/bdd/test_uc010.py::test_stable_{i}[rest-full]": "passed" for i in range(5)}
    _write_run(
        tmp_path / "old",
        {"bdd_inprocess": {**stable, f"{base}[rest-full]": "passed"}},
        declared=["bdd_inprocess"],
    )
    _write_run(
        tmp_path / "new",
        {"bdd_inprocess": {**stable, f"{base}[mcp-full]": "passed"}},
        declared=["bdd_inprocess"],
    )

    status, out = _run(tmp_path, capsys)

    assert status == 0, f"a re-parametrized nodeid is not a disappearance:\n{out}"
    assert "RE-PARAMETRIZED" in out, f"it must still be reported, not hidden:\n{out}"
    assert "always a defect" not in out, f"a re-parametrized nodeid must not be filed as a defect:\n{out}"


def test_a_genuinely_disappeared_nodeid_is_still_a_defect(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The pairing must not become a blanket excuse for removals."""
    _write_run(
        tmp_path / "old",
        {"bdd_inprocess": {"tests/bdd/test_uc010.py::test_gone[rest-full]": "passed"}},
        declared=["bdd_inprocess"],
    )
    _write_run(
        tmp_path / "new",
        {"bdd_inprocess": {"tests/bdd/test_uc010.py::test_other[mcp-full]": "passed"}},
        declared=["bdd_inprocess"],
    )

    status, out = _run(tmp_path, capsys)

    assert status != 0, f"a removed nodeid with no same-base replacement is a defect:\n{out}"
    assert "test_gone" in out, out


# --------------------------------------------------------------------------
# CLEAN must require that something was actually compared.
#
# The denominator work above made the SCOPE line honest -- it said "0 of 0
# suites, 0 shared nodeids" quite correctly. But the verdict word and the exit
# code are what a caller reads, and `if compare_runs; then ok` saw a pass. That
# is the same defect one level down: not-measured reading as fine.
#
# The way in is mundane. A typo, a run id that never produced a directory, or
# running the tool from a worktree where the relative path resolves to nothing
# -- test-results/ is gitignored and exists in one worktree only. BOTH
# directories were wrong and it said CLEAN.
# --------------------------------------------------------------------------


def test_directories_that_do_not_exist_refuse(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Neither path exists, so nothing was measured and nothing may be claimed."""
    status, out = _run(tmp_path, capsys)

    assert status != 0, f"a comparison of two absent directories must not pass:\n{out}"
    assert "CLEAN" not in out, f"nothing may print CLEAN having compared nothing:\n{out}"
    assert "does not exist" in out, f"the tool must say WHICH path it could not read:\n{out}"


def test_one_directory_that_does_not_exist_refuses(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_run(tmp_path / "old", {"unit": {"tests/unit/test_a.py::test_one": "passed"}}, declared=["unit"])

    status, out = _run(tmp_path, capsys)

    assert status != 0, out
    assert "CLEAN" not in out, out


def test_run_directories_holding_no_reports_refuse(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The directories exist and are empty: zero suites accounted for."""
    (tmp_path / "old").mkdir()
    (tmp_path / "new").mkdir()

    status, out = _run(tmp_path, capsys)

    assert status != 0, f"zero suites accounted for is not a clean run:\n{out}"
    assert "CLEAN" not in out, out
    assert "NOT MEASURED" in out, out


def test_reports_that_share_no_nodeids_refuse(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A suite compared on both sides, and nothing in it was graded.

    Suites can be accounted for while the comparison still measures nothing --
    two empty reports compare successfully and grade zero nodeids.
    """
    _write_run(tmp_path / "old", {"unit": {}}, declared=["unit"])
    _write_run(tmp_path / "new", {"unit": {}}, declared=["unit"])

    status, out = _run(tmp_path, capsys)

    assert status != 0, f"zero shared nodeids graded is not a clean run:\n{out}"
    assert "CLEAN" not in out, out
