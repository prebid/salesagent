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
    Some observations passed, the rest are xfailed (or collected but not run).
``xfail``
    Observations exist and none passed; all are xfailed.
``skipped``
    Collected (a selected manifest row carries its tag) but no outcome was
    recorded: skipped before its body ran, or ``xfail(run=False)``.
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
    totals          {scenarios, passing, partial, xfail, failing, skipped,
                     not_collected, untagged}
    storyboard      {floor: {protocol: int}, measured: [{protocol, run, passed,
                     failed, skipped, storyboards}]}  measured is [] when the
                     artifacts carry no runner summary
    features        [{feature, use_case, file, scenarios, <one count per status>}]
    scenarios       [{feature, use_case, tag, title, line, status,
                      transports: {transport: outcome}, xfail_reason,
                      xfail_category, xfail_strict}]

Per-transport ``outcome`` is ``passed``, ``xfailed``, ``failed``, ``mixed``
(an outline whose examples disagree) or ``not_run`` (collected, no outcome); a
transport the scenario is not parametrized over is absent. ``xfail_strict`` is
``null`` when the artifact predates the field. ``use_case`` is the feature name
's leading upper-case segments
(``BR-UC-002-create-media-buy`` -> ``BR-UC-002``; ``local-*`` -> ``local``).

``scoreboard.md`` — the per-feature table, for a job summary.

Usage::

    python3 scripts/audit/coverage_scoreboard.py --artifacts bdd-artifacts \
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

from scripts.audit import storyboard_runner_score, storyboard_spec  # noqa: E402
from tests._collection_manifest import read_artifacts  # noqa: E402
from tests.helpers.bdd_transport import NODEID_TRANSPORTS, transport_of  # noqa: E402

SCHEMA_VERSION = 1

FEATURES_SUBDIR = Path("tests") / "bdd" / "features"
PASS_FLOOR_FILE = ".storyboard-pass-floor"

STATUSES: tuple[tuple[str, str, str], ...] = (
    ("passing", "Passing", "Passed on every transport it runs on"),
    ("partial", "Partial", "Passed on some transports or examples, xfailed or not run on the rest"),
    ("xfail", "Expected failure", "Runs, but is xfailed everywhere (known gap or unbound steps)"),
    ("failing", "Failing", "Recorded a failure"),
    ("skipped", "Skipped", "Collected, but no outcome recorded (skipped, or xfail with run=False)"),
    ("not_collected", "Not collected", "In a feature file, but no test module binds it"),
    ("untagged", "Untagged", "No @T-* identity tag, so no run can report it"),
)
STATUS_IDS = tuple(s[0] for s in STATUSES)

#: Display order for transports; anything else observed sorts after these.
_TRANSPORT_ORDER = ("a2a", "mcp", "rest", "e2e_rest", *NODEID_TRANSPORTS, "default")

_REASON_LIMIT = 400


