#!/usr/bin/env python3
"""Per-feature BDD coverage scoreboard, graded from what a CI run MEASURED.

Every scenario in ``tests/bdd/features/*.feature`` gets one status, read off the
artifacts a BDD run already writes (``liveness-sessions/*.json`` for outcomes,
``collection-manifest/*.json`` for what was collected). Nothing is inferred from
test names: a scenario with no measured outcome is reported as exactly that.

Statuses (first match wins):

``failing``
    At least one transport recorded ``failed``.
``passing``
    Every observation on every transport passed.
``partial``
    Some observations passed, the rest are xfailed, skipped, or collected but not run.
``dormant``
    Would be ``xfail``, but the run recorded that it never really ran: a step with no
    definition (``steps_bound`` false) or a harness that reached no step body
    (``harness_wired`` false). Both fields come from the merged liveness record.
``xfail``
    Observations exist and none passed; all are xfailed, and its steps ran.
``skipped``
    Collected, but nothing ran to an outcome: every observation is a skip, or a
    selected manifest row with no outcome (skipped before its body ran, or
    ``xfail(run=False)``).
``not_collected``
    In a feature file, but no test module binds it, so nothing collected it.
``untagged``
    Tagged, but with no ``@T-*`` identity tag, so no artifact can name it. (A
    scenario with no tag line at all is not listed: ``storyboard_spec.tagged_scenarios``
    finds scenarios by their tag line. None exist in the tree today.)

Output, under ``--out``:

``scoreboard.json`` (``SCHEMA_VERSION`` 1) — flat enough to loop over in Liquid
(e.g. a Jekyll ``_data/`` file)::

    schema_version  int
    generated_at    ISO-8601 UTC
    repository      "owner/name" or null
    commit          full sha or null;  commit_url
    run_id          CI run id or null; run_url
    transports      [str]  every transport observed, in display order
    statuses        [{id, label, description}]  the vocabulary above, in order
    totals          {scenarios, passing, partial, dormant, xfail, failing, skipped,
                     not_collected, untagged}
    features        [{feature, use_case, file, scenarios, <one count per status>}]
    scenarios       [{feature, use_case, tag, title, line, status,
                      transports: {transport: outcome}, xfail_reason, xfail_strict}]

Per-transport ``outcome`` is ``passed``, ``xfailed``, ``failed``, ``skipped``,
``mixed`` (an outline whose examples disagree) or ``not_run`` (collected, no outcome); a
transport the scenario is not parametrized over is absent. ``xfail_reason`` is the
first xfailed observation's reason. ``xfail_strict`` is ``null`` when the artifact
predates the field. ``use_case`` is the feature name
's leading upper-case segments
(``BR-UC-002-create-media-buy`` -> ``BR-UC-002``; ``local-*`` -> ``local``).

``scoreboard.md`` — the per-feature table, for a job summary.

Which sessions count, the refusal of a partial set, and the fold are
``tests._collection_manifest.merged_records``, the owner ``scenario_liveness.load_run``
also uses; collected-but-unrun rows come from ``tests._collection_manifest.load``. Each
``@T-*`` tag must name one scenario, because the liveness record is keyed by it: a
repeated tag is refused rather than graded as the pooled outcome of two scenarios.

Usage::

    uv run python scripts/audit/coverage_scoreboard.py --artifacts bdd-artifacts \
        --out scoreboard --commit "$SHA" --run-id "$RUN_ID" --repository prebid/salesagent
"""

from __future__ import annotations

import argparse
import collections
import datetime
import itertools
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from scripts.audit import run_identity, storyboard_spec  # noqa: E402
from tests._collection_manifest import IncompleteLivenessRun, load, merged_records, read_artifacts  # noqa: E402
from tests.helpers.bdd_transport import IN_NETWORK_TRANSPORTS, NODEID_TRANSPORTS, transport_of  # noqa: E402

SCHEMA_VERSION = 1

