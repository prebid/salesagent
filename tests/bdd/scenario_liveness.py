"""Emit the scenario-liveness artifact from a real BDD run.

``storyboard_coverage_map.covered_storyboards`` (and the check index built on
top of it) derives "covered" from tag presence plus the ``@source``
footer alone. Nothing in that path asks whether the scenario actually RUNS —
and ``tests/bdd/conftest.py``'s own auto-xfail hook converts
``StepDefinitionNotFoundError`` into ``xfail``, so a ``@storyboard-v3.1``-tagged
scenario with zero bound step definitions counts as coverage forever. This
module is the fix for the measurement, not the report: it emits, from a real
``pytest tests/bdd`` invocation, one liveness record per tagged scenario, and
each record carries the three facts the parent finding names:

* ``steps_bound`` — measured, not inferred. Computed by walking every step of
  the scenario through pytest-bdd's own ``get_step_function`` (the exact
  function ``_execute_scenario`` uses to decide whether to raise
  ``StepDefinitionNotFoundError``) *before* any step body runs, via the
  ``pytest_bdd_before_scenario`` hook. This checks every step, not just the
  first one pytest-bdd would fail on — a scenario whose first three steps are
  bound and fourth is not still reports ``steps_bound=False`` with the
  unbound step named, instead of silently stopping at the first failure the
  way a live run's exception would.
* ``harness_wired`` — a best-effort signal for THIS module only, derived from
  the MEASURED ``steps_executed`` fact rather than from any reason text: True
  once a step body of the run began executing, False for a run that never got
  one (every blanket xfail route aborts in fixture setup), and ``None`` only
  while no observation has been folded in at all. A partly-dormant scenario
  therefore reads True — ``StepDefinitionNotFoundError`` is raised at the FIRST
  unmatched step, so the Background and any earlier steps have already run, and
  ``steps_bound``/``unbound_steps`` are what report the dormancy.
  ``scripts/audit/scenario_liveness_join.py`` does not trust this field — it replaces it with a
  proper data lookup against the declarative ``ENV_ROUTES`` registry (no
  reason-text matching), which is why this module's own ``harness_wired`` is
  documented as best-effort rather than promoted further here: any UC not yet
  a row in ``ENV_ROUTES`` should read as not-wired downstream, not as
  whatever a step body having run happens to imply.
* ``ledgered`` — True when the observation's reason falls into neither of the
  above two buckets (an explicit, curated ``xfail`` marker for a known
  production/spec gap), or the scenario's e2e_rest nodeid appears in
  ``tests/bdd/e2e_rest_known_failures.txt``. ``scripts/audit/scenario_liveness_join.py``
  reads this field straight through and combines it with the conformance-ledger
  ``measured`` column ``storyboard_check_index.CheckRecord`` already carries
  (the third ledger, via ``scripts/audit/ledger.py``'s L0 loaders) to flag
  graduation candidates — this module only reads the one ledger BDD itself
  already loads.

Emits ``test-results/bdd_scenario_liveness.json`` (or
``$BDD_LIVENESS_ARTIFACT``) at session end — a per-run artifact, not a
checked-in one; test-results/ is gitignored. A run that never collects any
``@storyboard-v3.1``-tagged scenario (e.g. ``-k`` narrowed to something else)
writes an artifact with an empty ``scenarios`` list rather than skipping the
write — "the artifact didn't run this session" must be visible in the file
itself, not inferred from the file's absence.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from pytest_bdd.exceptions import StepDefinitionNotFoundError
from pytest_bdd.scenario import get_step_function

from scripts.audit import storyboard_spec
from tests.helpers.ledger import load_ledger_nodeids
from tests.helpers.marker_names import derive_marker_names

if TYPE_CHECKING:
    from _pytest.fixtures import FixtureRequest
    from pytest_bdd.parser import Feature, Scenario, Step

# IMPORTED, not re-declared. The previous comment here forbade this
# import "so this pytest plugin has no import-time dependency on the audit CLI
# toolchain" — but importing a module that DEFINES an entry point does not
# EXECUTE one: storyboard_spec's module-level imports are stdlib-only and it
# imports no conftest, so no CLI runtime and no cycle comes with it. The cost of
# the literal was real: this file held "storyboard-v3.1" while storyboard_spec
# held "@storyboard-v3.1", so any consumer comparing them had to know which
# spelling it was holding.
_TAG = storyboard_spec.STORYBOARD_TAG

_E2E_REST_LEDGER = Path(__file__).parent / "e2e_rest_known_failures.txt"
_LEDGERED_NODEIDS: frozenset[str] = load_ledger_nodeids(_E2E_REST_LEDGER)

_DEFAULT_ARTIFACT_PATH = Path("test-results") / storyboard_spec.DEFAULT_ARTIFACT_PATH

_SCENARIO_ID_KEY = pytest.StashKey[str]()


@dataclass(frozen=True)
class Observation:
    """One (scenario, transport) run outcome."""

    transport: str
    nodeid: str
    outcome: str  # "passed" | "failed" | "xfailed"
    reason: str | None
    reason_category: str  # "live" | "no_steps_bound" | "harness_not_wired" | "ledgered"
    #: MEASURED: at least one step body of this (scenario, transport) run began
    #: executing. Recorded by ``pytest_bdd_before_step_call``, which pytest-bdd
    #: fires immediately before it calls a step function — so a run aborted in
    #: fixture setup (which is where every blanket xfail route lives) records
    #: False, whatever its reason string says. This is the structural fact
    #: ``harness_wired`` is derived from; the reason string is metadata.
    steps_executed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "transport": self.transport,
            "nodeid": self.nodeid,
            "outcome": self.outcome,
            "reason": self.reason,
            "reason_category": self.reason_category,
            "steps_executed": self.steps_executed,
        }


@dataclass
class ScenarioLiveness:
    """One ``@storyboard-v3.1``-tagged scenario's liveness, joined across transports."""

    scenario_id: str
    feature: str
    steps_bound: bool = True
    unbound_steps: list[str] = field(default_factory=list)
    harness_wired: bool | None = None
    ledgered: bool = False
    #: Every tag the scenario carries, WITHOUT the leading ``@``. The provenance
    #: tag (``storyboard-v3.1`` / ``schema-v3.1``) lives here as DATA. It used to
    #: be a collection FILTER — an early return when the scenario lacked it —
    #: which meant a retag could silently delete a scenario from the measurement.
    #: Membership is now the ``@T-*`` identity tag, which a retag cannot move.
    tags: list[str] = field(default_factory=list)
    #: The scenario's marker set, as the ROUTING CONTRACT derives it. Persisted
    #: because the audit join has no other marker source and now resolves the
    #: route with the same resolver the conftest uses — routing on the scenario
    #: id alone was blind to every marker-predicate branch.
    marker_names: list[str] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)

    def record_unbound(self, step_texts: list[str]) -> None:
        if not step_texts:
            return
        self.steps_bound = False
        for text in step_texts:
            if text not in self.unbound_steps:
                self.unbound_steps.append(text)

    def record_observation(self, obs: Observation) -> None:
        """Fold one (scenario, transport) outcome in.

        ``harness_wired`` is derived from ``obs.steps_executed`` — a fact the run
        recorded — and never from the reason text. The reason only ever selects
        BETWEEN the not-wired verdicts; it cannot produce a wired one.

        This is the fix for the defect the ``_classify_reason`` docstring
        describes from the other side. "ledgered" is that function's RESIDUAL
        bucket: every reason matching neither literal prefix nor both harness
        tokens lands there, and "ledgered" used to set ``harness_wired=True``.
        Two blanket routes that abort a scenario inside fixture setup name
        reasons that land in it: ``uc010-not-wired``, whose message says "wiring"
        where the token is "wired", and ``uc006-unclassified``, which contains
        neither token. Fed to the old logic both produce a WIRED verdict for a
        scenario that executed nothing.

        Scope, measured rather than assumed: neither reason appears in
        ``test-results/bdd_scenario_liveness.json`` as it stands, because both
        rows are catch-alls currently shadowed by more specific ones. So this
        removes a mechanism, not present inflation — the count does not move
        today. It is still the fix, because under-matching inflates coverage and
        the next rewording or newly-reachable catch-all would do it silently. A
        step either ran or it did not, and no wording changes that.
        """
        self.observations.append(obs)
        if obs.reason_category == "ledgered" or obs.nodeid in _LEDGERED_NODEIDS:
            self.ledgered = True
        # The verdict is AND over observations of one measured fact, so no reason
        # string appears in it at all. The former ``harness_not_wired`` text branch
        # is not merely unnecessary but unreachable as a separate case: that reason
        # is raised by ``_harness_env``'s catch-all during FIXTURE setup, where no
        # step can have run, so ``steps_executed`` is already False wherever it
        # fires. Keeping the branch would leave the verdict sensitive to wording in
        # exactly one corner, which is the defect with a smaller blast area rather
        # than a fixed one.
        if not obs.steps_executed:
            # False dominates the cross-shard merge too, which is the safe direction.
            self.harness_wired = False
        elif self.harness_wired is not False:
            self.harness_wired = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "feature": self.feature,
            "steps_bound": self.steps_bound,
            "unbound_steps": self.unbound_steps,
            "harness_wired": self.harness_wired,
            "ledgered": self.ledgered,
            "tags": self.tags,
            "marker_names": self.marker_names,
            "observations": [o.to_dict() for o in self.observations],
        }