@dataclass
class Scenario:
    feature: str
    tag: str | None
    title: str
    line: int
    transports: dict[str, str] = field(default_factory=dict)
    status: str = "not_collected"
    xfail_reason: str | None = None
    xfail_category: str | None = None
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
            "xfail_category": self.xfail_category,
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
    """
    return [
        Scenario(
            feature=Path(found.feature).stem,
            tag=found.identifier if found.identifier.startswith("T-") else None,
            title=found.title,
            line=found.line,
        )
        for found in storyboard_spec.tagged_scenarios(repo / FEATURES_SUBDIR, tag="@")
        if found.title
    ]


def _payloads(artifacts: Path, subdir: str) -> list[dict[str, Any]]:
    """Every per-session JSON file in every ``subdir`` under ``artifacts``."""
    return [
        payload
        for directory in sorted(artifacts.glob(f"**/{subdir}"))
        for _, payload in read_artifacts(directory)
        if isinstance(payload, dict)
    ]


@dataclass
class Measurements:
    #: tag -> transport -> [observation]
    observations: dict[str, dict[str, list[dict[str, Any]]]]
    #: tag -> transports it was collected (and selected) on
    collected: dict[str, set[str]]
    sessions: int


def read_measurements(artifacts: Path) -> Measurements:
    """Fold every liveness session and collection manifest under ``artifacts``."""
    observations: dict[str, dict[str, list[dict[str, Any]]]] = collections.defaultdict(
        lambda: collections.defaultdict(list)
    )
    observed_nodeids: set[str] = set()
    sessions = _payloads(artifacts, "liveness-sessions")
    for session in sessions:
        for record in session.get("scenarios", []):
            for obs in record.get("observations", []):
                observations[record["scenario_id"]][obs.get("transport") or "default"].append(obs)
                observed_nodeids.add(obs.get("nodeid", ""))
    collected: dict[str, set[str]] = collections.defaultdict(set)
    for manifest in _payloads(artifacts, "collection-manifest"):
        for row in manifest.get("rows", []):
            if not row.get("selected"):
                continue
            transport = transport_of(row.get("nodeid", "")) or "default"
            for marker in row.get("markers", []):
                if marker.startswith("T-"):
                    collected[marker].add(transport)
                    if row.get("nodeid") not in observed_nodeids:
                        observations[marker].setdefault(transport, [])
    return Measurements(observations=observations, collected=collected, sessions=len(sessions))


def _transport_outcome(obs: list[dict[str, Any]]) -> str:
    outcomes = {o.get("outcome") for o in obs}
    if not outcomes:
        return "not_run"
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
    if "failed" in outcomes:
        scenario.status = "failing"
    elif outcomes and all(o == "passed" for o in outcomes) and "not_run" not in scenario.transports.values():
        scenario.status = "passing"
    elif "passed" in outcomes:
        scenario.status = "partial"
    elif outcomes:
        scenario.status = "xfail"
    elif scenario.tag in measured.collected or per_transport:
        scenario.status = "skipped"
    else:
        scenario.status = "not_collected"
    xfails = [o for obs in per_transport.values() for o in obs if o.get("outcome") == "xfailed"]
    if xfails:
        reason, _ = collections.Counter(str(o.get("reason") or "") for o in xfails).most_common(1)[0]
        scenario.xfail_reason = reason[:_REASON_LIMIT] or None
        categories = collections.Counter(o.get("reason_category") for o in xfails)
        scenario.xfail_category = categories.most_common(1)[0][0]
        stricts = {o["strict"] for o in xfails if isinstance(o.get("strict"), bool)}
        scenario.xfail_strict = stricts.pop() if len(stricts) == 1 else (None if not stricts else False)


def sort_transports(names: Any) -> list[str]:
    rank = {name: i for i, name in reversed(list(enumerate(_TRANSPORT_ORDER)))}
    return sorted(names, key=lambda n: (rank.get(n, len(rank)), n))


def storyboard_score(repo: Path, artifacts: Path) -> dict[str, Any]:
    """The asserted floor from the repo, and the runner's own summary if the artifacts carry one."""
    floor_file = repo / PASS_FLOOR_FILE
    floor = json.loads(floor_file.read_text(encoding="utf-8")) if floor_file.is_file() else {}
    summaries = sorted(artifacts.glob("**/storyboard/summary_*.json"))
    return {"floor": floor, "measured": [storyboard_runner_score.score(path) for path in summaries]}


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
    measured = read_measurements(artifacts)
    if measured.sessions == 0:
        raise SystemExit(f"no liveness-sessions/*.json under {artifacts}: nothing was measured")
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
        "storyboard": storyboard_score(repo, artifacts),
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
    floor = board["storyboard"]["floor"]
    if board["storyboard"]["measured"]:
        scores = ", ".join(f"{m['protocol']} {m['passed']} passed" for m in board["storyboard"]["measured"])
        lines += [f"Storyboard conformance: {scores}.", ""]
    elif floor:
        floors = ", ".join(f"{p} >= {n}" for p, n in floor.items())
        lines += [f"Storyboard conformance floor (asserted by CI): {floors} checks passing.", ""]
    header = ["feature", "scenarios", *STATUS_IDS]
    lines += ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for row in [*board["features"], {"feature": "**total**", **board["totals"]}]:
        lines.append("| " + " | ".join(str(row[h]) for h in header) + " |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--repo", type=Path, default=_REPO_ROOT, help="repository checkout (feature files)")
    parser.add_argument(
        "--artifacts", type=Path, default=Path("test-results"), help="directory holding downloaded BDD artifacts"
    )
    parser.add_argument("--out", type=Path, default=Path("test-results") / "coverage-scoreboard")
    parser.add_argument("--commit", help="commit sha the artifacts were produced from")
    parser.add_argument("--run-id", help="CI run id the artifacts came from")
    parser.add_argument("--repository", help="owner/name, for links")
    parser.add_argument("--markdown", action="store_true", help="also print the markdown table to stdout")
    args = parser.parse_args(argv)

    board = build(args.repo, args.artifacts, commit=args.commit, run_id=args.run_id, repository=args.repository)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "scoreboard.json").write_text(json.dumps(board, separators=(",", ":")) + "\n", encoding="utf-8")
    markdown = render_markdown(board)
    (args.out / "scoreboard.md").write_text(markdown, encoding="utf-8")
    if args.markdown:
        sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