FEATURES_SUBDIR = Path("tests") / "bdd" / "features"

STATUSES: tuple[tuple[str, str, str], ...] = (
    ("passing", "Passing", "Passed on every transport it runs on"),
    ("partial", "Partial", "Passed on some transports or examples, xfailed or not run on the rest"),
    ("dormant", "Dormant", "Xfailed without running: unbound steps, or no harness reached a step"),
    ("xfail", "Expected failure", "Runs, but is xfailed everywhere (a ledgered gap)"),
    ("failing", "Failing", "Recorded a failure"),
    ("skipped", "Skipped", "Collected, but nothing ran to an outcome (skipped, or xfail with run=False)"),
    ("not_collected", "Not collected", "In a feature file, but no test module binds it"),
    ("untagged", "Untagged", "No @T-* identity tag, so no run can report it"),
)
STATUS_IDS = tuple(s[0] for s in STATUSES)

#: Display order for transports; anything else observed sorts after these.
_TRANSPORT_ORDER = ("a2a", "mcp", "rest", "e2e_rest", *NODEID_TRANSPORTS, "default")

_REASON_LIMIT = 400
NOT_RUN = "not_run"


@dataclass
class Scenario:
    feature: str
    tag: str | None
    title: str
    line: int
    transports: dict[str, str] = field(default_factory=dict)
    status: str = "not_collected"
    xfail_reason: str | None = None
    xfail_strict: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "use_case": use_case(self.feature),
            "tag": self.tag,
            "title": self.title,
            "line": self.line,
            "status": self.status,
            "transports": self.transports,
            "xfail_reason": self.xfail_reason,
            "xfail_strict": self.xfail_strict,
        }


def use_case(feature: str) -> str:
    """The feature name's leading upper-case segments: ``BR-UC-002-create-media-buy`` -> ``BR-UC-002``.

    A name with none (``local-tls-ask``) groups under its first segment, ``local``.
    """
    parts = feature.split("-")
    upper = list(itertools.takewhile(lambda part: part == part.upper(), parts))
    return "-".join(upper) if upper else parts[0]


def feature_scenarios(repo: Path) -> list[Scenario]:
    """Every scenario under ``tests/bdd/features`` that carries a tag line.

    Reuses ``storyboard_spec.tagged_scenarios``, matching any tag: its identifier
    is the scenario's ``@T-*`` tag (the identity ``scenario_liveness`` records),
    or ``(unnamed)``. A tag line followed by no ``Scenario`` line (a feature's
    or an ``Examples`` block's tags) has no title and is dropped.

    Raises ``ValueError`` when two scenarios share a ``@T-*`` tag: the liveness record
    is keyed by the tag, so both rows would show the pair's pooled outcome.
    """
    scenarios = [
        Scenario(
            feature=Path(found.feature).stem,
            tag=found.identifier if found.identifier.startswith("T-") else None,
            title=found.title,
            line=found.line,
        )
        for found in storyboard_spec.tagged_scenarios(repo / FEATURES_SUBDIR, tag="@")
        if found.title
    ]
    places: dict[str, list[str]] = collections.defaultdict(list)
    for scenario in scenarios:
        if scenario.tag is not None:
            places[scenario.tag].append(f"{scenario.feature}.feature:{scenario.line}")
    shared = {tag: where for tag, where in places.items() if len(where) > 1}
    if shared:
        raise ValueError(f"@T-* tags declared on more than one scenario, so no run can tell them apart: {shared}")
    return scenarios


@dataclass
class Measurements:
    #: tag -> transport -> [observation]; a selected-but-unrun nodeid is ``NOT_RUN``
    observations: dict[str, dict[str, list[dict[str, Any]]]]
    #: tag -> the merged liveness record (``steps_bound``, ``harness_wired``, ...)
    records: dict[str, dict[str, Any]]


