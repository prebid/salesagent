#!/usr/bin/env python3
"""Compare two test runs test-by-test, and refuse to summarise.

Aggregate counts cannot establish that a change was safe. A suite can hold its
passed count exactly while silently swapping which tests pass, and a change that
adds tests moves every total at once so nothing can be read from the movement.
The only claim worth making is per-test: THIS nodeid had THIS outcome before and
THIS outcome now, and here is why.

The comparison is deliberately split, because the two halves support different
claims:

PRE-EXISTING nodeids (present in both runs)
    The safety claim. A change that adds test files must not perturb a single
    test that already existed. Any outcome change here is a regression until
    individually explained -- including a change to a MORE tolerant outcome, since
    pass -> xfail and pass -> skip both remove grading while looking green.

NEW nodeids (present only in the new run)
    The accounting claim. Every one must be explained by a reason the change
    intends, grouped so the reasons can be read and judged. A new test with no
    stated reason for its outcome is unaccounted for, not "fine because it is new".

EVERY VERDICT NAMES ITS DENOMINATOR, and that is the point of the reporting below
rather than a nicety. This tool used to enumerate suites from the NEW directory
alone: ``sorted(p.name for p in new.glob("*.json"))``. A suite that ran in the
baseline and died in the new run produces no report, was therefore never
enumerated, was never mentioned in the output, and ``CLEAN`` printed over its
absence. The one event a regression gate exists for -- a whole suite stopping --
was the one event it could not see. It happened:
``test-results/innet_080926_1108/`` is missing ``bdd_inprocess.json`` (8182
nodeids, SIGKILLed at 957s) and the tool named it nowhere.

So the suite set is the UNION of four sources -- each run's ``.suites`` manifest
(what ``run_all_tests.sh`` recorded that run as having executed) and each run's
reports on disk. Every suite in that set is named and counted; none is ever
folded into "compared and fine". Two of them are fatal and one is not, and the
difference is which claim the gap can falsify:

NOT MEASURED (fatal)
    Ran in the baseline and produced nothing now -- the event a regression gate
    exists for -- or declared by a manifest and produced by neither run, which
    the reports alone cannot even see.

NOT COMPARABLE (reported)
    Present only in the new run. It has no pre-existing tests, so it cannot
    falsify the safety claim, and failing on it would fire every time a suite is
    added. Naming a suite on the command line overrides this: an explicit
    request that cannot be answered is a refusal, whatever the shape.

KNOWN NOISE, measured before trusting this tool, and now COMPUTED rather than
left to the reader. Two runs of the SAME code disagree on about 19 of 8182
bdd_inprocess nodeids, and the disagreement is always the transport parameter:
the identical scenario appears as ``[rest-<example>]`` in one run and
``[mcp-<example>]`` in the other. The docstring used to say "treat ~19 removed as
selection instability" while the code printed all 19 under "always a defect" and
exited 1 -- the tool's stated limit contradicting its own verdict. The pairing is
derivable, so it is derived: a removed nodeid whose base id (everything before
``[``) also appears among the added ones is reported as RE-PARAMETRIZED, and only
an unpaired removal is a disappearance. Measured against innet_080926_0943 vs
innet_080926_1145, that accounts for 19 of 19.

CLEAN REQUIRES THAT SOMETHING WAS COMPARED. Zero suites accounted for, or zero
PRE-EXISTING nodeids among the suites that were, is NOT MEASURED and fatal --
the same defect one level down, since the SCOPE line said "0 of 0 suites, 0
shared nodeids" quite honestly while the verdict word and the exit status, which
are what a caller reads, said pass. Pointing this at two paths that do not
resolve is the ordinary way in: ``test-results/`` is gitignored and exists in
one worktree, so a relative path invoked from another finds nothing, and both
directories were wrong at once. That case is diagnosed by name before anything
else, because an absent directory and a run that produced nothing leave
identical evidence.

NEITHER RUN MAY BE ANONYMOUS, and this is the axis the tool was missing while it
had every other one. A per-nodeid delta is only as good as the identity of its
two endpoints, and a run directory used to carry none -- so this tool once
reported 36 REGRESSED / 15 FIXED between a subject and a baseline that graded
neither tree in the claim. It did exactly what it was asked; the baseline lived
in a worktree on a DIFFERENT BRANCH, and against a real base the same change was
12 fixed, 0 new failures and 2 genuine regressions. All 36 dissolved
(salesagent-dxb3j). So the same "refuse to summarise" posture applies to the
endpoints themselves, via ``scripts/audit/run_identity.py``:

NO IDENTITY (refused, waivable by name)
    Either side carries no ``meta.json``, or one recording no commit. Waivable
    ONLY because every run retained today predates the field: refusing all of
    them leaves the tool useless on the only data there is, while accepting them
    silently is the defect. ``--allow-unidentified`` proceeds and every verdict
    is then labelled UNATTRIBUTED.

DIVERGENT or DIRTY (refused, not waivable)
    Two shas with no ancestry relationship in either direction -- the exact
    shape that produced the false report -- or a tree that was dirty when it ran.
    Not waivable because identity IS recorded here and says the comparison is
    not one; the divergence refusal names the merge-base, so there is an action
    rather than a flag to reach for.

Exit status is 1 when any pre-existing test changed outcome, when any nodeid
genuinely disappeared, or when any suite went NOT MEASURED; 2 when a run
directory does not exist or the pair cannot be attributed to a tree -- so this
can gate, and so a caller can tell "regressed" from "no claim possible".

    python3 -m scripts.audit.compare_runs <baseline-dir> <new-dir> [suite.json ...]
"""

