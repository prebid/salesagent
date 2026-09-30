#!/usr/bin/env python3
"""Per-CHECK index of the pinned conformance surface — the source of truth.

The storyboard roadmap (``storyboard_roadmap.py``) answers per STORYBOARD: is
anything covering it, is
anything tracking it. That is the wrong grain for the questions this PR needs
answered — which individual graded check is covered by which BDD scenario,
which is tracked by which ticket, and which is neither — because a storyboard
with one scenario and eleven graded checks looks "covered" in that table while
ten checks go ungraded by us.

So this emits ONE RECORD PER CHECK, as JSONL, and every markdown view is a
rendering of those records rather than a separate artifact. Adding a view means
adding a renderer, never a second source that can drift from the first. Each
record carries every column any view might want; the narrow tables in
``render()`` exist because one 12-column table is unreadable, not because the
data is separate.

Record identity is ``(storyboard, step_id, check_type, ordinal)``. The step is
the addressable unit: the conformance ledger keys on ``(protocol, track,
storyboard_id, step_id)``, so the step is what a ticket or a scenario can
actually be mapped onto, and the ordinal disambiguates repeated check types
within one step.

Joins, and what each is authoritative for:

* pinned compliance tree — which checks EXIST, and their gates. The standard.
* ``tests/storyboard/known_failures.txt`` — what a real in-network run MEASURED.
* ``storyboard_coverage_map`` — which ``@storyboard-v3.1`` scenario claims the
  storyboard (scenario coverage is declared per storyboard, not per check, so
  it is carried down to every check of that storyboard and marked as such).
* ``docs/test-obligations/storyboard-issue-map.yaml`` — the curated ticket
  mapping, the one input no program can derive.

Read-only. ``--jsonl`` for the source of truth, ``--markdown`` for the report.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.audit import (  # noqa: E402
    ledger,
    scenario_liveness_join,
    storyboard_binding_sweep,
    storyboard_coverage_map,
    storyboard_spec,
)

# A check whose storyboard needs this tool can never be graded here: the tool is
# a production test-control backdoor we will not implement.
CONTROLLER = "comply_test_controller"


@dataclass(frozen=True)
class CheckRecord:
    """One graded check — one record of this generator's ``--jsonl`` output.

    This IS the source of truth the module docstring promises: every markdown
    table in :func:`render` is a rendering of these fields, never a second
    source that can drift from the first. Declaring the shape (instead of the
    ``dict[str, Any]`` this replaces) makes two things impossible that the
    dict form allowed: renaming a field without every reader failing at
    attribute access, and publishing a record missing one of its identity
    fields — every field below is required, there is no partially-populated
    ``CheckRecord``.

    Grouped exactly as :func:`build` assembles them: identity, provenance,
    gating, measured, coverage, liveness, wireability, tracking. Record
    identity is ``(storyboard, step_id, check_type, ordinal)`` — see the
    module docstring for why the step, not the storyboard, is the
    addressable unit.
    """

    # identity
    storyboard: str
    storyboard_id: str
    phase_id: str | None
    step_id: str
    check_type: str
    ordinal: int
    # provenance — every claim below is checkable against this
    citation: str
    tier: str
    # conformance scope. Every check the pinned spec defines for a storyboard we
    # can reach is indexed, INCLUDING one we do not grade — a gated check is a
    # row with `gate="GATED"`, never a row that was dropped. Deleting them made
    # the headline count a floor that read like a total, and made a single
    # misclassification silently shrink the denominator by 43.
    #
    # GATED means "this storyboard declares `requires_capability` and the
    # offline classifier cannot evaluate it" — `declared_capabilities()` exposes
    # specialisms and protocols only, so a `media_buy.features.*` path is not
    # expressible. It does NOT assert we lack the capability. The live @adcp/sdk
    # runner reads the real capability document off the wire and may well grade
    # what we gate; when the two disagree the runner is right and the ledger
    # will show it. Reported, not hidden — that is the point of the column.
    gate: str
    gate_reason: str
    # gating: why a check may be unreachable rather than untested
    required_tools: list[str]
    requires_controller: bool
    # measured, from a real in-network run
    measured_failing_protocols: list[str]
    measured: str
    # our coverage — declared per storyboard, carried to each check
    scenarios: list[str]
    scenario_grain: str
    scenario_binding_buckets: dict[str, str]
    # liveness — joined from a real BDD run (tests/bdd/scenario_liveness.py) plus
    # the declarative ENV_ROUTES registry lookup (scripts/audit/scenario_liveness_join.py).
    # claimed_by_scenario is bool(scenarios) made explicit for renderers; a check is
    # graded_by_live_scenario when at least one claiming scenario has both its
    # steps bound AND its harness registry-verified wired. graduation_candidate is
    # true when a claiming scenario is locally ledgered (a curated xfail for a known
    # gap) while this check's own conformance-ledger measurement is `not failing` — a
    # mismatch worth taking through the xpass-graduation workflow. It requires
    # `not failing` and not merely "not FAILING": the difference is whether a run
    # reached the storyboard at all, and a check nobody has been shown to grade is not
    # a graduation candidate, it is an unmeasured one.
    claimed_by_scenario: bool
    scenario_liveness: dict[str, dict[str, Any]]
    graded_by_live_scenario: bool
    graduation_candidate: bool
    # can a scenario for this check be wired end-to-end?
    e2e_wireable: str
    e2e_axes: dict[str, Any]
    e2e_requires: list[str]
    e2e_blocker: str
    e2e_source: str
    # tracking — the one curated input
    issues: list[int]
    issue_coverage: str
    issue_note: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the JSON report.

        Every field, mechanically — see the sibling modules.
        """
        return dataclasses.asdict(self)