def read_measurements(artifacts: Path, repo: Path) -> Measurements:
    """One run's liveness sessions and collection manifests under ``artifacts``, folded.

    Sessions are folded by ``merged_records``, which refuses (``IncompleteLivenessRun``)
    a set that lost a worker or misses a BDD module of ``repo``, and admits a filtered
    session for its in-network transports only. Selected-but-unrun rows come from the
    unfiltered manifests, plus the in-network manifest's in-network rows when the run
    measured any; ``load`` refuses a missing or self-contradicting manifest.
    """
    sessions = [found for d in sorted(artifacts.glob("**/liveness-sessions")) for found in read_artifacts(d)]
    if not sessions:
        raise IncompleteLivenessRun(f"no liveness-sessions/*.json under {artifacts}: nothing was measured")
    records = merged_records(sessions, artifacts, repo_root=repo)
    observations: dict[str, dict[str, list[dict[str, Any]]]] = collections.defaultdict(
        lambda: collections.defaultdict(list)
    )
    for tag, record in records.items():
        for obs in record.get("observations", []):
            observations[tag][obs.get("transport") or "default"].append(obs)
    observed = {obs.get("nodeid") for record in records.values() for obs in record.get("observations", [])}

    manifests = sorted(artifacts.glob("**/collection-manifest"))
    rows = load(manifests, filter="", selected_only=True)
    if any(transport in IN_NETWORK_TRANSPORTS for per in observations.values() for transport in per):
        in_network = load(manifests, e2e_enabled=True, selected_only=True)
        rows += [row for row in in_network if transport_of(row["nodeid"]) in IN_NETWORK_TRANSPORTS]
    # A selected row with no outcome is recorded as ``not_run`` beside whatever its
    # siblings on the same transport recorded, so an outline with one example run and
    # one parked cannot read as fully passing.
    for row in rows:
        nodeid = row["nodeid"]
        if nodeid in observed:
            continue
        observed.add(nodeid)
        transport = transport_of(nodeid) or "default"
        for marker in row.get("markers", []):
            if marker.startswith("T-"):
                observations[marker][transport].append({"nodeid": nodeid, "outcome": NOT_RUN})
    return Measurements(observations=observations, records=records)


def _transport_outcome(obs: list[dict[str, Any]]) -> str:
    outcomes = {o.get("outcome") for o in obs}
    if "failed" in outcomes:
        return "failed"
    if len(outcomes) == 1:
        return str(outcomes.pop())
    return "mixed"


def grade(scenario: Scenario, measured: Measurements) -> None:
    """Set the scenario's per-transport outcomes, status and xfail detail."""
    if scenario.tag is None:
        scenario.status = "untagged"
        return
    per_transport = measured.observations.get(scenario.tag, {})
    scenario.transports = {t: _transport_outcome(per_transport[t]) for t in sort_transports(per_transport)}
    outcomes = [o.get("outcome") for obs in per_transport.values() for o in obs]
    ran = [o for o in outcomes if o not in (NOT_RUN, "skipped")]
    record = measured.records.get(scenario.tag, {})
    if "failed" in ran:
        scenario.status = "failing"
    elif ran and len(ran) == len(outcomes) and all(o == "passed" for o in ran):
        scenario.status = "passing"
    elif "passed" in ran:
        scenario.status = "partial"
    elif ran and (record.get("steps_bound") is False or record.get("harness_wired") is False):
        scenario.status = "dormant"
    elif ran:
        scenario.status = "xfail"
    elif outcomes:
        scenario.status = "skipped"
    else:
        scenario.status = "not_collected"
    xfails = [o for obs in per_transport.values() for o in obs if o.get("outcome") == "xfailed"]
    if xfails:
        scenario.xfail_reason = str(xfails[0].get("reason") or "")[:_REASON_LIMIT] or None
        stricts = {o["strict"] for o in xfails if isinstance(o.get("strict"), bool)}
        scenario.xfail_strict = stricts.pop() if len(stricts) == 1 else (None if not stricts else False)