_RECORDS: dict[str, ScenarioLiveness] = {}

#: Key under which a worker ships its records on ``config.workeroutput``.
_WORKEROUTPUT_KEY = "bdd_scenario_liveness"

#: Controller-side merge target: ``scenario_id -> record dict``, folded from every
#: worker's shard plus the controller's own (empty under xdist) records.
_SHARDS: dict[str, dict[str, Any]] = {}

#: Node ids whose run got at least one step body executed. Written by
#: ``pytest_bdd_before_step_call``; read once when the observation is built.
#: A set of ids rather than a counter: the question is whether ANY step ran.
_STEPS_RAN: set[str] = set()


def _scenario_identifier(scenario: Scenario, feature: Feature) -> str | None:
    """The scenario's ``T-*`` identity tag, matching ``storyboard_spec``'s ``_IDENT_TAG_RE``."""
    for tag in scenario.tags | feature.tags:
        if tag.startswith("T-"):
            return tag
    return None


def _classify_reason(reason: str | None) -> str:
    """Bucket a captured xfail reason into the three categories the parent finding names.

    Grounded in conftest.py's actual, distinct code paths — not free-text
    guessing: ``StepDefinitionNotFoundError``/``NotImplementedError`` always
    produce the two literal prefixes matched below (the auto-xfail hookwrapper
    in ``tests/bdd/conftest.py`` builds them verbatim), and every
    harness-selection fallback raises ``pytest.xfail`` with a message
    containing both "harness" and "wired" (``_harness_env``'s catch-all and
    the UC-004 harness-type fallback). Anything else reaching xfail is an
    explicit, curated marker for a known gap — the residual "ledgered" bucket.
    """
    if reason is None:
        return "live"
    if reason.startswith("Step definition not found:") or reason.startswith("Not implemented:"):
        return "no_steps_bound"
    lowered = reason.lower()
    if "harness" in lowered and "wired" in lowered:
        return "harness_not_wired"
    return "ledgered"