def _ledger_steps(repo: Path) -> dict[tuple[str, str], list[str]]:
    """(storyboard_id, step_id) -> protocols on which the ledger records a failure."""
    failures: dict[tuple[str, str], list[str]] = {}
    for check_id in ledger.load(ledger.ledger_path(repo)):
        failures.setdefault((check_id.storyboard_key, check_id.step_id), []).append(check_id.protocol)
    return failures


def _exercised_storyboards(repo: Path) -> set[str]:
    """Storyboards a real in-network run demonstrably REACHED.

    ``tests/storyboard/known_failures.txt`` records FAILURES only — its own header
    says so — so the absence of a row is not evidence of a pass. What the ledger DOES
    establish is that a run got as far as any storyboard it holds a row for.

    KNOWN CEILING, deliberate: this is storyboard grain, not check grain, and it
    infers "the run reached this storyboard's steps" from "the run failed one of
    them". A runner that aborts a storyboard part-way (``prerequisite_failed`` is a
    native skip, never ledgered) leaves later steps unreached while this reads them as
    exercised. The upgrade is an artifact of what the run COLLECTED — the conformance
    session computes exactly that set in-process for its stale-entry check
    (``test_storyboard_conformance.pytest_generate_tests``) and does not persist it.
    Publishing it would make this per-check rather than per-storyboard.

    IT IS PUBLISHED NOW, and this reads it when it is there. The ledger inference is
    kept as the FALLBACK rather than deleted, because an absent artifact must not read
    as "the run exercised nothing" -- turning a missing measurement into a confident
    zero is the same defect one level up, and it is the one that made this function
    worth a ticket. So: measurement when we have it, the old bounded over-count when we
    do not, never a silent zero.
    """
    artifact = _collected_artifact_path(repo)
    if artifact.exists():
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        collected = {c["storyboard_id"] for c in payload.get("checks", [])}
        if collected:
            return collected
    return _ledger_storyboards(repo)


def _collected_artifact_path(repo: Path) -> Path:
    """Where the conformance session publishes what it collected.

    Indirected through a function so the guard suite can point it somewhere else; the
    path itself has ONE owner, ``storyboard_spec.COLLECTED_ARTIFACT_PATH``, shared with
    the emitter in ``tests/storyboard/collected.py``.
    """
    return repo / "test-results" / storyboard_spec.COLLECTED_ARTIFACT_PATH


def _ledger_storyboards(repo: Path) -> set[str]:
    """The pre-artifact inference: storyboards the FAILURE ledger holds a row for."""
    return {check_id.storyboard_key for check_id in ledger.load(ledger.ledger_path(repo))}


def binding_buckets(repo: Path, adcp: Path) -> dict[str, str]:
    """Scenario id -> binding-sweep bucket (``storyboard_binding_sweep.BUCKET_LEGEND``).

    A scenario in bucket B cites a storyboard it does not actually claim, so
    "covered by that scenario" is a weaker statement than it looks. Carrying the
    bucket alongside the scenario is what stops this index from repeating the
    roadmap's overclaim in finer print.

    Reads ``storyboard_binding_sweep.audit()``'s own structured findings —
    never the RENDERED markdown table that sweep also produces for its
    checked-in report. That report is a view of this same data; regexing it
    back out risked silently reading zero if the emitter ever changed its
    bolding, and there was no way for a caller here to tell "the sweep found
    nothing" apart from "the render changed shape". Public (not
    underscore-private) so the join can be exercised directly in a test,
    offline, by monkeypatching ``storyboard_binding_sweep.audit`` — the
    CI-runnable proof that no markdown formatting is load-bearing here
    anymore.
    """
    result = storyboard_binding_sweep.audit(repo, adcp)
    return {b["identifier"]: b["bucket"] for b in result["bindings"]}