def sort_transports(names: Any) -> list[str]:
    rank = {name: i for i, name in reversed(list(enumerate(_TRANSPORT_ORDER)))}
    return sorted(names, key=lambda n: (rank.get(n, len(rank)), n))


def build(
    repo: Path,
    artifacts: Path,
    *,
    commit: str | None = None,
    run_id: str | None = None,
    repository: str | None = None,
    server_url: str = "https://github.com",
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    """The whole scoreboard as one JSON-serializable dict."""
    measured = read_measurements(artifacts, repo)
    scenarios = feature_scenarios(repo)
    for scenario in scenarios:
        grade(scenario, measured)

    features: dict[str, dict[str, Any]] = {}
    for scenario in scenarios:
        row = features.setdefault(
            scenario.feature,
            {
                "feature": scenario.feature,
                "use_case": use_case(scenario.feature),
                "file": f"{FEATURES_SUBDIR.as_posix()}/{scenario.feature}.feature",
                "scenarios": 0,
                **dict.fromkeys(STATUS_IDS, 0),
            },
        )
        row["scenarios"] += 1
        row[scenario.status] += 1
    totals = {"scenarios": len(scenarios), **dict.fromkeys(STATUS_IDS, 0)}
    for scenario in scenarios:
        totals[scenario.status] += 1

    repo_url = f"{server_url}/{repository}" if repository else None
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": (now or datetime.datetime.now(datetime.UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repository": repository,
        "commit": commit,
        "commit_url": f"{repo_url}/commit/{commit}" if repo_url and commit else None,
        "run_id": run_id,
        "run_url": f"{repo_url}/actions/runs/{run_id}" if repo_url and run_id else None,
        "transports": sort_transports({t for s in scenarios for t in s.transports}),
        "statuses": [{"id": i, "label": label, "description": d} for i, label, d in STATUSES],
        "totals": totals,
        "features": list(features.values()),
        "scenarios": [s.to_dict() for s in scenarios],
    }


def render_markdown(board: dict[str, Any]) -> str:
    """The per-feature table, headed by the run it was graded from."""
    source = board["run_url"] or board["run_id"] or "local artifacts"
    lines = [
        "### BDD coverage scoreboard",
        "",
        f"Commit `{board['commit'] or 'unknown'}`, run {source}, generated {board['generated_at']}.",
        "",
    ]
    header = ["feature", "scenarios", *STATUS_IDS]
    lines += ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for row in [*board["features"], {"feature": "**total**", **board["totals"]}]:
        lines.append("| " + " | ".join(str(row[h]) for h in header) + " |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--repo", type=Path, default=_REPO_ROOT, help="repository checkout (feature files)")
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=None,
        help="ONE run's BDD artifacts (default: the newest test-results/<run> with liveness sessions, by meta.json)",
    )
    parser.add_argument("--out", type=Path, default=Path("test-results") / "coverage-scoreboard")
    parser.add_argument("--commit", help="commit sha the artifacts were produced from")
    parser.add_argument("--run-id", help="CI run id the artifacts came from")
    parser.add_argument("--repository", help="owner/name, for links")
    parser.add_argument("--markdown", action="store_true", help="also print the markdown table to stdout")
    args = parser.parse_args(argv)

    artifacts = args.artifacts or run_identity.newest_run(args.repo / "test-results", "liveness-sessions")
    if artifacts is None:
        parser.error("no test-results/<run> holds liveness-sessions and a meta.json; pass --artifacts")
    board = build(args.repo, artifacts, commit=args.commit, run_id=args.run_id, repository=args.repository)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "scoreboard.json").write_text(json.dumps(board, separators=(",", ":")) + "\n", encoding="utf-8")
    markdown = render_markdown(board)
    (args.out / "scoreboard.md").write_text(markdown, encoding="utf-8")
    if args.markdown:
        sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