def _classify_exception(excinfo: Any) -> str | None:
    """The category implied by the EXCEPTION, or None when there is nothing typed to read.

    ``StepDefinitionNotFoundError`` and ``NotImplementedError`` are the two
    auto-xfail causes conftest converts, and the exception class says so without
    anyone having to preserve a sentence. Returning None (rather than guessing)
    keeps marker-based and explicit ``pytest.xfail()`` reasons on the prose path,
    where the reason genuinely is the only signal.
    """
    if excinfo is None:
        return None
    if excinfo.errisinstance(StepDefinitionNotFoundError) or excinfo.errisinstance(NotImplementedError):
        return "no_steps_bound"
    return None


def _transport_name(item: pytest.Item) -> str:
    """Best-effort transport label from the parametrized nodeid, e.g. ``mcp``."""
    nodeid = item.nodeid
    if "[" not in nodeid:
        return "default"
    param = nodeid.rsplit("[", 1)[-1].rstrip("]")
    return param.split("-", 1)[0] if param else "default"


def pytest_bdd_before_scenario(request: FixtureRequest, feature: Feature, scenario: Scenario) -> None:
    """Measure ``steps_bound`` for every step, before any step body executes.

    ``get_step_function`` is the exact lookup ``pytest_bdd._execute_scenario``
    uses to decide whether to raise ``StepDefinitionNotFoundError`` — calling
    it here only resolves the step-definition fixture (a ``StepFunctionContext``
    wrapper), it does not invoke the step's body, so this has no side effects
    on the scenario that is about to run.
    """
    tags = scenario.tags | feature.tags
    # MEMBERSHIP IS IDENTITY, NOT PROVENANCE. This used to be
    # `if _TAG not in tags: return` — so retagging a scenario @schema-v3.1
    # deleted it from the artifact entirely, and the instrument reported on a
    # population a tag edit could silently shrink. A scenario is measured if it
    # has an identity tag at all; its provenance tags are recorded as data below.
    scenario_id = _scenario_identifier(scenario, feature)
    if scenario_id is None:
        return

    record = _RECORDS.setdefault(scenario_id, ScenarioLiveness(scenario_id=scenario_id, feature=feature.rel_filename))
    # The SHARED derivation, not scenario.tags | feature.tags. Those are a STRICT
    # SUBSET of what the conftest routes on (they omit auto-applied entity
    # markers), so recording them would hand the join a narrower input than the
    # conftest used and reproduce this lane's disease one layer out.
    record.tags = sorted(t.lstrip("@") for t in tags)
    record.marker_names = sorted(derive_marker_names(request.node))
    unbound = [step.name for step in scenario.steps if get_step_function(request, step) is None]
    record.record_unbound(unbound)
    request.node.stash[_SCENARIO_ID_KEY] = scenario_id


