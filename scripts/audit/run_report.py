#!/usr/bin/env python3
"""Read one run directory and say what it holds, suite by suite and check by check.

The per-suite JSON reports under ``test-results/<run>/`` answer "what ran and how it
ended", but three things a reader wants are not on their face, and each has been
misread at least once:

STORYBOARD SCORE
    The conformance suite materializes only failures and skips as pytest items, so
    ``storyboard.json`` reads ``0 passed`` whatever the runner scored. The score is the
    runner's own per-protocol summary (``<run>/storyboard/summary_<protocol>.json``,
    kept there by ``run_all_tests.sh``): passed / failed / skipped / storyboards
    executed, one line per protocol, and the failing check ids side by side so a
    protocol failing what its sibling passes is visible as a DISPARITY row.

FAILURE DETAIL
    A count says a suite is red; the nodeid and the last line of its traceback say
    why. Every failing and erroring node is listed with that line.

DELTA AGAINST A BASELINE
    With ``--baseline LABEL=<run-dir>``, every nodeid is graded against that run:
    FIXED (failed there, passes here), REGRESSED (passed there, not passing here --
    xfail and skip count, they remove grading while looking green), NEW FAILURE
    (absent there, failing here) and GONE (failed there, absent here). Storyboard
    checks get the same treatment per protocol when the baseline carries summaries.
    ``scripts/audit/compare_runs.py`` remains the strict regression GATE; this is
    the readable account of the same data, and it never issues a verdict.

Usage::

    python3 -m scripts.audit.run_report test-results/innet_120926_1917 \
        --baseline exec=test-results/innet_120926_1634 \
        --baseline feature=/path/to/other/test-results/innet_120926_1730

Exit 2 on an unreadable run directory; 0 otherwise (a report, not a gate).
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

from scripts.audit.compare_runs import outcomes

#: Where ``run_all_tests.sh`` keeps the runner's per-protocol summaries inside a run.
#: A subdirectory, not ``<run>/*.json``: ``compare_runs.py`` globs that level as pytest
#: reports and would read a summary (no ``tests`` key) as an empty suite.
STORYBOARD_SUBDIR = "storyboard"
PROTOCOLS = ("mcp", "a2a")
OUTCOMES = ("passed", "failed", "error", "xfailed", "xpassed", "skipped")
NOT_PASSING = ("failed", "error", "xfailed", "skipped")

Run = dict[str, dict[str, tuple[str, str]]]  # suite -> nodeid -> (outcome, reason)


def read_run(run: Path) -> Run:
    """Every suite report in *run*: ``suite -> nodeid -> (outcome, last reason)``."""
    if not run.is_dir():
        raise FileNotFoundError(f"run directory does not exist: {run}")
    suites: Run = {}
    for report in sorted(run.glob("*.json")):
        suites[report.stem] = outcomes(report)
    if not suites:
        raise FileNotFoundError(f"{run}: no per-suite reports")
    return suites


def read_storyboard(run: Path) -> dict[str, dict]:
    """The runner's summary per protocol, or an empty mapping when the run kept none."""
    found: dict[str, dict] = {}
    for protocol in PROTOCOLS:
        path = run / STORYBOARD_SUBDIR / f"summary_{protocol}.json"
        if path.exists():
            found[protocol] = json.loads(path.read_text())
    return found


def check_id(failure: dict) -> str:
    return f"{failure.get('track')}/{failure.get('storyboard_id')}/{failure.get('step_id')}"


def failing_checks(summary: dict) -> dict[str, dict]:
    return {check_id(f): f for f in summary.get("failures", [])}


def _last_line(reason: str) -> str:
    """The line that names the failure: pytest's last ``E   ...`` line, else the last line."""
    lines = [line for line in reason.strip().splitlines() if line.strip()]
    asserted = [line for line in lines if line.startswith("E ")]
    return (asserted or lines)[-1].strip()[:140] if lines else ""


_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_NUMBER = re.compile(r"\b\d+\b")
_HEX = re.compile(r"\b[0-9a-f]{8,}\b")


def signature(reason: str) -> str:
    """The failure line with its instance-specific parts blanked: quoted strings, numbers, hashes.

    Two failures with the same signature almost always share one cause -- a renamed
    symbol, an added positional argument, a code the pin spells differently -- so the
    signature is the grouping key for triage. It is deliberately coarse; a group that
    turns out to hold two causes is split by whoever reads it, which is cheaper than
    reading 160 tracebacks one by one.
    """
    line = _last_line(reason)
    line = _QUOTED.sub("…", line)
    line = _HEX.sub("#", line)
    return _NUMBER.sub("#", line)


