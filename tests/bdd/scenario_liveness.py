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
  the same auto-xfail reason text conftest.py already produces verbatim (e.g.
  ``"No harness wired for {uc}"``, ``"UC-004 harness not yet wired for type:
  ..."``). ``None`` when the scenario's steps aren't bound at all (the
  question is unreached: a step that doesn't even parse never gets to the
  harness-selection branch). ``scripts/audit/scenario_liveness_join.py``
  does not trust this field — it replaces it with a
  proper data lookup against the declarative ``ENV_ROUTES`` registry (no
  reason-text matching), which is why this module's own ``harness_wired`` is
  documented as best-effort rather than promoted further here: any UC not yet
  a row in ``ENV_ROUTES`` should read as not-wired downstream, not as
  whatever this reason-text heuristic happens to guess.
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

import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from pytest_bdd.exceptions import StepDefinitionNotFoundError
from pytest_bdd.scenario import get_step_function

from scripts.audit import storyboard_spec
from scripts.ci import shard_split
from tests.helpers.ledger import load_ledger_nodeids
from tests.helpers.marker_names import derive_marker_names

if TYPE_CHECKING:
    from _pytest.fixtures import FixtureRequest
    from pytest_bdd.parser import Feature, Scenario

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "transport": self.transport,
            "nodeid": self.nodeid,
            "outcome": self.outcome,
            "reason": self.reason,
            "reason_category": self.reason_category,
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
        self.observations.append(obs)
        if obs.reason_category == "ledgered" or obs.nodeid in _LEDGERED_NODEIDS:
            self.ledgered = True
        if obs.reason_category == "harness_not_wired":
            self.harness_wired = False
        elif obs.reason_category in ("live", "ledgered") and self.harness_wired is not False:
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