def pytest_bdd_before_step_call(request: FixtureRequest, step: Step) -> None:
    """Record that a step body of this run is about to execute.

    pytest-bdd fires this hook immediately before calling a step function, with
    the step's arguments already resolved. Reaching it means the scenario got
    past collection, past every fixture, and into its own logic — which is the
    only thing that distinguishes a scenario that GRADED something from one that
    was xfailed out of existence in fixture setup.

    ``pytest_bdd_before_step`` (without ``_call``) would fire slightly earlier,
    before the step's own setup. This hook is the stricter of the two, and the
    stricter one is correct here: the claim being recorded is that production
    logic ran.
    """
    _STEPS_RAN.add(request.node.nodeid)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> Any:
    """Record this item's outcome against the scenario ``pytest_bdd_before_scenario`` stashed.

    Reads ``report.wasxfail`` for marker-based/explicit ``pytest.xfail()``
    reasons (pytest's own regular hookimpls populate it during ``yield``,
    before any hookwrapper's post-yield code runs, so this is reliable
    regardless of registration order relative to conftest.py's own auto-xfail
    hookwrapper). For the two exception-based auto-xfail cases
    (``StepDefinitionNotFoundError``/``NotImplementedError``), ``wasxfail`` is
    only set by conftest.py's *own* hookwrapper post-yield — ordering between
    two hookwrappers is not guaranteed, so this derives the same signal
    independently from ``call.excinfo`` rather than depending on conftest.py
    having already run.
    """
    outcome = yield
    report = outcome.get_result()
    if report.when != "call":
        return

    scenario_id = item.stash.get(_SCENARIO_ID_KEY, None)
    if scenario_id is None:
        return
    record = _RECORDS.get(scenario_id)
    if record is None:
        return

    reason = getattr(report, "wasxfail", None)
    if reason is None and call.excinfo is not None:
        if call.excinfo.errisinstance(StepDefinitionNotFoundError):
            reason = f"Step definition not found: {call.excinfo.value}"
        elif call.excinfo.errisinstance(NotImplementedError):
            reason = f"Not implemented: {call.excinfo.value}"

    # CLASSIFY ON THE TYPED CAUSE, fall back to prose only when there is no exception
    # to read (marker-based and explicit ``pytest.xfail()``). The reason string stays
    # whatever conftest wrote, because it is the HUMAN channel; it is no longer the
    # machine one.
    #
    # This module used to derive the category from that prose whenever conftest had
    # already set ``wasxfail`` -- which made a REWORDING of conftest's message able to
    # silently reclassify a scenario. It did: a dormancy message that stopped starting
    # with "Step definition not found:" fell through to "ledgered", and "ledgered"
    # sets ``harness_wired=True``, so two genuinely unbound UC-006 scenarios were
    # reported as harness-wired. Anchoring the prefix match protected against
    # over-matching but not against UNDER-matching, and under-matching is the
    # direction that inflates measured coverage.
    typed = _classify_exception(call.excinfo)
    category = typed if typed is not None else _classify_reason(reason)
    outcome_name = "passed" if report.passed else ("xfailed" if reason is not None else "failed")
    record.record_observation(
        Observation(
            transport=_transport_name(item),
            nodeid=item.nodeid,
            outcome=outcome_name,
            reason=reason,
            reason_category=category,
            steps_executed=item.nodeid in _STEPS_RAN,
        )
    )


