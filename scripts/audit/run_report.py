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

    IT STILL MAKES A CROSS-TREE CLAIM, which is why it takes the same identity
    refusals the gate does (``scripts/audit/run_identity.py``): a baseline that
    carries no ``meta.json``, or one whose sha has no ancestry relationship with
    this run's, is refused per baseline and named. Not issuing a verdict is no
    protection -- the 36 "regressions" that started this were read off THIS
    section's shape, and a labelled table of nodeids is exactly as quotable as a
    verdict. ``--allow-unidentified`` proceeds past the missing-identity half
    for the retained runs that predate the field, and labels the section.
    The identity of the run itself is printed unconditionally, baseline or not.

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
from scripts.audit.run_identity import IDENTITY_FILE, describe, read, refusals, report_refusals

#: Where ``run_all_tests.sh`` keeps the runner's per-protocol summaries inside a run.
#: A subdirectory, not ``<run>/*.json``: ``compare_runs.py`` globs that level as pytest
#: reports and would read a summary (no ``tests`` key) as an empty suite.
STORYBOARD_SUBDIR = "storyboard"
#: This repository, for the ancestry question. Any worktree answers it -- they share
#: one object database -- so the tool's own location is a steadier root than the cwd.
REPO = Path(__file__).resolve().parents[2]
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
        if report.name == IDENTITY_FILE:
            continue  # beside the reports, not one of them — see present_suites()
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


def _skip_cause(reason: str) -> str:
    """What pytest recorded as the cause of a skip, or a STATED absence.

    ``outcomes()`` hands over the longrepr of the phase that did not pass, which for
    a skip is the ``(file, lineno, "Skipped: <cause>")`` triple -- behind the xdist
    worker banner, which is why the cause is searched for rather than read off the
    first line. Grouping on the banner instead produces a histogram by WORKER, which
    is a meaningless answer that looks like an answer.

    A skip with no recorded cause returns a named bucket rather than an empty string,
    so it is counted and shown instead of disappearing into a blank row.
    """
    marker = "Skipped: "
    at = reason.find(marker)
    if at < 0:
        return "(no cause recorded)"
    return reason[at + len(marker) :].strip().splitlines()[0].rstrip("'\")").strip()[:104] or "(no cause recorded)"


def skips_section(run: Run, out) -> None:
    """Every skip in the run, grouped by cause, per suite.

    WHY THIS EXISTS, and it is not tidiness. ``suite_table`` above prints a
    ``skipped`` COLUMN, and a column is a number without its cause -- so
    ``unit 6646 passed / 3 failed / 40 skipped`` was read as holding steady against
    another run's ``6677 / 3 / 9`` when the two differ by THIRTY-ONE guards that did
    not ask their question. The pinned AdCP bundle was absent in one tree, so
    ``requires_pinned_bundle`` skipped them, and the smaller graded set reported the
    better-looking number (salesagent-13wao).

    The skip itself is correct and stays: a guard with no valid input must refuse,
    never pass. What failed was the AGGREGATE, which is why this is a reporting fix
    and not a change to any guard. ``compare_runs`` already catches this ACROSS runs
    -- pass -> skip is a regression there, and it names all thirty-one -- so this
    covers the case with no baseline to compare against, where nothing else looks.

    Three outcomes, deliberately distinguishable, because "no skips" and "skips whose
    cause was not recorded" and "skips with causes" must not render the same:
    a cause breakdown, a counted ``(no cause recorded)`` bucket, or an explicit
    statement that the run recorded none.
    """
    print("\nSKIPS (a skip is not a pass: these items did not ask their question)", file=out)
    seen = False
    for suite, tests in run.items():
        causes = collections.Counter(_skip_cause(reason) for outcome, reason in tests.values() if outcome == "skipped")
        if not causes:
            continue
        seen = True
        print(f"  {suite}: {sum(causes.values())} skipped", file=out)
        for cause, count in causes.most_common():
            print(f"      {count:>5}  {cause}", file=out)
    if not seen:
        print("  no suite in this run recorded a skip", file=out)


#: A creative-agent response the seller's HTTP client logged as 429, in a test's captured
#: output, and the same response in the TLS front's access log.
_AGENT_429_IN_OUTPUT = re.compile(r"/api/creative-agent/\S*\s+\"HTTP/1\.1 429")
_AGENT_429_IN_PROXY_LOG = re.compile(r"\"POST /api/creative-agent/\S* HTTP/1\.1\" 429 ")