def failure_groups(run: Run, storyboard: dict[str, dict]) -> list[dict]:
    """Every failing node of *run*, grouped by signature, largest group first.

    Storyboard checks come from the runner summaries, not from the pytest items, and
    are grouped by their own reason (kind + text) with the protocols that fail them.
    """
    by_sig: dict[str, dict] = {}
    for suite, tests in run.items():
        for nodeid, (outcome, reason) in tests.items():
            if outcome not in ("failed", "error"):
                continue
            if suite == "storyboard" and storyboard and "test_storyboard_check[" in nodeid:
                # The same check, once per protocol, is already a runner failure below;
                # its pytest item says only "assert 'fail' != 'fail'" and would double-count.
                continue
            g = by_sig.setdefault(signature(reason), {"signature": signature(reason), "nodeids": [], "sample": reason})
            g["nodeids"].append(f"{suite}::{nodeid}")
    for protocol, s in storyboard.items():
        for check, f in failing_checks(s).items():
            key = f"storyboard {f.get('reason_kind')}: {_NUMBER.sub('#', str(f.get('reason', ''))[:140])}"
            g = by_sig.setdefault(key, {"signature": key, "nodeids": [], "sample": str(f.get("reason", ""))})
            g["nodeids"].append(f"storyboard-runner::{protocol}::{check}")
    groups = sorted(by_sig.values(), key=lambda g: (-len(g["nodeids"]), g["signature"]))
    for index, g in enumerate(groups):
        g["id"] = f"g{index:02d}"
        g["count"] = len(g["nodeids"])
        g["suites"] = sorted({n.split("::")[0] for n in g["nodeids"]})
        g["files"] = dict(collections.Counter(n.split("::")[1] for n in g["nodeids"]))
    return groups


def groups_section(groups: list[dict], limit: int, out) -> None:
    print(f"\nFAILURE GROUPS (by signature): {len(groups)}", file=out)
    for g in groups[:limit]:
        print(f"\n  {g['id']}  {g['count']:>4}  {'/'.join(g['suites'])}  {g['signature']}", file=out)
        for f, c in sorted(g["files"].items(), key=lambda kv: -kv[1])[:6]:
            print(f"        {c:>4}  {f}", file=out)
    if len(groups) > limit:
        print(f"\n  ... {len(groups) - limit} more groups", file=out)


# ── sections ─────────────────────────────────────────────────────────────────


def suite_table(run: Run, out) -> None:
    print("SUITES", file=out)
    header = f"  {'suite':16}{'ran':>7}" + "".join(f"{o:>8}" for o in OUTCOMES)
    print(header, file=out)
    totals: collections.Counter = collections.Counter()
    for suite, tests in run.items():
        counts = collections.Counter(o for o, _ in tests.values())
        totals.update(counts)
        row = f"  {suite:16}{len(tests):>7}" + "".join(f"{counts[o]:>8}" for o in OUTCOMES)
        if suite == "storyboard":
            row += "   (pytest items only: passes are not items, see STORYBOARD)"
        print(row, file=out)
    print(f"  {'total':16}{sum(totals.values()):>7}" + "".join(f"{totals[o]:>8}" for o in OUTCOMES), file=out)


def storyboard_section(summaries: dict[str, dict], out) -> None:
    print("\nSTORYBOARD (runner summaries)", file=out)
    if not summaries:
        print(f"  NOT MEASURED: no {STORYBOARD_SUBDIR}/summary_<protocol>.json in this run directory", file=out)
        return
    for protocol, s in summaries.items():
        print(
            f"  {protocol:4} {s.get('overall_status')}: passed={s.get('passed')} failed={s.get('failed')} "
            f"skipped={s.get('skipped')} storyboards_executed={len(s.get('storyboards_executed', []))} "
            f"sdk={s.get('sdk_version')} adcp={s.get('adcp_version')}",
            file=out,
        )
    failing = {p: failing_checks(s) for p, s in summaries.items()}
    every = sorted(set().union(*(set(f) for f in failing.values())))
    if every:
        print(f"\n  failing checks: {len(every)}" + "".join(f"{p:>6}" for p in failing), file=out)
        for check in every:
            marks = "".join(f"{'X' if check in failing[p] else '-':>6}" for p in failing)
            reason = next((failing[p][check] for p in failing if check in failing[p]), {})
            print(
                f"    {check:80}{marks}  [{reason.get('reason_kind')}] {str(reason.get('reason', ''))[:100]}", file=out
            )
    if len(failing) == 2:
        first, second = failing.values()
        disparity = sorted(set(first) ^ set(second))
        print(f"\n  DISPARITY between protocols: {len(disparity)} checks", file=out)
        for check in disparity:
            where = [p for p in failing if check in failing[p]]
            print(f"    {check}  fails only on {', '.join(where)}", file=out)
    for protocol, s in summaries.items():
        causes = s.get("skipped_by_reason") or {}
        if causes:
            print(
                f"\n  {protocol} skipped by reason: " + ", ".join(f"{k}={v}" for k, v in sorted(causes.items())),
                file=out,
            )