def artifact_path() -> Path:
    override = os.environ.get(storyboard_spec.ARTIFACT_ENV_VAR)
    return Path(override) if override else _DEFAULT_ARTIFACT_PATH


def build_artifact() -> dict[str, Any]:
    return {"scenarios": [_RECORDS[k].to_dict() for k in sorted(_RECORDS)]}


def _is_xdist_worker(config: pytest.Config) -> bool:
    """True in an xdist WORKER process (the controller has no ``workeroutput``)."""
    return hasattr(config, "workeroutput")


def _merge_record(into: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    """Merge one scenario's shard into another. The rule is stated, not implied.

    ``record_observation``'s precedence is NOT associative across shards, so each
    field's combine is named explicitly:

    * ``observations``  — concatenated; every transport a shard saw is real.
    * ``steps_bound``   — AND. One shard finding an unbound step is decisive.
    * ``unbound_steps`` — union, order-stable.
    * ``ledgered``      — OR. Ledgered anywhere is ledgered.
    * ``harness_wired`` — tri-state with False DOMINANT: an observed
      not-wired beats a live/ledgered observation elsewhere, and ``None``
      (unknown) survives only if no shard ever resolved it.
    * ``tags`` / ``marker_names`` — union; they are properties of the scenario,
      identical on every shard that saw it.

    Shards cross the process boundary as DICTS (decision 4): execnet ships basic
    types only, so dataclasses cannot travel and must not be reconstructed here.
    """
    merged = dict(into)
    merged["observations"] = list(into.get("observations", [])) + list(other.get("observations", []))
    merged["steps_bound"] = bool(into.get("steps_bound", True)) and bool(other.get("steps_bound", True))
    seen_steps = list(into.get("unbound_steps", []))
    for step in other.get("unbound_steps", []):
        if step not in seen_steps:
            seen_steps.append(step)
    merged["unbound_steps"] = seen_steps
    merged["ledgered"] = bool(into.get("ledgered", False)) or bool(other.get("ledgered", False))
    wired_values = [into.get("harness_wired"), other.get("harness_wired")]
    if False in wired_values:
        merged["harness_wired"] = False
    elif True in wired_values:
        merged["harness_wired"] = True
    else:
        merged["harness_wired"] = None
    for key in ("tags", "marker_names"):
        merged[key] = sorted(set(into.get(key, [])) | set(other.get(key, [])))
    return merged


def _merge_shard(shard: list[dict[str, Any]]) -> None:
    """Fold one worker's records into the controller's ``_SHARDS``."""
    for record in shard:
        scenario_id = record["scenario_id"]
        existing = _SHARDS.get(scenario_id)
        _SHARDS[scenario_id] = record if existing is None else _merge_record(existing, record)


def pytest_testnodedown(node: Any, error: Any) -> None:
    """Collect a finished worker's records on the CONTROLLER.

    xdist calls this for every node BEFORE the controller's ``runtestloop``
    returns — i.e. before the controller's own ``pytest_sessionfinish`` — so the
    merged artifact below is complete. This is the pattern pytest-cov uses.
    """
    workeroutput = getattr(node, "workeroutput", None)
    if workeroutput and _WORKEROUTPUT_KEY in workeroutput:
        _merge_shard(workeroutput[_WORKEROUTPUT_KEY])


def _run_scope(session: pytest.Session) -> dict[str, Any]:
    """What this session actually covered, recorded beside what it observed.

    The collect-only early return above closes the case where a session observes
    NOTHING and writes ``{"scenarios": []}`` over a real artifact; the join fails
    closed on an empty file. It fails closed only on EMPTY. A NARROWED run is the
    remaining hole and it is not hypothetical: seeding the artifact with 900
    records and running ``pytest tests/bdd -k "storyboard and recancel"`` rewrote
    it to ONE record, and the 899 became ``measured_this_run=False`` -> dormant,
    with nothing in the file saying a narrower question had been asked.

    This module's docstring claims "the artifact didn't run this session must be
    visible in the file itself, not inferred from the file's absence". That was
    true for zero and false for partial. This block makes it true for both:
    ``load_artifact`` refuses an artifact whose own scope says it is not a
    measurement of the suite.
    """
    config = session.config
    return {
        "collected": session.testscollected,
        "selection": getattr(config.option, "keyword", "") or "",
        "markers": getattr(config.option, "markexpr", "") or "",
        "deselected": len(getattr(session, "deselected", ()) or ()),
        "workers": len(getattr(config, "workerinput", {})) or _worker_count(config),
        "exitstatus": int(session.exitstatus or 0),
        "testsfailed": int(session.testsfailed or 0),
    }


def _worker_count(config: pytest.Config) -> int:
    """xdist worker count as the CONTROLLER sees it, or 0 for a serial run."""
    return int(getattr(config.option, "numprocesses", 0) or 0)


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Ship this worker's shard, or (on the controller) write the merged artifact.

    Under xdist every process held its OWN ``_RECORDS`` module global and every
    process wrote the same path, so the controller wrote LAST from an EMPTY dict
    — the artifact was silently empty under ``-n auto``, which is how tox runs
    bdd. Serial runs were fine, which is why it went unnoticed.

    A worker therefore SHIPS rather than writes (decision 1: ``workeroutput``,
    the in-process channel, with no temp-file lifecycle and no orphan shards from
    a killed worker). ONLY THE CONTROLLER WRITES (decision 2) — letting a worker
    write last would produce a NONEMPTY BUT PARTIAL artifact, which passes a
    non-emptiness check while measuring one shard.
    """
    config = session.config
    if _is_xdist_worker(config):
        config.workeroutput[_WORKEROUTPUT_KEY] = [_RECORDS[k].to_dict() for k in sorted(_RECORDS)]
        return

    # A COLLECT-ONLY session observes nothing: no scenario runs, so `_RECORDS`
    # is empty by construction rather than by measurement. Writing here would
    # clobber a real artifact with `{"scenarios": []}`, and the join
    # (scripts/audit/scenario_liveness_join.load_artifact) fails CLOSED on an
    # empty file — so the next artifact regeneration silently reports liveness
    # as 0 for every check.
    #
    # This is not hypothetical. tests/unit/test_architecture_env_route_agreement.py
    # shells out to `pytest tests/bdd --collect-only` to derive its marker sets,
    # which means `make quality` destroyed the liveness artifact on every run and
    # the published "graded by a LIVE scenario" figure could not be reproduced by
    # anyone who had run the unit suite since their last BDD run.
    if getattr(config.option, "collectonly", False):
        return

    # Controller (or a serial run): merge this process's own records over any
    # shards received, then write once.
    _merge_shard([_RECORDS[k].to_dict() for k in sorted(_RECORDS)])
    path = artifact_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {"run": _run_scope(session), "scenarios": [_SHARDS[k] for k in sorted(_SHARDS)]}
    path.write_text(json.dumps(artifact, indent=2, sort_keys=False) + "\n", encoding="utf-8")