from __future__ import annotations

import collections
import json
import pathlib
import sys
from typing import NamedTuple

# Importable whether this is run as ``-m scripts.audit.compare_runs`` (repo root
# on sys.path) or as the path ``scripts/audit/compare_runs.py`` (only this
# directory is). The second is what this tool's own usage line said for its whole
# life, so it keeps working rather than being quietly broken by a shared import.
_REPO = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.audit.run_identity import IDENTITY_FILE, refusals, report_refusals  # noqa: E402

#: Written by ``run_all_tests.sh`` next to the reports: the comma-separated list
#: of suites THAT invocation ran. Its own comment says consumers cannot infer
#: this from the reports -- report mtimes do not separate "stale" from "ran early
#: in a long serial run" -- which is exactly why this consumer must read it.
SUITES_MANIFEST = ".suites"


class SuiteComparison(NamedTuple):
    """What one suite contributed to the verdict, including its denominator."""

    suite: str
    baseline: int
    new: int
    shared: int
    changed: int
    disappeared: int
    reparametrized: int
    added: int
    regressed: bool


def _without_xdist_group(nodeid: str) -> str:
    """A nodeid with xdist's ``@<group>`` scheduling suffix removed.

    Under ``--dist loadgroup`` xdist appends the ``xdist_group`` name to the
    nodeid of every marked item (``xdist/remote.py``:244) and the scheduler
    groups by splitting it back off. It is scheduling bookkeeping, not identity:
    the same test marked into a group and out of one is the same test, and
    comparing the two spellings as different nodeids reports a test that ran in
    both runs as having disappeared from one.

    The ``]`` guard is xdist's own
    (``xdist/scheduler/loadgroup.py::_split_scope``) and is here for the same
    reason -- a parametrized value may legitimately contain ``@`` -- so this
    reads a nodeid exactly the way the tool that wrote it does.
    """
    if nodeid.rfind("@") > nodeid.rfind("]"):
        return nodeid.rsplit("@", 1)[0]
    return nodeid


def outcomes(report: pathlib.Path) -> dict[str, tuple[str, str]]:
    """``nodeid -> (outcome, reason)`` for one suite's JSON report.

    The reason is the longrepr of the phase that did NOT pass. Under xdist every
    phase carries a longrepr -- a passed setup's is the worker banner
    (``[gw3] linux -- Python ...``) -- so "the first phase with a longrepr" read
    the banner for every failure and the traceback for none.
    """
    data = json.loads(report.read_text())
    result: dict[str, tuple[str, str]] = {}
    for test in data.get("tests", []):
        outcome = test.get("outcome", "?")
        reason = ""
        phases = [test.get(phase) or {} for phase in ("setup", "call", "teardown")]
        unhappy = [info for info in phases if info.get("outcome") not in (None, "passed")]
        for info in unhappy or phases:
            if info.get("longrepr"):
                reason = str(info["longrepr"])
                break
        result[_without_xdist_group(test["nodeid"])] = (outcome, reason)
    return result


