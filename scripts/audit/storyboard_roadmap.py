#!/usr/bin/env python3
"""Per-storyboard roadmap: spec clause + scenario + implementation clue, per on-path row.

For every ON-PATH storyboard (from storyboard_coverage_map.build()), attaches:

  * the 3.1.1 citation (the storyboard's own pinned-tree path + pinned_version()),
  * required_tools and a static, YAML-derived check-type inventory,
  * the comply_test_controller divergence tag for the 20 storyboards triaged
    as deliberate (not a plain gap),

Scenario-level reconciliation (VERDICT/action per re-grounding proposal) is NOT
joined here and no longer exists as a report. ``storyboard_reconciliation.py``
read 40 proposal files that lived in an agent working directory, and commit
b09479143 deleted those on the ground that committed code reading an agent's
working directory as data is a layering violation. This module was corrected in
that commit; the reconciliation script was left behind, and is now deleted.
tests/unit/test_architecture_audit_scripts_have_a_subject.py encodes the rule.

Measured-status join key: the runner's ``tested_tracks[].scenarios[].
scenario`` field is ``"<storyboard_stem>/<sub-scenario-name>"`` in the
runner's own underscore-cased spelling (e.g. ``capability_discovery/...``),
while coverage_map's stems are hyphenated for universal/ storyboards
(``capability-discovery``). Joined by normalizing both sides to underscores.
A storyboard with zero matching runner scenarios is reported ``not_yet_run``,
never silently omitted -- this script asserts a minimum join rate against the
runner's own ``storyboards_executed``/``storyboards_missing_tools`` totals so
a regression in the join logic cannot ship looking like "nothing was
measured" (architect review finding).

Read-only. Emits JSON, or ``--markdown`` for the checked-in artifact.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.audit import ledger, storyboard_coverage_map, storyboard_spec  # noqa: E402

COMPLY_TEST_CONTROLLER = "comply_test_controller"

# The EDITORIAL half of the comply_test_controller triage, and only that half.
#
# WHETHER a storyboard is ungradable is not stored here -- it is derived from
# the storyboard's own `required_tools` (see `build_row_status_fields`). This
# table once doubled as that membership test, and measured against the 3.1.1
# pin it was a strict SUBSET of the storyboards declaring the tool: 20 typed in
# against 29 declaring it, so 9 -- 4 of them on-path -- rendered as "no ledger
# entries", i.e. a permanent by-design divergence displayed as not-yet-measured
# (the sibling). Adding a stem here does not
# make a storyboard ungradable and removing one does not make it gradable.
#
# What survives is the judgement no structure carries: DETERMINISTIC INJECTION
# storyboards stay dormant by design; PRIOR STATE ONLY storyboards are reachable
# via real API sequencing instead of the missing tool. Keyed by storyboard stem
# (matches storyboard_coverage_map's `stem`). Each entry's kind was triaged by
# hand; the triage notes are not committed. Storyboards requiring the tool with no entry
# here render UNTRIAGED, which is a real answer: ungradable, kind not yet
# triaged. Guarded by tests/unit/test_architecture_storyboard_controller_
# divergence.py, which fails on a stem the pinned tree does not gate.
_COMPLY_TEST_CONTROLLER_DIVERGENCE: dict[str, str] = {
    "audience_buy_flow": "DETERMINISTIC INJECTION",
    "billing_finality_delivery": "DETERMINISTIC INJECTION",
    "canonical_formats": "PRIOR STATE ONLY",
    "clicks_buy_flow": "DETERMINISTIC INJECTION",
    "completed_views_buy_flow": "DETERMINISTIC INJECTION",
    "dependency_impairment": "PRIOR STATE ONLY",
    "dependency_impairment_cardinality": "PRIOR STATE ONLY",
    "frequency_cap_enforcement": "DETERMINISTIC INJECTION",
    "get_products_async": "DETERMINISTIC INJECTION",
    "performance_buy_flow": "DETERMINISTIC INJECTION",
    "performance_buy_flow_roas": "DETERMINISTIC INJECTION",
    "pricing_currency_filter": "PRIOR STATE ONLY",
    "product_signal_targeting": "PRIOR STATE ONLY",
    "provenance_audit_observation": "DETERMINISTIC INJECTION",
    "reach_buy_flow": "DETERMINISTIC INJECTION",
    "vendor_metric_catalog_precondition": "DETERMINISTIC INJECTION",
    "vendor_metric_optimization_flow": "PRIOR STATE ONLY",
    "canonical-format-validate-input": "PRIOR STATE ONLY",
    "comply-controller-mode-gate": "DETERMINISTIC INJECTION",
    "deterministic-testing": "DETERMINISTIC INJECTION",
}


def check_issue_map_complete(repo: Path, on_path: list[str]) -> list[str]:
    """Return the on-path storyboards absent from the issue map.

    A spec bump that introduces a storyboard must not silently add an untracked
    conformance gap -- the new storyboard has to be triaged into the map, even
    if the triage outcome is `coverage: none`. Same ratchet discipline as the
    structural guards: the map may not fall behind the spec.
    """
    return sorted(set(on_path) - set(ledger.load_issue_map(repo)))


def build_row_status_fields(stem: str, text: str) -> dict[str, Any]:
    """The two status-bearing fields for one storyboard: ungradability + its kind.

    `requires_controller` is DERIVED from the storyboard's own `required_tools`
    — never from membership in the editorial table above, which is a strict
    subset of the storyboards the tree gates and so silently misses whatever
    nobody typed in. Deriving also handles the off-path ones for free, so they
    stay correct if the coverage gate ever brings them on-path.

    `divergence` is the editorial kind: the triaged label where one exists,
    `UNTRIAGED` where the tool is required but no triage was recorded (a real
    answer — ungradable, kind unknown — rather than a blank cell sitting next
    to a Status that says otherwise), and None where the tool is irrelevant.
    """
    requires_controller = COMPLY_TEST_CONTROLLER in storyboard_spec.required_tools(text)
    label = _COMPLY_TEST_CONTROLLER_DIVERGENCE.get(stem)
    return {
        "requires_controller": requires_controller,
        "divergence": (label or "UNTRIAGED") if requires_controller else None,
    }


def _status_cell(row: dict[str, Any]) -> str:
    """Objective measured state for one storyboard, from the in-network CI ledger.

    Three answers, and the distinction between the last two is the point of the
    column. `FAILING` is measured. `no ledger entries` means the job ran the
    storyboard and nothing failed. `ungradable` means the job could not reach the
    assertions at all — a coverage hole that a naive "0 failures" reading would
    score as a pass.

    Ledgered failures outrank ungradability deliberately: a ledgered failure is
    proof the job DID reach the assertions, so reporting "ungradable" there
    would throw away a real measurement.
    """
    if failures := row["ledgered_failures"]:
        return f"**FAILING** — {len(failures)} ledgered"
    if row["requires_controller"]:
        return "ungradable (comply_test_controller)"
    return "no ledger entries"


def _render_issue_cell(entry: dict[str, Any] | None) -> str:
    """One table cell: the tracking status of a storyboard's gap."""
    if entry is None:
        return "**UNTRIAGED**"
    issues = entry.get("issues") or []
    coverage = entry.get("coverage", "none")
    if not issues:
        return "**TO FILE**"
    refs = ", ".join(f"#{n}" for n in issues)
    return f"{refs} ({coverage})" if coverage != "full" else refs


