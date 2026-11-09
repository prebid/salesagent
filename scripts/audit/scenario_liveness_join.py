"""Join BDD scenario liveness into the storyboard-checks pipeline.

``storyboard_check_index`` derives ``covered_by`` from tag presence plus the
``@source`` footer alone — a ``@storyboard-v3.1``-tagged scenario with zero bound
step definitions has counted as "covered" forever, because nothing joined the
three liveness facts that already exist as data (the finding).
This module is that join:

* ``steps_bound`` — measured by a real ``pytest tests/bdd`` run, emitted by
  ``tests/bdd/scenario_liveness.py`` as
  ``test-results/bdd_scenario_liveness.json``.
* ``registry_wired`` — a DATA LOOKUP against the declarative
  ``tests.bdd.conftest.ENV_ROUTES`` registry, never
  reason-text matching. A scenario is registry-wired when its own ``T-*`` tag is a
  row (the per-scenario demonstrator), or the ``UC-<n>`` bucket its
  ``T-UC-<n>...`` tag derives — the same derivation ``conftest._detect_uc`` uses
  for a tag matching that pattern — is a row, and that row isn't a placeholder
  (``EnvRoute.xfail_reason`` unset). The registry only covers a subset of UCs
  today (see ``ENV_ROUTES``'s own comment in conftest.py); a scenario whose UC
  isn't in the registry yet is ``registry_wired=False``, not "unknown" — this join
  is deliberately conservative, and will only grow more scenarios into
  ``graded_by_live_scenario`` as the registry widens. This is why the join
  replaces the artifact's own best-effort, reason-text-derived ``harness_wired``
  field rather than trusting it: that field is only as good as the auto-xfail
  reason text conftest happens to produce today, exactly what this task exists to
  stop relying on.
* ``ledgered`` — read straight from the artifact, itself already a join of the
  e2e_rest known-failures ledger and conftest's curated xfail-reason bucket
 . Combined here with the conformance-ledger ``measured``
  column ``CheckRecord`` already carries (built from ``scripts/audit/ledger.py``'s
  ``load()`` against ``tests/storyboard/known_failures.txt`` — the third ledger)
  to flag a graduation candidate: a scenario we locally xfail as a known gap, for
  a check the real conformance run does not currently measure as failing.

A scenario absent from the artifact (no BDD run this session, or the run was
narrowed past it) is ``measured_this_run=False`` and reports
``steps_bound=False``/``ledgered=False`` — missing measurement renders as a gap,
never as a false "it's live" positive.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.audit import storyboard_spec
from src.core.config import ToolingSettings

#: The artifact field carrying the scenario's marker set — the join's only
#: marker source, and why the plugin persists it.
_RECORD_MARKER_FIELD = "marker_names"

_ARTIFACT_DEFAULT = Path("test-results") / storyboard_spec.DEFAULT_ARTIFACT_PATH

# The UC bucket derivation is no longer re-implemented here — it lives with the
# rest of the shared contract. This module and tests/bdd/conftest.py used to hold
# byte-identical copies, each with a comment saying it mirrored the other, and
# they DIVERGED in what they did next: the conftest matched a bucket and then
# fell through a chain of marker predicates this module knew nothing about, so
# every predicate-routed scenario looked dormant from here.


@dataclass(frozen=True)
class ScenarioLiveness:
    """One claiming scenario's joined liveness verdict."""

    scenario_id: str
    steps_bound: bool
    registry_wired: bool
    ledgered: bool
    measured_this_run: bool

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the JSON report.

        Every field, mechanically. The hand-written version omitted ``scenario_id``; asdict includes it, which makes each record self-describing rather than meaningful only under its parent key.
        """
        return dataclasses.asdict(self)

    @property
    def graded_by_live_scenario(self) -> bool:
        """The strict, join-backed liveness verdict.

        ``steps_bound`` alone (the parent finding's original "4-of-21" hand
        measurement) is not enough — a scenario whose harness routing isn't
        registry-verified stays ungraded, and a scenario the artifact never
        observed this run (``measured_this_run=False``) can never be graded by
        omission.
        """
        return self.measured_this_run and self.steps_bound and self.registry_wired


def load_env_routes() -> list[Any]:
    """The declarative env-routing registry — pure data, in resolution order.

    Deferred import: this module has no import-time coupling to the pytest-bdd
    conftest it reads ``ENV_ROUTES`` from, and importing that module standalone
    (outside a pytest session) needs no database or fixture context — every
    heavier import in conftest.py lives inside a function body, not at module
    scope.
    """
    from tests.bdd.conftest import ENV_ROUTES

    return ENV_ROUTES


def registry_wired(marker_names: Any, env_routes: Any) -> bool:
    """Is this scenario routed to a real harness env?

    Delegates to ``storyboard_spec.resolve_env_route`` — the SAME function the
    BDD conftest routes on. It used to re-implement a narrower lookup here (the
    scenario's own tag, then its ``UC-<n>`` bucket), which knew nothing about the
    conftest's marker-predicate branches; every scenario routed by one of those
    was therefore reported DORMANT even though it ran. That false positive is
    exactly what this join exists to eliminate, so the lookup is no longer
    written twice.

    A row with ``xfail_reason`` set is a registered placeholder, not a real wire.
    """
    route = storyboard_spec.resolve_env_route(marker_names, env_routes)
    return route is not None and route.xfail_reason is None


def default_artifact_path() -> Path:
    """The artifact path, from the ONE owner of the env var and default name."""
    override = ToolingSettings().bdd_liveness_artifact  # storyboard_spec.ARTIFACT_ENV_VAR
    return override if override else _ARTIFACT_DEFAULT


def load_artifact(path: Path) -> dict[str, dict[str, Any]]:
    """``scenario_id -> raw liveness record`` from a real ``pytest tests/bdd`` run.

    ``{}`` when the artifact is absent — every scenario then reports
    ``measured_this_run=False``, never silently assumed live.
    """
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    _reject_narrowed_run(path, data.get("run"))
    return {s["scenario_id"]: s for s in data.get("scenarios", [])}


def _reject_narrowed_run(path: Path, run: dict[str, Any] | None) -> None:
    """A narrowed session's artifact is not a measurement of the suite.

    Absence fails closed already; NARROWING did not. ``pytest tests/bdd -k
    "storyboard and recancel"`` over a 900-record artifact leaves one record, and
    every other scenario reads as dormant rather than as unasked. The writer now
    records its own scope, so the distinction is in the file rather than
    inferred from it.

    TWO narrowings, because pytest records them in two places and for a long time
    only one was asked about. ``-k``/``-m`` land on ``selection``/``markers``; a run
    narrowed by PATH leaves both EMPTY and lands on ``args``. That was the hole, and
    it was the form actually in use: ``pytest tests/bdd/test_uc010_...py`` sets
    neither expression, so a 24-record artifact over a suite of some eight thousand
    parametrized scenarios was accepted as a full measurement and every scenario it
    did not name read as dormant. The ``-k`` door was watched and the path door was
    not (salesagent-peq79).

    There is deliberately NO floor on ``collected``. A floor needs a number, and the
    only honest source for it is one full run's count -- which goes stale the day a
    scenario is added or removed, failing the build for a non-reason, or needs ratchet
    machinery to maintain it. It is also unsound in both directions: near the true
    count, suite shrinkage breaks it; low enough to survive shrinkage, and the largest
    single module -- over a thousand items on its own -- passes. ``args`` needs no
    number. A run that asked for the suite root or an ancestor asked the whole
    question; anything else asked a narrower one.

    ``run`` is absent in an artifact written before that landed, and ``args`` is
    absent in one written before THIS landed. Both treated as legacy and allowed
    through: it is a gitignored, regenerated file, and refusing it would fail the
    first run after an upgrade rather than the condition worth catching.
    """
    if not run:
        return
    selection = run.get("selection") or ""
    markers = run.get("markers") or ""
    if selection or markers:
        raise storyboard_spec.StoryboardAuditError(
            f"{path} was written by a NARROWED bdd run "
            f"(-k {selection!r}, -m {markers!r}; {run.get('collected')} collected). "
            "Every scenario it does not name would report dormant, which is a claim about "
            "production rather than about the question that was asked. Re-run the full suite "
            "(`pytest tests/bdd`) before regenerating anything that quotes liveness."
        )
    args = run.get("args")
    suite = run.get("suite") or ""
    narrower = [a for a in args or () if not _asks_for_suite(a, suite)] if args and suite else []
    if narrower:
        raise storyboard_spec.StoryboardAuditError(
            f"{path} was written by a bdd run narrowed BY PATH to {narrower} "
            f"({run.get('collected')} collected of the {suite!r} suite). It records no -k and "
            "no -m, so it used to pass as a full measurement; every scenario outside those "
            "paths would report dormant, which is a claim about production rather than about "
            "the question that was asked. Re-run the full suite (`pytest tests/bdd`) before "
            "regenerating anything that quotes liveness."
        )


def _asks_for_suite(arg: str, suite: str) -> bool:
    """Whether collection argument *arg* asked for the whole *suite*.

    True for the suite itself and for any ancestor of it -- ``tests`` and ``.`` both
    collect every bdd scenario on the way past. Compared as posix strings because
    that is how the writer records them; the trailing slash is what keeps
    ``tests/bdd-extra`` from counting as a parent of ``tests/bdd``.

    An ABSOLUTE *arg* is false here for every ancestor, and that is correct rather
    than a hole: the writer resolves each argument repo-relative (``_scope_paths``,
    measured -- invoking pytest with a full path records ``tests/bdd/...``), so the
    only arg that stays absolute is one pointing OUTSIDE the repo, which collects no
    bdd scenario and therefore measured no part of this suite.
    """
    return arg in {".", suite} or suite.startswith(f"{arg}/")


def build_index(
    scenario_ids: set[str],
    *,
    artifact_path: Path | None = None,
    env_routes: dict[str, Any] | None = None,
) -> dict[str, ScenarioLiveness]:
    """One :class:`ScenarioLiveness` per claiming scenario id."""
    artifact = load_artifact(artifact_path if artifact_path is not None else default_artifact_path())
    routes = env_routes if env_routes is not None else load_env_routes()
    index: dict[str, ScenarioLiveness] = {}
    for scenario_id in scenario_ids:
        record = artifact.get(scenario_id)
        # The MARKER SET comes from the record — the join has no other source for
        # it, and routing now keys on markers rather than on the scenario id
        # alone (4a). An UNMEASURED scenario therefore resolves NOT-WIRED: with
        # no marker set there is nothing to route on, and reporting it wired
        # would be a guess. That is consistent, because graded_by_live_scenario
        # already ANDs measured_this_run.
        marker_names = frozenset(record.get(_RECORD_MARKER_FIELD) or ()) if record is not None else None
        index[scenario_id] = ScenarioLiveness(
            scenario_id=scenario_id,
            steps_bound=bool(record["steps_bound"]) if record is not None else False,
            registry_wired=registry_wired(marker_names, routes) if marker_names is not None else False,
            ledgered=bool(record["ledgered"]) if record is not None else False,
            measured_this_run=record is not None,
        )
    return index