def declared_suites(run: pathlib.Path) -> set[str] | None:
    """Report filenames the run's own manifest says it executed, or ``None``.

    ``None`` means the run predates the manifest, and is different from an empty
    manifest: with no declaration the reports on disk are the only evidence of
    what ran, and this tool says so rather than inventing a denominator.
    """
    manifest = run / SUITES_MANIFEST
    if not manifest.exists():
        return None
    return {f"{name.strip()}.json" for name in manifest.read_text().split(",") if name.strip()}


def present_suites(run: pathlib.Path) -> set[str]:
    """Report filenames actually on disk for a run.

    ``IDENTITY_FILE`` sits beside the reports and is not one: read as a suite it
    would be an empty report on both sides and drag the verdict into NOT
    MEASURED. Excluded by NAME rather than by a leading dot, because
    ``pathlib.Path.glob("*.json")`` matches dotfiles while ``glob.glob`` does
    not, and a run directory is enumerated through both.
    """
    return {path.name for path in run.glob("*.json")} - {IDENTITY_FILE}


def _base_nodeid(nodeid: str) -> str:
    """A nodeid with its ``[parametrization]`` suffix removed."""
    return nodeid.split("[", 1)[0]


def _pair_reparametrized(removed: set[str], added: set[str]) -> tuple[set[str], set[str]]:
    """Removed/added nodeids that differ only in their parametrization suffix.

    Paired per base id and by count, so a base that lost two nodeids and gained
    one leaves one genuine disappearance rather than excusing both.
    """
    removed_by_base: dict[str, list[str]] = collections.defaultdict(list)
    added_by_base: dict[str, list[str]] = collections.defaultdict(list)
    for nodeid in removed:
        removed_by_base[_base_nodeid(nodeid)].append(nodeid)
    for nodeid in added:
        added_by_base[_base_nodeid(nodeid)].append(nodeid)

    paired_removed: set[str] = set()
    paired_added: set[str] = set()
    for base, gone in removed_by_base.items():
        pairs = min(len(gone), len(added_by_base.get(base, ())))
        paired_removed.update(sorted(gone)[:pairs])
        paired_added.update(sorted(added_by_base[base])[:pairs])
    return paired_removed, paired_added


def _print_changed(changed: list[str], old: dict, now: dict) -> None:
    print(f"\n  PRE-EXISTING TESTS THAT CHANGED OUTCOME: {len(changed)}  <-- each needs an explanation")
    transitions = collections.Counter((old[n][0], now[n][0]) for n in changed)
    for (before, after), count in transitions.most_common():
        print(f"    {count:6}  {before} -> {after}")
        for nodeid in [n for n in changed if (old[n][0], now[n][0]) == (before, after)][:3]:
            print(f"            e.g. {nodeid[:100]}")
            if now[nodeid][1]:
                print(f"                 now: {now[nodeid][1].strip().splitlines()[-1][:90]}")


def _print_added(added: set[str], now: dict) -> None:
    print(f"\n  NEW TESTS: {len(added)}, by outcome and reason")
    by_reason: collections.Counter = collections.Counter()
    for nodeid in added:
        outcome, reason = now[nodeid]
        head = reason.strip().splitlines()[-1][:76] if reason.strip() else "(no reason recorded)"
        by_reason[(outcome, head)] += 1
    for (outcome, head), count in by_reason.most_common(14):
        print(f"    {count:6}  {outcome:9} {head}")
    if len(by_reason) > 14:
        print(f"    ... {len(by_reason) - 14} more distinct reasons")


def compare(baseline: pathlib.Path, new: pathlib.Path, suite: str) -> SuiteComparison:
    """Compare one suite present on both sides, and report its denominator."""
    old, now = outcomes(baseline / suite), outcomes(new / suite)
    shared = old.keys() & now.keys()
    added = now.keys() - old.keys()
    removed = old.keys() - now.keys()
    reparam_removed, reparam_added = _pair_reparametrized(set(removed), set(added))
    disappeared = sorted(removed - reparam_removed)
    genuinely_added = added - reparam_added

    changed = sorted(n for n in shared if old[n][0] != now[n][0])

    print(f"\n=== {suite} ===")
    print(
        f"  baseline {len(old):6}   new {len(now):6}   shared {len(shared):6}   "
        f"added {len(genuinely_added):6}   disappeared {len(disappeared):6}"
    )

    if changed:
        _print_changed(changed, old, now)
    else:
        print(f"\n  PRE-EXISTING TESTS: zero outcome changes over {len(shared)} shared nodeids")

    if reparam_removed:
        print(
            f"\n  RE-PARAMETRIZED: {len(reparam_removed)}  (same base nodeid, different [transport-example];"
            " selection instability, not a change)"
        )
        for nodeid in sorted(reparam_removed)[:3]:
            print(f"    {nodeid[:104]}")

    if disappeared:
        print(f"\n  DISAPPEARED (collected before, not now, no replacement): {len(disappeared)}  <-- always a defect")
        for nodeid in disappeared[:5]:
            print(f"    {nodeid[:104]}")

    if genuinely_added:
        _print_added(genuinely_added, now)

    return SuiteComparison(
        suite=suite,
        baseline=len(old),
        new=len(now),
        shared=len(shared),
        changed=len(changed),
        disappeared=len(disappeared),
        reparametrized=len(reparam_removed),
        added=len(genuinely_added),
        regressed=bool(changed or disappeared),
    )