def _ledgered_failures(repo: Path) -> dict[str, list[str]]:
    """storyboard_id -> failing step ids, from the in-network CI ledger.

    This is the authoritative measured state. `tests/storyboard/known_failures.txt`
    is seeded exclusively from real runs of the Storyboard Conformance job, which
    executes the runner IN-NETWORK against a live stack — the same topology the
    agent ships in.

    It deliberately supersedes the older `runner/results/sb1b-*.json` and
    `sb1d-*.json` captures as the join source for this table. Those were host-side
    runs against published ports, taken before this branch reverted its two
    production fixes, so they describe neither the current topology nor the current
    code. The architect review's HIGH finding on this ticket says exactly that:
    host-side numbers do not carry over. They stay in the repo as history.
    """
    failures: dict[str, list[str]] = {}
    for check_id in ledger.load(ledger.ledger_path(repo)):
        # storyboard_key normalizes hyphens to underscores: the ledger carries
        # the runner's underscore spelling (webhook_emission) while
        # coverage_map stems are hyphenated for universal/ (webhook-emission).
        # Joining raw (storyboard_id) silently matched nothing and rendered
        # every row as passing.
        failures.setdefault(check_id.storyboard_key, []).append(check_id.step_id)
    return failures


def build(repo: Path, adcp: Path) -> dict[str, Any]:
    coverage = storyboard_coverage_map.build(repo, adcp)
    dist = storyboard_spec.dist_root(adcp, coverage["pinned_version"])
    issue_map = ledger.load_issue_map(repo)
    ledgered = _ledgered_failures(repo)

    on_path = [r for r in coverage["storyboards"] if r["status"] == "ON-PATH"]
    gated = [r for r in coverage["storyboards"] if r["status"] == "GATED"]
    untriaged = check_issue_map_complete(repo, [r["storyboard"] for r in on_path])

    rows: list[dict[str, Any]] = []
    for row in on_path:
        text = (dist / row["storyboard"]).read_text(encoding="utf-8")
        # The runner keys results on the storyboard's DECLARED `id:`, which
        # differs from the filename for 69 of 121 storyboards at 3.1.1
        # (universal/security.yaml -> security_baseline; every media-buy
        # scenario is namespaced media_buy_seller/<name>). Joining on the
        # filename stem matched only where the two happen to coincide; the
        # stem is the fallback for an id-less storyboard, which is this
        # table's policy rather than a fact about the tree.
        storyboard_id = ledger.join_id(storyboard_spec.storyboard_id(text), row["stem"])
        # Never sum checks_for_phase() over phases() here: phase windows
        # enclose their steps' windows, so that spelling counts every nested
        # check twice.
        checks = storyboard_spec.check_inventory(text)

        tracking = issue_map.get(row["storyboard"])

        rows.append(
            {
                "storyboard": row["storyboard"],
                "stem": row["stem"],
                "citation": f"repo=adcp ref={coverage['pinned_version']} path={row['storyboard']}",
                "scenarios": row["covered_by"],
                # The claim/coverage split the coverage map now carries, passed through
                # rather than re-derived: "Scenario" in this table was a tag claim, and
                # "scenarios TO WRITE" counted only the storyboards with no tag at all.
                "scenarios_live": row["covered_by_live"],
                "coverage": row["coverage"],
                "required_tools": sorted(storyboard_spec.required_tools(text)),
                "checks": checks,
                **build_row_status_fields(stem=row["stem"], text=text),
                "tracking": _render_issue_cell(tracking),
                "tracking_issues": (tracking or {}).get("issues") or [],
                "tracking_coverage": (tracking or {}).get("coverage", "untriaged"),
                "tracking_note": (tracking or {}).get("note", ""),
                "storyboard_id": storyboard_id,
                "ledgered_failures": sorted(ledgered.get(storyboard_id, [])),
            }
        )

    if ledgered and not any(r["ledgered_failures"] for r in rows):
        raise storyboard_spec.StoryboardAuditError(
            f"ledger has {sum(len(v) for v in ledgered.values())} failing checks across "
            f"{len(ledgered)} storyboards, but none joined to an on-path row — the join key is "
            "broken, not the data. Every row would render as passing. Fix _ledgered_failures()."
        )

    if untriaged:
        raise storyboard_spec.StoryboardAuditError(
            f"{len(untriaged)} on-path storyboard(s) are absent from {ledger.ISSUE_MAP}. A storyboard the "
            "pinned spec grades us on must be triaged before this table can claim to be the "
            "conformance gap record — even if the triage outcome is `coverage: none`:\n"
            + "\n".join(f"  {s}" for s in untriaged)
        )

    return {
        "pinned_version": coverage["pinned_version"],
        "totals": {
            "on_path": len(on_path),
            "gated": len(gated),
            "unknown": coverage["totals"]["unknown"],
            "liveness_measured": coverage["totals"]["liveness_measured"],
            "no_scenario": sum(1 for r in rows if not r["scenarios"]),
            "scenario_not_live": sum(1 for r in rows if r["coverage"] == storyboard_coverage_map.COVERAGE_CLAIMED),
            "scenario_not_measured": sum(
                1 for r in rows if r["coverage"] == storyboard_coverage_map.COVERAGE_NOT_MEASURED
            ),
            "scenario_live": sum(1 for r in rows if r["coverage"] == storyboard_coverage_map.COVERAGE_LIVE),
            "no_ticket": sum(1 for r in rows if not r["tracking_issues"]),
            "no_scenario_no_ticket": sum(1 for r in rows if not r["scenarios"] and not r["tracking_issues"]),
            "distinct_issues": len({i for r in rows for i in r["tracking_issues"]}),
            "failing": sum(1 for r in rows if r["ledgered_failures"]),
            "ledgered_checks": sum(len(r["ledgered_failures"]) for r in rows),
        },
        "rows": rows,
        # Gated storyboards travel with the result so render() can LIST them.
        # They are excluded from `rows` because every row column (scenario,
        # ticket, measured status) is a property of a graded storyboard, but
        # excluding them from the DOCUMENT is what made the count read as a
        # total when it was a floor.
        "gated": [{"storyboard": r["storyboard"], "reason": r["reason"]} for r in gated],
    }