def _wire_fields(entry: dict[str, Any] | None, requires_controller: bool) -> dict[str, Any]:
    """Wireability columns for one check.

    A controller-gated step needs no assessment and gets none: it is ungradable
    by policy, so the verdict is deterministic and stating it as an assessed
    judgement would misrepresent where it came from.
    """
    if requires_controller:
        return {
            "e2e_wireable": "not_wireable",
            "e2e_axes": {},
            "e2e_requires": [],
            "e2e_blocker": "requires comply_test_controller, which will not be implemented",
            "e2e_source": "policy",
        }
    if entry is None:
        return {"e2e_wireable": "unassessed", "e2e_axes": {}, "e2e_requires": [], "e2e_blocker": "", "e2e_source": ""}
    return {
        "e2e_wireable": entry["verdict"],
        "e2e_axes": entry.get("axes") or {},
        "e2e_requires": entry.get("requires") or [],
        "e2e_blocker": entry.get("blocker", ""),
        "e2e_source": "assessed",
    }


# Statuses that earn a row in the index. OFF-PATH (a protocol we do not declare
# at all) stays out — that is a different agent's surface, not a gap in ours.
INDEXED_STATUSES = ("ON-PATH", "GATED")

#: The ``measured`` column's vocabulary. ``NOT MEASURED`` used to be spelled "no ledger
#: entry", which is an accurate description of the DATA and a misleading verdict: the
#: ledger holds failures only, so an empty result covers both "the in-network run graded
#: this and it passed" and "no run ever reached this check". Measured at the 3.1.1 pin,
#: 427 checks read "no ledger entry" and 355 of them — 83% — belonged to storyboards the
#: ledger has no row for at all, i.e. nothing established that any run reached them.
MEASURED_FAILING = "FAILING"
MEASURED_NOT_FAILING = "not failing"
MEASURED_NOT_MEASURED = "NOT MEASURED"
MEASURED_UNGRADABLE = "ungradable"
MEASURED_GATED = "gated"


def measured_status(*, gated: bool, failing: bool, step_controller: bool, storyboard_exercised: bool) -> str:
    """The ``measured`` verdict for one check.

    Its own function because it is the verdict step, and a verdict step has to be
    gradable without the pinned tree — ``tests/unit/test_architecture_storyboard_measured_status.py``
    exercises every branch offline.

    The order is precedence. A ledgered failure outranks everything, including the
    controller gate: a failure is PROOF the run reached the assertions, so reporting
    ``ungradable`` there would discard a real measurement.
    """
    if gated:
        # Not graded, so neither "not failing" nor "NOT MEASURED" — those belong to a
        # check we DO grade.
        return MEASURED_GATED
    if failing:
        return MEASURED_FAILING
    if step_controller:
        return MEASURED_UNGRADABLE
    if storyboard_exercised:
        # No row for this step, and the ledger proves a run reached this storyboard —
        # see _exercised_storyboards for the grain's ceiling.
        return MEASURED_NOT_FAILING
    return MEASURED_NOT_MEASURED


def _tool_step_keys(adcp: Path) -> set[tuple[str, str]]:
    """Ledger keys for pinned steps that invoke a tool and grade no ``check:``.

    Walks EVERY storyboard in the pinned tree, not only the ones ``build``
    indexes: the question this answers is whether the pin declares the step at
    all, which does not depend on the storyboard's coverage status.

    A storyboard shipped in both ``domains/`` and ``protocols/`` yields the same
    keys twice; the set absorbs it.
    """
    return {
        (ledger.join_id(storyboard_spec.storyboard_id(sb.text), sb.stem), step_id)
        for sb in storyboard_spec.storyboards(adcp)
        for step_id, _task in storyboard_spec.tool_steps_without_checks(sb.text)
    }