def _why_unmeasured(in_old: bool, in_new: bool, declared_old: bool, declared_new: bool) -> tuple[str, bool] | None:
    """``(reason, fatal)`` for a suite that cannot be compared, else ``None``.

    Only one of the three uncomparable shapes is benign, and the difference is
    which claim it can falsify. This tool's claim is "no PRE-EXISTING test
    changed outcome": a suite that exists only in the new run has no
    pre-existing tests, so it cannot falsify that and failing on it would fire
    every time a suite is added. It is still named and counted, because a
    verdict that quietly excludes part of the run is the disease.

    The other two are fatal. A suite that ran in the baseline and produced
    nothing now is the exact event a regression gate exists for, and a suite
    both manifests declared while neither produced one was never measured at
    all -- the reports alone cannot even see that case.
    """
    if in_old and in_new:
        return None
    manifests = [side for side, was in (("baseline", declared_old), ("new", declared_new)) if was]
    declared = f" (declared by the {' and '.join(manifests)} manifest)" if manifests else ""
    if in_old:
        return f"ran in the baseline, produced NO report in the new run{declared}", True
    if in_new:
        return f"only in the new run{declared} — no baseline to compare against", False
    return f"NO report on either side{declared}", True


def _resolve_suites(
    requested: list[str],
    sides: dict[str, tuple[set[str] | None, set[str]]],
) -> list[str]:
    """Every suite that must be accounted for: what was named, declared, or ran."""
    if requested:
        return sorted({name if name.endswith(".json") else f"{name}.json" for name in requested})
    accounted: set[str] = set()
    for declared, present in sides.values():
        accounted |= present | (declared or set())
    return sorted(accounted)


def _print_header(baseline: pathlib.Path, new: pathlib.Path, sides: dict, suites: list[str]) -> None:
    print("RUN COMPARISON")
    for label, path in (("baseline", baseline), ("new", new)):
        declared, present = sides[label]
        claim = (
            f"{len(declared)} suites declared ({SUITES_MANIFEST})"
            if declared is not None
            else f"no {SUITES_MANIFEST} manifest"
        )
        print(f"  {label:8} {path}")
        print(f"           {claim}, {len(present)} reports on disk")
    print(f"  suites to account for: {len(suites)}")


def _unreadable(runs: dict[str, pathlib.Path]) -> list[str]:
    """Run directories that are not there, named by side and path.

    Checked before anything else because a path that does not resolve produces
    the same evidence as a run that produced nothing -- no manifest, no reports
    -- and the two want different words. ``test-results/`` is gitignored and
    lives in one worktree, so a relative path invoked from another resolves to
    nothing at all; that is how both directories came to be wrong at once.
    """
    return [f"{label} directory does not exist: {path}" for label, path in runs.items() if not path.is_dir()]


#: Opts past the waivable half of the identity gate. A flag and not an
#: environment variable on purpose: it has to appear in the command somebody ran
#: and in the scrollback above the verdict, which is the whole point of it being
#: acknowledgeable rather than automatic.
ALLOW_UNIDENTIFIED = "--allow-unidentified"