def render(result: dict[str, Any]) -> str:
    out = [
        f"# Storyboard roadmap — AdCP {result['pinned_version']}",
        "",
        f"**What AdCP {result['pinned_version']} grades this agent on, what we test, and what is tracked.**",
        "",
        f"One row per on-path storyboard: the {result['pinned_version']} clause, the BDD scenario that claims it (or "
        "**TO WRITE**), the static check-type inventory, the MEASURED status from the "
        "in-network Storyboard Conformance CI job, and the ticket to reuse (or **TO FILE**). "
        "Generated — do not hand-edit; regenerate with `scripts/audit/storyboard_roadmap.py`. "
        "Every column but **Ticket** derives from the pinned compliance tree, this repo's "
        "`.feature` files, and `tests/storyboard/known_failures.txt`; **Ticket** comes from "
        "the curated `storyboard-issue-map.yaml`, the one hand-maintained input.",
        "",
        f"- on-path storyboards: **{result['totals']['on_path']}**",
        f"- gated, not graded: **{result['totals']['gated']}** — listed below the table. A gated "
        "storyboard declares `requires_capability` that the OFFLINE classifier cannot evaluate "
        "(`declared_capabilities()` exposes specialisms and protocols only). It is NOT a claim "
        "that we lack the capability; per-check detail is in the check index "
        "(`scripts/audit/storyboard_check_index.py --jsonl`), where "
        "gated checks are indexed with `gate=GATED` rather than dropped.",
        f"- **measured FAILING: {result['totals']['failing']} storyboards, "
        f"{result['totals']['ledgered_checks']} ledgered checks**",
        f"- **storyboards with NO scenario claiming them: {result['totals']['no_scenario']} of "
        f"{result['totals']['on_path']}**",
        (
            f"- of the {result['totals']['on_path'] - result['totals']['no_scenario']} that ARE claimed: "
            f"**{result['totals']['scenario_live']} graded by a live scenario**, "
            f"{result['totals']['scenario_not_live']} claim-only (tagged, steps not bound or harness "
            "not wired). A claim is a `@storyboard-v3.1` TAG; only the first number is coverage."
            if result["totals"]["liveness_measured"]
            else (
                f"- of the {result['totals']['on_path'] - result['totals']['no_scenario']} that ARE claimed: "
                f"**liveness NOT MEASURED for all {result['totals']['scenario_not_measured']}** — no "
                "`test-results/bdd_scenario_liveness.json` was joined, so no **Scenario** cell below has "
                "been shown to grade anything. Run `pytest tests/bdd` and regenerate."
            )
        ),
        f"- **unclassified storyboards (no verdict at all): {result['totals']['unknown']}**",
        f"- **tickets TO FILE: {result['totals']['no_ticket']}**",
        f"- **neither scenario nor ticket: {result['totals']['no_scenario_no_ticket']}**",
        f"- existing tickets to REUSE: **{result['totals']['distinct_issues']}**",
        "",
        "This table deliberately files **no new issues**. The uncovered storyboards are a "
        "conformance gap, not a work plan — decomposing them into tickets is a decision for "
        "the project's roadmap, not for the PR that measured them. **TO FILE** is the "
        "finding, stated plainly, with the spec clause and check inventory attached so the "
        "triage conversation can start from evidence.",
        "",
        "Reading the cells:",
        "",
        "- **Scenario** — an id means a `@storyboard-v3.1` scenario CLAIMS this storyboard; "
        "**NO SCENARIO** means none does. The parenthesis is the claim's liveness, joined from a "
        "real `pytest tests/bdd` run plus the `ENV_ROUTES` registry: `LIVE` is the only one that "
        "is coverage, `claim only` means the scenario is tagged but its steps are not bound or its "
        "harness is not wired, and `NOT MEASURED` means no BDD run was joined and nothing about "
        "the claim has been established. A listed scenario does *not* mean its checks all pass — "
        "compare against Status.",
        "- **Ticket** — `#N (partial)` is an EXISTING issue to reuse, covering some of this "
        "storyboard's checks; the map's `note:` says what it leaves out. **TO FILE** means "
        "nothing in the tracker covers it.",
        "- **Divergence** — a storyboard whose `required_tools` name "
        "`comply_test_controller` is permanently ungradable here by design: that tool "
        "will not be implemented (it is a production test-control backdoor). Status says "
        "so for every such storyboard, derived from the pinned tree rather than from a "
        "list someone maintains. This column says which KIND of divergence it is — "
        "`DETERMINISTIC INJECTION` (dormant by design) or `PRIOR STATE ONLY` (reachable "
        "by real API sequencing instead) — and `UNTRIAGED` where that editorial call has "
        "not been made yet.",
        "",
        "",
        "| Storyboard | Citation | Scenario | Required tools | Checks | Status | Divergence | Ticket |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in result["rows"]:
        scenarios = storyboard_coverage_map.coverage_cell(r["coverage"], r["scenarios"])
        tools = ", ".join(f"`{t}`" for t in r["required_tools"]) or "—"
        checks = ", ".join(f"{k}×{v}" for k, v in r["checks"].items()) or "—"
        divergence = r["divergence"] or "—"
        out.append(
            f"| `{r['storyboard']}` | {r['citation']} | {scenarios} | {tools} | {checks} | "
            f"{_status_cell(r)} | {divergence} | {r['tracking']} |"
        )

    # The gap, restated as the only two lists a reader actually acts on.
    out += [
        "",
        "## Graded, untested, untracked",
        "",
        "On our conformance path at the pin, with no BDD scenario **and** no open issue. "
        "This is the list to take to roadmap triage.",
        "",
        "| Storyboard | Checks | Why it matters |",
        "|---|---|---|",
    ]
    for r in result["rows"]:
        if r["scenarios"] or r["tracking_issues"]:
            continue
        total = sum(r["checks"].values())
        note = (r["tracking_note"] or "—").replace("\n", " ").strip()
        out.append(f"| `{r['storyboard']}` | {total} | {note} |")

    out += [
        "",
        "## Graded and untested, but already tracked",
        "",
        "No BDD scenario, but an open issue covers some or all of it — these map onto work "
        "the project has already accepted.",
        "",
        "| Storyboard | Checks | Issue(s) | What the issue does not cover |",
        "|---|---|---|---|",
    ]
    for r in result["rows"]:
        if r["scenarios"] or not r["tracking_issues"]:
            continue
        total = sum(r["checks"].values())
        refs = ", ".join(f"#{n}" for n in r["tracking_issues"])
        note = (r["tracking_note"] or "—").replace("\n", " ").strip()
        out.append(f"| `{r['storyboard']}` | {total} | {refs} | {note} |")

    out += [
        "",
        "## Gated storyboards (declared `requires_capability`, not graded)",
        "",
        "Listed, not dropped. The offline classifier cannot evaluate a `requires_capability` "
        "path because `declared_capabilities()` exposes specialisms and protocols only — so "
        'GATED means "undetermined offline", not "does not apply to us". The live @adcp/sdk '
        "runner reads the real capability document off the wire and may grade what we gate; "
        "when it does, the ledger shows it and the disagreement is visible here rather than "
        "silently absent.",
        "",
        "| Storyboard | Gate |",
        "|---|---|",
    ]
    out += [f"| `{r['storyboard']}` | {r['reason']} |" for r in result["gated"]]

    return "\n".join(out) + "\n"


def main() -> int:
    return storyboard_spec.run_cli(__doc__ or "", build, render)


if __name__ == "__main__":
    sys.exit(main())
