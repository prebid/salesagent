#!/usr/bin/env python3
"""Derive what changed between two runs, by diffing their report sets.

    python3 -m scripts.compare_test_runs OLD_RESULTS_DIR NEW_RESULTS_DIR

Every suite already writes ``test-results/<tag>/<suite>.json`` carrying a nodeid
and an outcome per test, so what a branch broke does not have to be declared
anywhere — it is a dict diff over two of those directories, joined on
``(suite, nodeid)``. Nothing to maintain, nothing to drift, and regenerable from
any commit by checking it out and re-running.

BOTH DIRECTIONS ARE REPORTED, and that is the point. ``passed -> failed`` is
the transition a green suite already tells you about. ``passed -> xfailed`` is
the one nothing tells you about: coverage that quietly stopped being graded. A
merge in this repo once reverted 205 passing scenarios that way while every
layer stayed green, because they slid to xfail instead of failing. A tool that
only counted new failures would have called that merge clean.

Tests present on one side only are reported too: deleting a test and xfailing it
remove the same amount of grading.

READING THE OUTPUT. The join key is the nodeid, so anything that rewrites a
nodeid without changing what it grades — reordering a ``parametrize`` list, say —
shows up as a matched pair of removals and additions with the same outcome, not
as a transition. Real: between ``innet_040926_0437`` and ``innet_040926_0616``,
27 uc010 scenarios appear on both lists purely because their ``[mcp-…]`` and
``[rest-…]`` ids swapped places. Equal counts on both sides at the same outcome
is the signature; a genuine loss is unbalanced.

THE PINNED BASELINE for the one-tool-registry migration
(``docs/design/one-tool-registry.md``):

    commit      e6140f5d7
    report set  test-results/innet_040926_0616/   (8 suites, 22050 outcomes)

That commit is not a guess from the directory's timestamp — report sets do not
record the commit they ran, so it was measured from the reports themselves:
nodeids added by aab245773 (07:54) are present, and the one test deleted by
e6140f5d7 (08:10) is absent while the rest of its file is collected. Re-running
the suite at e6140f5d7 regenerates an equivalent set.

Related: ``scripts/compare_bdd_runs.py`` answers the same question for a single
pair of bdd report FILES, and adds a per-use-case breakdown this does not.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict

from scripts._suite_reports import scan_suite_outcomes

#: Outcomes that mean "this test is no longer grading anything", worst first.
#: ``xpassed`` sits here because a strict-xfail xpass is a failure and a
#: non-strict one is a graduation nobody performed — either way, not grading.
_UNGRADED = ("error", "failed", "xpassed", "xfailed", "skipped")


def _severity(old: str, new: str) -> tuple[int, str, str]:
    """Sort key putting the transitions worth acting on at the top.

    A regression (was grading, now is not) outranks an improvement, which
    outranks a change between two ungraded states. Ties break alphabetically so
    the report is byte-stable across runs.
    """
    if old == "passed" and new != "passed":
        rank = 0
    elif old != "passed" and new == "passed":
        rank = 1
    else:
        rank = 2
    return (rank, old, new)


def _label(old: str, new: str) -> str:
    if old == "passed" and new == "xfailed":
        return "  <== SILENT COVERAGE REGRESSION"
    if old == "passed" and new in _UNGRADED:
        return "  <== REGRESSION"
    if old in _UNGRADED and new == "passed":
        return "  (improvement)"
    return ""


def transitions(
    old: dict[tuple[str, str], str], new: dict[tuple[str, str], str]
) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """Group the joinable keys by their ``(old outcome, new outcome)`` pair."""
    grouped: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for key in old.keys() & new.keys():
        grouped[(old[key], new[key])].append(key)
    return grouped


def _print_group(header: str, keys: list[tuple[str, str]], limit: int) -> None:
    print(f"\n{header}")
    for suite, nodeid in sorted(keys)[:limit]:
        print(f"    {suite}: {nodeid}")
    if len(keys) > limit:
        print(f"    ... and {len(keys) - limit} more (raise --limit to see them)")


def report(old_dir: str, new_dir: str, limit: int) -> None:
    old = scan_suite_outcomes(old_dir)
    new = scan_suite_outcomes(new_dir)

    print(f"OLD  {old_dir}  ({len(old)} outcomes, suites: {', '.join(sorted({s for s, _ in old}))})")
    print(f"NEW  {new_dir}  ({len(new)} outcomes, suites: {', '.join(sorted({s for s, _ in new}))})")
    print(f"\nOLD outcome totals: {dict(sorted(Counter(old.values()).items()))}")
    print(f"NEW outcome totals: {dict(sorted(Counter(new.values()).items()))}")

    grouped = transitions(old, new)
    joined = sum(len(keys) for keys in grouped.values())
    changed = {pair: keys for pair, keys in grouped.items() if pair[0] != pair[1]}
    unchanged = {pair: len(keys) for pair, keys in grouped.items() if pair[0] == pair[1]}

    print(f"\nJoined on (suite, nodeid): {joined}")
    print("=" * 78)
    print(f"TRANSITIONS: {sum(len(k) for k in changed.values())} tests changed outcome")
    print("=" * 78)
    for old_outcome, new_outcome in sorted(changed, key=lambda p: _severity(*p)):
        keys = changed[(old_outcome, new_outcome)]
        _print_group(
            f"{old_outcome} -> {new_outcome}: {len(keys)}{_label(old_outcome, new_outcome)}",
            keys,
            limit,
        )
    if not changed:
        print("\n  (none — every joined test kept its outcome)")

    print("\n" + "-" * 78)
    print(f"UNCHANGED: {dict(sorted((f'{o}->{n}', c) for (o, n), c in unchanged.items()))}")

    for side, gone, outcomes in (("OLD", old.keys() - new.keys(), old), ("NEW", new.keys() - old.keys(), new)):
        if not gone:
            continue
        by_outcome: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for key in gone:
            by_outcome[outcomes[key]].append(key)
        word = "removed since OLD" if side == "OLD" else "added since OLD"
        print("\n" + "-" * 78)
        print(f"ONLY IN {side} ({word}): {len(gone)}")
        for outcome in sorted(by_outcome):
            _print_group(f"  was {outcome}: {len(by_outcome[outcome])}", by_outcome[outcome], limit)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("old_dir", help="baseline test-results/<tag>/ directory")
    parser.add_argument("new_dir", help="current test-results/<tag>/ directory")
    parser.add_argument("--limit", type=int, default=40, help="max nodeids listed per group (default: 40)")
    args = parser.parse_args(argv)

    report(args.old_dir, args.new_dir, args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