def main() -> int:
    argv = [arg for arg in sys.argv[1:] if arg != ALLOW_UNIDENTIFIED]
    allow_unidentified = ALLOW_UNIDENTIFIED in sys.argv[1:]
    if len(argv) < 2:
        print(__doc__)
        return 2
    baseline, new = pathlib.Path(argv[0]), pathlib.Path(argv[1])
    absent = _unreadable({"baseline": baseline, "new": new})
    if absent:
        print("NOT MEASURED: nothing could be read.")
        for problem in absent:
            print(f"    {problem}")
        return 2

    # Before any suite is read: a delta between two trees nothing identifies is
    # not a weaker claim than a real one, it is a different claim about nothing.
    hard, waivable, identities = refusals(baseline, new, _REPO)
    if not report_refusals(hard, waivable, identities, allow_unidentified):
        return 2
    unattributed = bool(waivable)

    sides = {
        "baseline": (declared_suites(baseline), present_suites(baseline)),
        "new": (declared_suites(new), present_suites(new)),
    }
    suites = _resolve_suites(argv[2:], sides)
    _print_header(baseline, new, sides, suites)

    compared: list[SuiteComparison] = []
    unmeasured: list[tuple[str, str]] = []
    uncomparable: list[tuple[str, str]] = []
    for suite in suites:
        verdict = _why_unmeasured(
            in_old=suite in sides["baseline"][1],
            in_new=suite in sides["new"][1],
            declared_old=suite in (sides["baseline"][0] or set()),
            declared_new=suite in (sides["new"][0] or set()),
        )
        if verdict is None:
            compared.append(compare(baseline, new, suite))
        elif verdict[1] or argv[2:]:
            # A suite named on the command line was asked for by a human. Not
            # being able to compare it is a refusal, whatever the shape.
            unmeasured.append((suite, verdict[0]))
        else:
            uncomparable.append((suite, verdict[0]))

    return _report(compared, unmeasured, uncomparable, len(suites), unattributed)


def _report(
    compared: list[SuiteComparison],
    unmeasured: list[tuple[str, str]],
    uncomparable: list[tuple[str, str]],
    total: int,
    unattributed: bool = False,
) -> int:
    """Print the scope, then a verdict that cannot be read apart from it."""
    shared = sum(c.shared for c in compared)
    print(f"\nSCOPE: {len(compared)} of {total} suites compared, {shared} shared nodeids graded.")
    print(
        f"       baseline held {sum(c.baseline for c in compared)} nodeids in those suites, "
        f"new held {sum(c.new for c in compared)}."
    )

    if unmeasured:
        print(f"\nNOT MEASURED: {len(unmeasured)} of {total} suites — each is a hole in any verdict below.")
        for suite, reason in unmeasured:
            print(f"    {suite}: {reason}")

    if uncomparable:
        print(f"\nNOT COMPARABLE: {len(uncomparable)} of {total} suites — outside the claim, not a hole in it.")
        for suite, reason in uncomparable:
            print(f"    {suite}: {reason}")

    # CLEAN has to mean something was compared. The SCOPE line above is honest
    # about "0 of 0 suites, 0 shared nodeids" — but the verdict word and the
    # exit status are what a caller reads, and `if compare_runs; then ok` read
    # a pass. Nothing graded is the same defect one level down from a vanished
    # suite: not measured reading as fine.
    nothing_graded = not compared or shared == 0
    regressed = [c.suite for c in compared if c.regressed]
    # Appended to the verdict WORD, not printed near it. The word and the exit
    # status are the two things a caller reads, and an endpoint nobody can name
    # is a caveat on the verdict itself rather than a note beside it.
    tree = " (UNATTRIBUTED)" if unattributed else ""
    if regressed:
        print(f"\nREGRESSION{tree} over the {len(compared)} of {total} suites compared: {', '.join(regressed)}")
    elif not compared:
        print(
            f"\nNOT MEASURED: no suite could be compared at all — 0 of {total} accounted for. "
            "Check the run directories; this is an absent run, not a clean one."
        )
    elif nothing_graded:
        print(
            f"\nNOT MEASURED: {len(compared)} of {total} suites compared and not one PRE-EXISTING "
            "nodeid among them. The safety claim would be vacuous, so it is not made."
        )
    elif unmeasured:
        print(
            f"\nNO REGRESSION{tree} in the {len(compared)} of {total} suites compared — and this says NOTHING about the other {len(unmeasured)}."
        )
    else:
        print(
            f"\nCLEAN{tree} over {len(compared)} of {total} suites, {shared} shared nodeids: no pre-existing test changed outcome."
        )

    return 1 if (regressed or unmeasured or nothing_graded) else 0


if __name__ == "__main__":
    raise SystemExit(main())