#: Every xdist worker the controller saw shut down, and the subset that shipped
#: its records. A worker in the first set and not the second lost its scenarios.
_WORKERS_SEEN: set[str] = set()
_WORKERS_REPORTED: set[str] = set()


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

    category = _classify_reason(reason)
    outcome_name = "passed" if report.passed else ("xfailed" if reason is not None else "failed")
    record.record_observation(
        Observation(
            transport=_transport_name(item),
            nodeid=item.nodeid,
            outcome=outcome_name,
            reason=reason,
            reason_category=category,
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

    A worker that ships nothing is RECORDED, not skipped. This hook used to read
    ``if workeroutput and key in workeroutput`` and do nothing otherwise, so a
    worker that died took its scenarios with it and the controller published a
    file that looked whole. Nothing downstream could tell: the run scope is
    written from the controller's own session, so it describes the full target
    list either way.

    Losing records is therefore not detected here — it is made unrepresentable.
    The file names every worker that failed to report, and ``load_run`` refuses
    a file that names any.
    """
    worker_id = getattr(getattr(node, "gateway", None), "id", None) or repr(node)
    _WORKERS_SEEN.add(worker_id)

    workeroutput = getattr(node, "workeroutput", None)
    if workeroutput and _WORKEROUTPUT_KEY in workeroutput:
        _merge_shard(workeroutput[_WORKEROUTPUT_KEY])
        _WORKERS_REPORTED.add(worker_id)


IN_PROCESS_TRANSPORTS = frozenset({"mcp", "a2a", "rest"})

#: Per-session artifact files live beside the merged one. Two shards are two
#: SEPARATE pytest sessions, so both wrote the single merged path and the last
#: one won -- see `_run_scope`'s `target`.
_SESSIONS_DIRNAME = "liveness-sessions"

#: Where the per-session files live. Named by the runner so the directory is
#: PER RUN, exactly as `PYTEST_COLLECTION_MANIFEST` is: `artifact_path()` points
#: at a stable top-level file, so deriving the directory from it alone leaves
#: yesterday's sessions sitting beside today's, where they satisfy the coverage
#: check and get merged into the measurement. One resolver, used by the writer
#: and by every reader, so the two cannot disagree about which run is being read.
SESSIONS_ENV_VAR = "BDD_LIVENESS_SESSIONS"

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _relative_target(config: pytest.Config) -> list[str]:
    """The invocation paths, relative to rootdir, normalized and sorted."""
    rootdir = Path(str(config.rootdir)).resolve()
    targets = []
    for arg in config.args:
        path = Path(str(arg).split("::", 1)[0])
        try:
            targets.append(path.resolve().relative_to(rootdir).as_posix().rstrip("/") or ".")
        except ValueError:
            targets.append(str(arg))
    return sorted(set(targets))


def _write_sessions_dir() -> Path:
    """Where THIS session writes its file. Falls back, because writing is harmless."""
    override = os.environ.get(SESSIONS_ENV_VAR)
    return Path(override) if override else artifact_path().parent / _SESSIONS_DIRNAME


def sessions_dir() -> Path:
    """Where a READER finds this run's sessions. Refuses to guess.

    Deliberately asymmetric with `_write_sessions_dir` above, and the asymmetry
    is the point. A writer that falls back to the shared top-level directory
    costs nothing. A READER that falls back silently grades whatever previous
    runs left there — which is exactly how this went unnoticed once already:
    `BDD_LIVENESS_SESSIONS` was absent from docker-compose's curated env block,
    the resolver fell back, and run innet_020926_1010 read a shared directory
    while appearing to honour a per-run one. Raising makes that misconfiguration
    a failure instead of a quietly weaker measurement.

    Mirrors `tests/_collection_manifest.manifest_dir()`, which refuses the same
    way for the same reason.
    """
    override = os.environ.get(SESSIONS_ENV_VAR)
    if not override:
        raise IncompleteLivenessRun(
            f"{SESSIONS_ENV_VAR} is unset, so there is no way to tell THIS run's "
            "liveness sessions from any other run's. run_all_tests.sh and "
            "run_all_tests_host.sh export it per run; docker-compose.e2e.yml "
            "forwards it into the test container."
        )
    return Path(override)


def _session_filename(scope: dict[str, Any]) -> str:
    """A name no two concurrent sessions can share, derived from the scope."""
    key = json.dumps([scope["target"], scope["selection"], scope["markers"]], sort_keys=True)
    return f"{hashlib.sha256(key.encode('utf-8')).hexdigest()[:12]}.json"


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
        # The PATHS this session was invoked with. `selection`/`markers` below
        # cover a -k/-m narrowing; a SHARD narrows by neither -- it is handed a
        # file list. Without this a shard's artifact is indistinguishable from a
        # whole-suite one, which is exactly how the published file came to
        # describe 9 of 21 modules while claiming to measure the suite.
        "target": _relative_target(config),
        "selection": getattr(config.option, "keyword", "") or "",
        "markers": getattr(config.option, "markexpr", "") or "",
        "deselected": len(getattr(session, "deselected", ()) or ()),
        "workers": len(getattr(config, "workerinput", {})) or _worker_count(config),
        # Which workers shut down without shipping records. Empty on a healthy
        # run and on a serial one. A reader refuses a file where this is not
        # empty, so a lost shard cannot be read as a complete measurement.
        "workers_missing": sorted(_WORKERS_SEEN - _WORKERS_REPORTED),
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
    # This is not hypothetical. tests/collection/test_architecture_env_route_agreement.py
    # shells out to `pytest tests/bdd --collect-only` to derive its marker sets,
    # which means `make quality` destroyed the liveness artifact on every run and
    # the published "graded by a LIVE scenario" figure could not be reproduced by
    # anyone who had run the unit suite since their last BDD run.
    if getattr(config.option, "collectonly", False):
        return

    # Controller (or a serial run): merge this process's own records over any
    # shards received, then write once.
    _merge_shard([_RECORDS[k].to_dict() for k in sorted(_RECORDS)])
    artifact = {"run": _run_scope(session), "scenarios": [_SHARDS[k] for k in sorted(_SHARDS)]}
    body = json.dumps(artifact, indent=2, sort_keys=False) + "\n"

    # ALSO write a per-session copy, so a second session cannot silently replace
    # this measurement with its own. The merged path is kept as-is because other
    # consumers read it by name; the directory is what `load_run` folds.
    sessions = _write_sessions_dir()
    _write(artifact_path(), body)
    _write(sessions / _session_filename(artifact["run"]), body)


def _write(path: Path, body: str) -> None:
    """Write one artifact file, reporting a write failure instead of raising.

    A diagnostic never changes the outcome of the thing it measures. Both the
    mkdir and the write raise on an unwritable parent, on a path occupied by a
    regular file, and on a full disk; an exception out of `pytest_sessionfinish`
    is an unhandled error, so a suite whose scenarios all passed exits nonzero
    with no summary line. This plugin is registered for EVERY bdd session
    (tests/bdd/conftest.py), which makes that the ordinary case rather than a
    corner.

    Losing the artifact is not silent, and that is why swallowing the write is
    safe here: every consumer fails CLOSED on a missing or empty artifact --
    `scripts/audit/scenario_liveness_join.load_artifact` refuses an empty file,
    and `load_run` raises `IncompleteLivenessRun` when the session files do not
    add up to the suite. The absence is detected where it is read, so it does
    not also need to fail the run that produced it.

    OSError, not Exception: PermissionError, FileExistsError, IsADirectoryError
    and ENOSPC are all subclasses of it, while a defect in the artifact dict
    stays loud instead of being hidden by the guard that tolerates a read-only
    disk.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    except OSError as exc:
        print(f"[scenario-liveness] could not write the artifact to {path}: {exc}", file=sys.stderr)


class IncompleteLivenessRun(RuntimeError):
    """The session files on disk do not add up to a measurement of the suite.

    Raised rather than returning what was found. A consumer handed a partial
    artifact asks its question of a partial population and passes, which is the
    failure this whole artifact exists to prevent -- and is what the single
    fixed write path produced for as long as the suite was sharded.
    """


def _project_in_process(record: dict[str, Any]) -> dict[str, Any]:
    """One scenario, as the in-process transports alone measured it.

    The record-level fields are DERIVED from observations, so filtering the
    observations without re-deriving them leaves a record whose summary
    describes a population its own observation list no longer contains. That is
    not hypothetical: `ledgered` is set from an e2e_rest nodeid, so the three
    uc005 scenarios ledgered only on e2e_rest carry `ledgered=True` while every
    in-process run of them passes.

    Re-derivation replays through `ScenarioLiveness.record_observation`, the
    single owner of that rule, rather than restating it here.
    `steps_bound`/`unbound_steps` are carried through instead: they come from
    `record_unbound`, never from an observation, and an unbound step is unbound
    on every transport.
    """
    replay = ScenarioLiveness(scenario_id=record["scenario_id"], feature=record.get("feature", ""))
    kept = [o for o in record["observations"] if o.get("transport") in IN_PROCESS_TRANSPORTS]
    for obs in kept:
        replay.record_observation(
            Observation(
                nodeid=obs["nodeid"],
                transport=obs["transport"],
                outcome=obs["outcome"],
                reason=obs.get("reason", ""),
                reason_category=obs["reason_category"],
            )
        )
    projected = dict(record)
    projected["observations"] = kept
    projected["ledgered"] = replay.ledgered
    projected["harness_wired"] = replay.harness_wired
    return projected


def load_run(directory: str | Path) -> dict[str, Any]:
    """Every scenario the real run measured, merged across sessions.

    Folds the per-session files with the same `_merge_record` the controller
    already uses for workers, then projects each scenario onto the in-process
    transports.

    A FILTERED session is skipped rather than refused: on the fast path
    `[testenv:bdd_e2e]` always writes one (`-k e2e_rest`) beside the shards, so
    refusing on its presence would red every such run. Skipping it and letting
    the coverage check below decide is the single-valued rule. The exclusion is
    still load-bearing -- bdd_e2e's TARGET is the whole tree, so a path-only
    check would accept it, and its in-process projection is empty for every
    scenario at once.
    """
    directory = Path(directory)
    merged: dict[str, dict[str, Any]] = {}
    covered: set[str] = set()
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        scope = payload.get("run", {})
        if scope.get("selection") or scope.get("markers"):
            continue
        missing = scope.get("workers_missing") or []
        if missing:
            raise IncompleteLivenessRun(
                f"{path.name} was written by a session whose workers {missing} shut down "
                "without shipping their records, so the scenarios they ran are absent from "
                "it. The file is a partial measurement wearing a whole run's target list."
            )
        covered.update(scope.get("target", []))
        for record in payload.get("scenarios", []):
            existing = merged.get(record["scenario_id"])
            merged[record["scenario_id"]] = record if existing is None else _merge_record(existing, record)

    expected = {Path(f).as_posix() for f in shard_split.list_suite_files("bdd", repo_root=_REPO_ROOT)}
    if not _covers(covered, expected):
        raise IncompleteLivenessRun(
            f"the liveness sessions in {directory} cover {sorted(covered) or 'nothing'}, "
            f"which does not account for the {len(expected)} BDD modules. A partial "
            "measurement cannot be graded as the suite."
        )
    return {"scenarios": {k: _project_in_process(v) for k, v in merged.items()}}


def _covers(targets: set[str], expected: set[str]) -> bool:
    """True when the union of session targets accounts for every BDD module.

    Two shapes reach here. A session invoked on the DIRECTORY (`pytest tests/bdd`)
    names one target that is a prefix of every module. Sharded sessions each name
    their own files, and must union to the whole set.
    """
    if not targets:
        return False
    return all(any(module == t or module.startswith(f"{t}/") for t in targets) for module in expected)