def rate_limit_section(run_dir: Path, limit: int, out) -> None:
    """Count the creative agent's 429s, and the non-passing tests that saw one.

    The reference creative agent rate-limits per client IP, and behind the TLS front every
    test process shares one IP. When the budget runs out the seller correctly answers
    RATE_LIMITED or SERVICE_UNAVAILABLE, so the tests that hit it fail as if a scenario
    were wrong (innet_291026_2322: ten failures across three suites, one cause). Naming
    the 429s here lets a reader see an environment limit before chasing ten scenarios.
    """
    print("\nCREATIVE AGENT RATE LIMITS (an environment limit, not a scenario result)", file=out)
    proxy = sum(
        len(_AGENT_429_IN_PROXY_LOG.findall(log.read_text(errors="replace")))
        for log in sorted((run_dir / "containers").glob("*tls-proxy*.log"))
    )
    hit: list[tuple[str, str, str]] = []
    for report_path in sorted(run_dir.glob("*.json")):
        if report_path.name == IDENTITY_FILE:
            continue
        for test in json.loads(report_path.read_text()).get("tests", []):
            if test.get("outcome") == "passed":
                continue
            captured = " ".join(
                str(test.get(phase, {}).get(stream, ""))
                for phase in ("setup", "call", "teardown")
                for stream in ("stdout", "stderr", "log")
            )
            if _AGENT_429_IN_OUTPUT.search(captured):
                hit.append((report_path.stem, test["outcome"], test["nodeid"]))
    if not proxy and not hit:
        print("  none recorded", file=out)
        return
    print(f"  429s in the TLS front's access log: {proxy}", file=out)
    print(f"  non-passing tests whose captured output carries one: {len(hit)}", file=out)
    for suite, outcome, nodeid in hit[:limit]:
        print(f"    {outcome:8} {suite}::{nodeid}", file=out)
    if len(hit) > limit:
        print(f"    ... {len(hit) - limit} more", file=out)


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
    label: str,
    base: Run,
    base_sb: dict[str, dict],
    run: Run,
    run_sb: dict[str, dict],
    limit: int,
    out,
    unattributed: bool = False,
) -> None:
    if unattributed:
        print("  (UNATTRIBUTED: no tree is named for one of these runs)", file=out)
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
    run_dir: Path,
    baselines: dict[str, Path],
    limit: int,
    out=sys.stdout,
    groups_json: Path | None = None,
    allow_unidentified: bool = False,
) -> int:
    """Print the report; return how many requested baselines were refused.

    The body is printed either way -- a run's own suite table, skips and
    failures make no cross-tree claim and are worth reading regardless. The
    RETURN is what stops a refused ``--baseline`` from exiting 0: this tool
    issues no verdict, but a section somebody asked for and did not get is the
    same class of event as an unreadable run directory, which already exits 2.
    """
    run = read_run(run_dir)
    run_sb = read_storyboard(run_dir)
    print(f"RUN {run_dir}", file=out)
    print(f"  tree {describe(read(run_dir))}", file=out)
    suite_table(run, out)
    skips_section(run, out)
    rate_limit_section(run_dir, limit, out)
    storyboard_section(run_sb, out)
    groups = failure_groups(run, run_sb)
    groups_section(groups, limit, out)
    if groups_json is not None:
        groups_json.write_text(json.dumps(groups, indent=1))
        print(f"\n  groups written to {groups_json}", file=out)
    failure_details(run, limit, out)
    refused = 0
    for label, path in baselines.items():
        # Per baseline, not once for the batch: one unattributable baseline must
        # not suppress a second that is perfectly comparable, and the refusal has
        # to name which of several it is about.
        print(f"\nAGAINST BASELINE {label}", file=out)
        hard, waivable, identities = refusals(path, run_dir, REPO, (label, "run"))
        if not report_refusals(hard, waivable, identities, allow_unidentified, out):
            refused += 1
            continue
        baseline_section(label, read_run(path), read_storyboard(path), run, run_sb, limit, out, bool(waivable))
    return refused


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
    parser.add_argument(
        "--allow-unidentified",
        action="store_true",
        help="compare against a baseline that records no tree (runs retained from before meta.json existed)",
    )
    args = parser.parse_args(argv)
    try:
        refused = report(
            args.run,
            dict(args.baseline),
            args.details,
            groups_json=args.groups_json,
            allow_unidentified=args.allow_unidentified,
        )
    except FileNotFoundError as exc:
        print(f"NOT MEASURED: {exc}", file=sys.stderr)
        return 2
    return 2 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