def build(repo: Path, adcp: Path) -> dict[str, Any]:
    coverage = storyboard_coverage_map.build(repo, adcp)
    dist = storyboard_spec.dist_root(adcp, coverage["pinned_version"])
    issue_map = ledger.load_issue_map(repo)
    ledger_failures = _ledger_steps(repo)
    exercised = _exercised_storyboards(repo)
    wireability = ledger.load_wireability(repo)
    buckets = binding_buckets(repo, adcp)
    claiming_scenarios = {
        s for row in coverage["storyboards"] if row["status"] in INDEXED_STATUSES for s in row["covered_by"]
    }
    liveness = scenario_liveness_join.build_index(claiming_scenarios)
    # Whether a real `pytest tests/bdd` run was joined at all. Without the artifact every
    # scenario reports measured_this_run=False and `with_live_scenario` is 0 — a zero
    # indistinguishable from "a run happened and nothing is live" unless this travels
    # beside it. Derived here rather than read off `coverage["totals"]` so this module
    # depends on the join it performs, not on a sibling report's shape.
    liveness_measured = any(f.measured_this_run for f in liveness.values())

    records: list[CheckRecord] = []
    for row in coverage["storyboards"]:
        if row["status"] not in INDEXED_STATUSES:
            continue
        gate = row["status"]
        gate_reason = row["reason"] if gate == "GATED" else ""
        text = (dist / row["storyboard"]).read_text(encoding="utf-8")
        storyboard_id = ledger.join_id(storyboard_spec.storyboard_id(text), row["stem"])
        tools = sorted(storyboard_spec.required_tools(text))
        tracking = issue_map.get(row["storyboard"]) or {}
        issues = tracking.get("issues") or []

        seen: dict[tuple[str, str], int] = {}
        # Two ways the pinned tree grades a step, same (owner, type, phase) shape.
        # checks_by_owner() walks literal `check:` lines; graded_steps_by_task()
        # covers steps graded through an assertion TASK (`expect_webhook` and
        # friends) that declare no `check:` of their own — invisible here before,
        # which is why 7 ledger entries on webhook-emission resolved to no record.
        # The two never overlap: a step with `check:` lines is skipped by the second.
        owned = storyboard_spec.checks_by_owner(text) + storyboard_spec.graded_steps_by_task(text)
        for step_id, check_type, phase_id in owned:
            ordinal = seen[(step_id, check_type)] = seen.get((step_id, check_type), -1) + 1
            failing = ledger_failures.get((storyboard_id, step_id), [])
            # The controller gate is per-STEP as well as per-storyboard: several
            # pinned files seed through it in one step and grade ordinary client
            # traffic in the rest, so judging by the top-level block alone marks
            # gradable checks ungradable.
            step_controller = CONTROLLER in tools or CONTROLLER in storyboard_spec.step_tools(text, step_id)
            wire = _wire_fields(wireability.get(f"{row['storyboard']}::{step_id}"), step_controller)
            measured = measured_status(
                gated=gate == "GATED",
                failing=bool(failing),
                step_controller=step_controller,
                storyboard_exercised=storyboard_id in exercised,
            )
            claiming = row["covered_by"]
            live_facts = {s: liveness[s] for s in claiming}
            # A ledgered claiming scenario (a curated xfail for a known gap) whose
            # check the real conformance run reached and did NOT measure FAILING is a
            # graduation candidate — worth taking through the xpass-graduation
            # workflow. Two exclusions, for opposite reasons: `ungradable`
            # (comply_test_controller-gated) can never graduate regardless of BDD
            # status, and NOT MEASURED has no run behind it — offering a check nobody
            # has been shown to run as ready to graduate is how a candidate list
            # becomes a to-do list nobody can act on.
            graduation_candidate = measured == MEASURED_NOT_FAILING and any(f.ledgered for f in live_facts.values())
            records.append(
                CheckRecord(
                    # identity
                    storyboard=row["storyboard"],
                    storyboard_id=storyboard_id,
                    phase_id=phase_id,
                    step_id=step_id,
                    check_type=check_type,
                    ordinal=ordinal,
                    # provenance — every claim below is checkable against this
                    citation=f"repo=adcp ref={coverage['pinned_version']} path={row['storyboard']}",
                    tier=storyboard_spec.storyboard_tier(row["storyboard"]),
                    gate=gate,
                    gate_reason=gate_reason,
                    # gating: why a check may be unreachable rather than untested
                    required_tools=tools,
                    requires_controller=step_controller,
                    # measured, from a real in-network run
                    measured_failing_protocols=sorted(failing),
                    measured=measured,
                    # our coverage — declared per storyboard, carried to each check
                    scenarios=claiming,
                    scenario_grain="storyboard",
                    scenario_binding_buckets={s: buckets.get(s, "-") for s in claiming},
                    # liveness — joined from a real BDD run + the ENV_ROUTES registry
                    claimed_by_scenario=bool(claiming),
                    scenario_liveness={s: f.to_dict() for s, f in live_facts.items()},
                    graded_by_live_scenario=any(f.graded_by_live_scenario for f in live_facts.values()),
                    graduation_candidate=graduation_candidate,
                    # can a scenario for this check be wired end-to-end?
                    e2e_wireable=wire["e2e_wireable"],
                    e2e_axes=wire["e2e_axes"],
                    e2e_requires=wire["e2e_requires"],
                    e2e_blocker=wire["e2e_blocker"],
                    e2e_source=wire["e2e_source"],
                    # tracking — the one curated input
                    issues=issues,
                    issue_coverage=tracking.get("coverage", "untriaged"),
                    issue_note=(tracking.get("note") or "").replace("\n", " ").strip(),
                )
            )

    # A conditional verdict that names nothing is not an answer: it says "there
    # is a hurdle" without saying what, so nobody can act on it and nobody can
    # check it. Fail loudly rather than publish it — the same discipline the
    # roadmap applies to untriaged storyboards.
    unexplained = sorted(
        {
            f"{r.storyboard}::{r.step_id}"
            for r in records
            if r.e2e_wireable == "conditional" and not r.e2e_requires and not r.e2e_blocker
        }
    )
    if unexplained:
        raise storyboard_spec.StoryboardAuditError(
            f"{len(unexplained)} step(s) are marked `conditional` in {ledger.WIREABILITY} with neither a "
            "`requires` entry nor a `blocker`. A conditional verdict must name what has to be "
            "provisioned, or be a plain `wireable`:\n" + "\n".join(f"  {s}" for s in unexplained)
        )

    # A silent {} fallback here would repeat the exact bug this refactor kills:
    # a bucket join that renders every row as if it were verified. audit() and
    # coverage_map share a key space by construction (both walk the same
    # @storyboard-v3.1-tagged scenarios), so a scenario named in covered_by
    # that resolves no bucket is a genuine join break, never a legitimate
    # "nothing to report" case.
    unresolved_scenarios = sorted({s for r in records for s in r.scenarios if s not in buckets})
    if unresolved_scenarios:
        raise storyboard_spec.StoryboardAuditError(
            f"{len(unresolved_scenarios)} scenario(s) are claimed by an on-path row's `covered_by` "
            "but storyboard_binding_sweep.audit() resolved no binding bucket for them — the join is "
            "broken, not the data. Fix binding_buckets() before trusting this output:\n"
            + "\n".join(f"  {s}" for s in unresolved_scenarios)
        )

    # The ledger-to-index join. Until this PR it lived in a guard that compared
    # the COMMITTED storyboard-checks.jsonl against this ledger; with the
    # artifact no longer committed there is nothing to go stale, and re-asserting
    # that equality here would be a tautology — `measured_failing_protocols` is
    # built from `_ledger_steps()` a few lines above, so it would grade this
    # function against its own input.
    #
    # The invariant that survives is the other direction: every row of the
    # curated ledger must name a step the PINNED TREE can produce. An orphan row
    # means the ledger grades something the pin no longer has.
    #
    # THE GRAIN RULE (moved here from docs/test-obligations/storyboard-check-index.md,
    # deleted in this PR, and CORRECTED on the way). The ledger keys on
    # (storyboard_id, step_id) and takes step_id VERBATIM from the real
    # @adcp/sdk runner. Two families of step the runner produces at run time have
    # no `check:` line and therefore no record here:
    #
    #   * signed_requests vector steps, one per conformance fixture — the old
    #     prose said "negative-NNN", which was WRONG: 12 of the 39 are
    #     positive-NNN, so a reader implementing it verbatim still had 12 orphans.
    #     Derived by ledger.vector_step_keys() from the pinned bundle, never
    #     listed by name.
    #   * the runner-level synthetic (ledger.RUNNER_SYNTHETIC_KEY), emitted when
    #     the runner grades nothing at all.
    #   * a step invoking a real TOOL with no `check:` line. The runner runs it
    #     and reports pass or fail on the INVOCATION — the tool answered or it
    #     did not — while the index, keyed on `check:` lines, produces no row.
    #     `media_buy_seller/creative_reception::list_formats` is the measured
    #     case: declared by the 3.1.1 pin under section
    #     `discover_accepted_formats`, carrying zero checks, and genuinely
    #     FAILING on both protocols. Derived by
    #     storyboard_spec.tool_steps_without_checks() from the pinned tree,
    #     never listed by name, so a storyboard reshaped upstream moves it.
    #
    # So the join universe is the index keys plus those three families. Measured
    # when this landed: index-only leaves 40 orphans, this universe leaves 0.
    known_step_keys = (
        {(r.storyboard_id, r.step_id) for r in records}
        | ledger.vector_step_keys(adcp)
        | {ledger.RUNNER_SYNTHETIC_KEY}
        | _tool_step_keys(adcp)
    )
    orphan_ledger_rows = sorted(
        f"{storyboard_id}::{step_id}"
        for storyboard_id, step_id in {(c.storyboard_key, c.step_id) for c in ledger.load(ledger.ledger_path(repo))}
        if (storyboard_id, step_id) not in known_step_keys
    )
    if orphan_ledger_rows:
        raise storyboard_spec.StoryboardAuditError(
            f"{len(orphan_ledger_rows)} ledger row(s) in {ledger.LEDGER} name a step the pinned tree "
            "does not produce — neither a graded check, nor a request-signing conformance fixture, nor "
            "the runner synthetic. The ledger grades something the pin no longer has; re-seed it from a "
            "measured run, or drop the rows:\n" + "\n".join(f"  {row}" for row in orphan_ledger_rows)
        )

    # Every derived metric below is scoped to the GRADED surface (gate="ON-PATH").
    # A gated check has no ledger entry by construction and its BDD/wireability
    # status grades nothing, so folding it into "claimed by a scenario" or
    # "measured FAILING" would inflate a denominator with rows that cannot move.
    # `checks` (total in scope) and `graded_checks` are therefore two numbers,
    # not one — the earlier single number was the graded set wearing the total's
    # label, which is exactly how 43 checks went missing without a trace.
    graded = [r for r in records if r.gate == "ON-PATH"]
    gaps = [r for r in graded if not r.scenarios and not r.issues]
    return {
        "pinned_version": coverage["pinned_version"],
        "liveness_measured": liveness_measured,
        "totals": {
            "checks": len(records),
            "storyboards": len({r.storyboard for r in records}),
            "graded_checks": len(graded),
            "graded_storyboards": len({r.storyboard for r in graded}),
            "gated_checks": len(records) - len(graded),
            "gated_storyboards": len({r.storyboard for r in records if r.gate == "GATED"}),
            "with_scenario": sum(1 for r in graded if r.scenarios),
            "with_live_scenario": sum(1 for r in graded if r.graded_by_live_scenario),
            "graduation_candidates": sum(1 for r in graded if r.graduation_candidate),
            "with_issue": sum(1 for r in graded if r.issues),
            "neither": len(gaps),
            # ALL FOUR READ `r.measured`, which is `measured_status()`'s verdict and the
            # one place the precedence lives. Two of them used to read the raw INPUTS
            # instead — `measured_failing_protocols` and `requires_controller` — and a
            # check that is both a ledgered failure AND controller-gated was therefore
            # counted twice. The precedence says such a check is FAILING (a failure
            # proves the run reached the assertions, so `ungradable` would discard a
            # real measurement), so the double count also contradicted the documented
            # order. Latent until the ledger first admitted a media_buy_seller failure
            # on a controller-gated step, at which point the partition assertion in
            # tests/unit/test_architecture_storyboard_measured_status.py caught it at 7
            # over. Reading the verdict makes the partition true by construction rather
            # than by three predicates happening to stay disjoint.
            "failing": sum(1 for r in graded if r.measured == MEASURED_FAILING),
            "not_failing": sum(1 for r in graded if r.measured == MEASURED_NOT_FAILING),
            "not_measured": sum(1 for r in graded if r.measured == MEASURED_NOT_MEASURED),
            "ungradable": sum(1 for r in graded if r.measured == MEASURED_UNGRADABLE),
            "wireable": sum(1 for r in graded if r.e2e_wireable == "wireable"),
            "conditional": sum(1 for r in graded if r.e2e_wireable == "conditional"),
            "not_wireable": sum(1 for r in graded if r.e2e_wireable == "not_wireable"),
            "unassessed": sum(1 for r in graded if r.e2e_wireable == "unassessed"),
        },
        # to_dict() at the JSONL boundary, mirroring Binding.to_dict() — every
        # consumer of build()'s return value (render(), jsonl(), the plain
        # `json.dumps(result)` CLI path) works with dicts; only this function
        # and its internal post-processing above ever touch a CheckRecord
        # attribute directly.
        "records": [r.to_dict() for r in records],
    }