def failure_details(run: Run, limit: int, out) -> None:
    print("\nFAILURES (nodeid, last traceback line)", file=out)
    any_failure = False
    for suite, tests in run.items():
        red = sorted((n, r) for n, (o, r) in tests.items() if o in ("failed", "error"))
        if not red:
            continue
        any_failure = True
        print(f"\n  {suite}: {len(red)}", file=out)
        for nodeid, reason in red[:limit]:
            print(f"    {nodeid}", file=out)
            if reason:
                print(f"        {_last_line(reason)}", file=out)
        if len(red) > limit:
            print(f"    ... {len(red) - limit} more (raise --details to see them)", file=out)
    if not any_failure:
        print("  none", file=out)


def _flat(run: Run) -> dict[tuple[str, str], str]:
    return {(suite, nodeid): outcome for suite, tests in run.items() for nodeid, (outcome, _) in tests.items()}


def baseline_section(
    label: str, base: Run, base_sb: dict[str, dict], run: Run, run_sb: dict[str, dict], limit: int, out
) -> None:
    print(f"\nAGAINST BASELINE {label}", file=out)
    before, now = _flat(base), _flat(run)
    fixed = sorted(k for k, o in before.items() if o in ("failed", "error") and now.get(k) == "passed")
    regressed = sorted(k for k, o in before.items() if o == "passed" and k in now and now[k] in NOT_PASSING)
    new_failures = sorted(k for k, o in now.items() if o in ("failed", "error") and k not in before)
    gone = sorted(k for k, o in before.items() if o in ("failed", "error") and k not in now)
    for title, rows in (
        ("FIXED (failed there, passes here)", fixed),
        ("REGRESSED (passed there, not passing here)", regressed),
        ("NEW FAILURE (absent there, failing here)", new_failures),
        ("GONE (failed there, absent here)", gone),
    ):
        by_suite = collections.Counter(suite for suite, _ in rows)
        print(f"  {title}: {len(rows)}" + (f"  {dict(by_suite)}" if rows else ""), file=out)
        for suite, nodeid in rows[:limit]:
            print(f"    {now.get((suite, nodeid), before.get((suite, nodeid)))!s:8} {suite}::{nodeid}", file=out)
        if len(rows) > limit:
            print(f"    ... {len(rows) - limit} more", file=out)
    if base_sb and run_sb:
        for protocol in PROTOCOLS:
            if protocol not in base_sb or protocol not in run_sb:
                continue
            b, n = base_sb[protocol], run_sb[protocol]
            fb, fn = set(failing_checks(b)), set(failing_checks(n))
            print(
                f"  storyboard {protocol}: passed {b.get('passed')} -> {n.get('passed')}, "
                f"failed {b.get('failed')} -> {n.get('failed')}; checks fixed {len(fb - fn)}, newly failing {len(fn - fb)}",
                file=out,
            )
            for check in sorted(fb - fn):
                print(f"    fixed        {check}", file=out)
            for check in sorted(fn - fb):
                print(f"    newly failing {check}", file=out)


def report(
    run_dir: Path, baselines: dict[str, Path], limit: int, out=sys.stdout, groups_json: Path | None = None
) -> None:
    run = read_run(run_dir)
    run_sb = read_storyboard(run_dir)
    print(f"RUN {run_dir}", file=out)
    suite_table(run, out)
    storyboard_section(run_sb, out)
    groups = failure_groups(run, run_sb)
    groups_section(groups, limit, out)
    if groups_json is not None:
        groups_json.write_text(json.dumps(groups, indent=1))
        print(f"\n  groups written to {groups_json}", file=out)
    failure_details(run, limit, out)
    for label, path in baselines.items():
        baseline_section(label, read_run(path), read_storyboard(path), run, run_sb, limit, out)


def _parse_baseline(value: str) -> tuple[str, Path]:
    label, sep, path = value.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError(f"--baseline wants LABEL=<run-dir>, got {value!r}")
    return label, Path(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path)
    parser.add_argument("--baseline", action="append", type=_parse_baseline, default=[], metavar="LABEL=DIR")
    parser.add_argument("--details", type=int, default=60, help="rows listed per section (default 60)")
    parser.add_argument("--groups-json", type=Path, default=None, help="also write the failure groups as JSON here")
    args = parser.parse_args(argv)
    try:
        report(args.run, dict(args.baseline), args.details, groups_json=args.groups_json)
    except FileNotFoundError as exc:
        print(f"NOT MEASURED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