def jsonl(result: dict[str, Any]) -> list[dict[str, Any]]:
    """The source of truth: one line per check."""
    return result["records"]


def _check_id(record: dict[str, Any]) -> str:
    suffix = f"#{record['ordinal']}" if record["ordinal"] else ""
    return f"`{record['storyboard_id']}/{record['step_id']}/{record['check_type']}{suffix}`"


def render(result: dict[str, Any]) -> str:
    """Several narrow tables, each answering ONE question about the same rows."""
    totals = result["totals"]
    records = result["records"]
    out = [
        f"# Storyboard check index — AdCP {result['pinned_version']}",
        "",
        "**One row per graded check**, not per storyboard. Every table below is a view of "
        "the same in-memory records, so they cannot disagree. This report is NOT committed: "
        "it is regenerated on every CI run from the pinned bundle and published to the job "
        "summary. Reproduce it with `scripts/audit/storyboard_check_index.py --markdown` "
        "(add `--jsonl` for the per-record stream).",
        "",
        f"- checks the pinned spec defines for storyboards on our protocol: **{totals['checks']}** "
        f"across **{totals['storyboards']}** storyboards",
        f"- of those, GRADED (`gate=ON-PATH`): **{totals['graded_checks']}** across "
        f"**{totals['graded_storyboards']}** storyboards — every metric below is over this set",
        f"- GATED, not graded (`gate=GATED`): **{totals['gated_checks']}** across "
        f"**{totals['gated_storyboards']}** storyboards. GATED means the storyboard declares "
        "`requires_capability` and the OFFLINE classifier cannot evaluate it — "
        "`declared_capabilities()` exposes specialisms and protocols only, so a "
        "`media_buy.features.*` path is not expressible. It is not a claim that we lack the "
        "capability: the live runner reads the real capability document off the wire and may "
        "grade what we gate. These rows are listed, with their reason, in §7.",
        f"- claimed by a BDD scenario: **{totals['with_scenario']} of {totals['graded_checks']}**",
        (
            f"- graded by a LIVE scenario (steps bound + registry-verified harness): "
            f"**{totals['with_live_scenario']} of {totals['with_scenario']} claimed**"
            if result["liveness_measured"]
            else (
                "- graded by a LIVE scenario: **NOT MEASURED** — no "
                "`test-results/bdd_scenario_liveness.json` was joined, so every claim below reads "
                "dormant by omission rather than by observation. Run `pytest tests/bdd` and "
                "regenerate before quoting a liveness number."
            )
        ),
        f"- tracked by an issue: **{totals['with_issue']}**",
        f"- **neither scenario nor ticket: {totals['neither']}**",
        "",
        f"Measured status over the **{totals['graded_checks']}** graded checks, from the in-network "
        f"conformance ledger: **{totals['failing']}** FAILING · **{totals['not_failing']}** not failing "
        f"(a run reached their storyboard) · **{totals['ungradable']}** permanently ungradable "
        f"(`comply_test_controller`) · **{totals['not_measured']} NOT MEASURED**. That last number is "
        "the one the old report hid: `tests/storyboard/known_failures.txt` records FAILURES only, so "
        'an empty result covers both "the run graded this and it passed" and "no run ever reached '
        'it". A check is `not failing` only when the ledger holds a row for some OTHER step of the '
        "same storyboard, which is what proves a run got there; with no row anywhere for the "
        "storyboard, nothing has been established and the check is NOT MEASURED.",
        "",
        f"- graduation candidates (ledgered locally, reached by a run, not measured FAILING): "
        f"**{totals['graduation_candidates']} of {totals['not_failing']}**",
        "",
        f"E2E wireability — **{totals['wireable']}** wireable as-is, **{totals['conditional']}** "
        f"conditional on provisioning, **{totals['not_wireable']}** not wireable"
        + (f", **{totals['unassessed']}** unassessed" if totals["unassessed"] else "")
        + ".",
        "",
        "**Two grains, both indexed.** The conformance ledger keys on "
        "`(storyboard_id, step_id)` and takes `step_id` VERBATIM from the real `@adcp/sdk` "
        "runner, which this repo does not control. The pinned tree grades a step two ways: by a "
        "literal `check:` line (owned by the innermost enclosing step) and by an assertion TASK "
        "— `expect_webhook` and friends — whose step declares no `check:` of its own and whose "
        "failure the runner attributes to the step named in its `triggered_by`. Both now produce "
        "rows here (`storyboard_spec.checks_by_owner` and `graded_steps_by_task`), so the "
        "`measured` join resolves: every ledger entry lands on a record except "
        "`signed_requests`' runtime-generated `negative-NNN` steps (built from vector fixtures, "
        "as the pinned file states) and the `agent_reachability` runner-level synthetic, neither "
        "of which is a spec check. Before this, seven `universal/webhook-emission.yaml` entries "
        "resolved to nothing, so a check with no ledger row was not evidence it passed. Fixing "
        "the join made the row-to-record mapping trustworthy; the column now also distinguishes "
        "a check the run reached and did not fail from one no run has been shown to reach.",
        "",
        "Scenario coverage is declared per STORYBOARD (`@storyboard-v3.1` tags a scenario "
        "to a storyboard, not to a check), so a scenario shown against a check means "
        '"this check\'s storyboard is claimed" — not that this check is asserted. That '
        "distinction is the whole reason for indexing at this grain, and the whole reason "
        "**claimed by a BDD scenario** and **graded by a LIVE scenario** are reported as two "
        "separate numbers rather than one: claimed only asks whether a scenario's tag names "
        "this storyboard; graded additionally requires, from a real `pytest tests/bdd` run, "
        "that every one of that scenario's steps has a bound step definition AND that its "
        "harness routing resolves to a non-placeholder row in the declarative `ENV_ROUTES` "
        "registry — a data lookup, never reason-text matching. A claim with no live scenario "
        "behind it is a dormant claim, not coverage.",
        "",
        "## 1. Measured status",
        "",
        "Every check except the `not failing` ones — those are the rows a run reached and "
        "did not fail, and listing them would bury the four statuses a reader acts on.",
        "",
        "| Check | Status | Protocols failing |",
        "|---|---|---|",
    ]
    for r in records:
        if r["measured"] == MEASURED_NOT_FAILING:
            continue
        protocols = ", ".join(f"`{p}`" for p in r["measured_failing_protocols"]) or "—"
        out.append(f"| {_check_id(r)} | {r['measured']} | {protocols} |")

    unmeasured: dict[str, int] = {}
    for r in records:
        if r["measured"] == MEASURED_NOT_MEASURED:
            unmeasured[r["storyboard"]] = unmeasured.get(r["storyboard"], 0) + 1
    out += [
        "",
        "### 1b. Storyboards no run has been shown to reach",
        "",
        f"**{sum(unmeasured.values())} checks across {len(unmeasured)} storyboards.** The ledger "
        "holds no row for any step of these, so nothing establishes that the in-network "
        "conformance job ever graded them. They are NOT passing checks; they are unmeasured "
        "ones, and any conformance number that counts them as clean is flattering by exactly "
        "this much.",
        "",
        "| Storyboard | Unmeasured checks |",
        "|---|---|",
    ]
    out += [f"| `{sb}` | {n} |" for sb, n in sorted(unmeasured.items())]

    out += [
        "",
        "## 2. Tracking",
        "",
        "Checks whose storyboard carries an issue. `coverage` is the map's own assessment "
        "of how much of the storyboard that issue covers.",
        "",
        "| Check | Issue(s) | Coverage |",
        "|---|---|---|",
    ]
    for r in records:
        if not r["issues"]:
            continue
        refs = ", ".join(f"#{n}" for n in r["issues"])
        out.append(f"| {_check_id(r)} | {refs} | {r['issue_coverage']} |")

    out += [
        "",
        "## 3. Scenario coverage",
        "",
        "`live?` is this row's `graded_by_live_scenario` — at least one claiming scenario with "
        "steps bound and a registry-verified wired harness. A claim with no live scenario "
        'renders "claimed only", not silently as covered.',
        "",
        "| Check | Scenario(s) claiming the storyboard | live? |",
        "|---|---|---|",
    ]
    for r in records:
        if not r["scenarios"]:
            continue
        live = "yes" if r["graded_by_live_scenario"] else "claimed only"
        out.append(f"| {_check_id(r)} | {', '.join(f'`{s}`' for s in r['scenarios'])} | {live} |")

    out += [
        "",
        "## 4. Graduation candidates",
        "",
        "A claiming scenario locally xfails this check's storyboard as a known gap (the "
        "`ledgered` bucket, from a real BDD run — see `tests/bdd/scenario_liveness.py`), while "
        "the in-network conformance run REACHED this check's storyboard and did not measure "
        "the check FAILING. That mismatch is a candidate for the xpass-graduation workflow — "
        "inspect per scenario before removing the xfail, per scenario, never in bulk. "
        "Visibility only: no CI gate reads this table.",
        "",
        "Checks whose status is NOT MEASURED are deliberately absent: with no ledger row "
        "anywhere for their storyboard there is no run to graduate against, and offering "
        "them here would put checks nobody has been shown to grade on a list a human is "
        "meant to act on.",
        "",
        "| Check | Ledgered scenario(s) |",
        "|---|---|",
    ]
    for r in records:
        if not r["graduation_candidate"]:
            continue
        ledgered = ", ".join(f"`{s}`" for s, facts in r["scenario_liveness"].items() if facts["ledgered"])
        out.append(f"| {_check_id(r)} | {ledgered} |")

    out += [
        "",
        "## 5. End-to-end wireability",
        "",
        "Can a BDD scenario for this check be wired in the e2e environment — Given seeded by "
        "sending requests or by ordinary stack fixtures, When constructed and sent by a client, "
        "Then asserted on the wire? Curated in `storyboard-wireability.yaml`; the harness is a "
        "client, so building a signed request, sending a malformed one or firing N requests to "
        "trip a rate limit are all wireable. Controller-gated checks are `not_wireable` by "
        "policy rather than by assessment.",
        "",
        "| Check | Wireable | Needs provisioning | Blocker |",
        "|---|---|---|---|",
    ]
    for r in records:
        if r["e2e_wireable"] == "wireable":
            continue
        # `unassessed` is a gap, not a non-finding: a check with no curated verdict
        # in storyboard-wireability.yaml (and not named in that file's `untriaged:`
        # list) means nobody has judged whether it can be wired at all. Dropping the
        # row here used to make an untriaged step invisible instead of visible as a
        # gap — the exact silent-omission bug test_architecture_storyboard_
        # wireability.py guards against.
        needs = ", ".join(f"`{x}`" for x in r["e2e_requires"]) or "—"
        blocker = (r["e2e_blocker"] or "—").replace("\n", " ").strip()
        out.append(f"| {_check_id(r)} | {r['e2e_wireable']} | {needs} | {blocker[:180]} |")

    out += [
        "",
        "## 6. Neither scenario nor ticket",
        "",
        f"The list to take to triage: {result['pinned_version']} grades these, we do not test them, "
        "and nothing in the tracker names them.",
        "",
        "| Check | Storyboard | Required tools |",
        "|---|---|---|",
    ]
    for r in records:
        if r["scenarios"] or r["issues"]:
            continue
        tools = ", ".join(f"`{t}`" for t in r["required_tools"]) or "—"
        out.append(f"| {_check_id(r)} | `{r['storyboard']}` | {tools} |")

    return "\n".join(out) + "\n"


def main() -> int:
    return storyboard_spec.run_cli(__doc__ or "", build, render, jsonl_fn=jsonl)


if __name__ == "__main__":
    sys.exit(main())
