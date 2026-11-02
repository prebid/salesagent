"""Record the request payload every graded BDD dispatch actually SENT.

WHY OUTCOMES ARE NOT ENOUGH. ``scripts/audit/compare_runs.py`` diffs per-nodeid
outcomes and refuses to summarise, which is the right outer check and is not
sufficient on its own: a migration that NORMALIZES a seeded payload -- repairing
an invalid literal into a valid one, or flipping which production branch runs --
moves no outcome at all. The scenario stays green and grades a different request
than it used to. This module makes the request itself a measured artifact, so
that repair is a diff instead of a silence.

THE THREE-VALUED CONTRACT, and the one it exists to prevent. Every nodeid the
run executed gets a row, INCLUDING the 66% that dispatch nothing (5421 of 8182
measured, salesagent-ryzil.2) -- because "ran and dispatched nothing" and "did
not run" must not be the same artifact. The comparator's verdicts are SAME /
CHANGED / NOT_MEASURED, and a nodeid the baseline recorded that this run did not
FAILS. A missing measurement reading as "no change" is exactly the defect
salesagent-b341x.18 removed from the census (commit 75f4317f6); reintroducing it
inside the gate built to catch silent repairs would be indefensible.

SERIALIZATION MUST REFUSE, NEVER DROP. ``tests/harness/_base.json_safe`` is
reused -- it already converts models, enums, datetimes and Decimals exactly the
way a wire body does -- but it is NOT TOTAL: 33 of 2905 real payloads (1.14%)
hold a value ``json.dump`` cannot write (30 pydantic ``AnyUrl``, 9 bare
``object()`` OMIT sentinels). The scan's own first full run lost 4 of 8 worker
shards to a silently truncated artifact because of it. So every payload is
round-tripped through ``json`` AT CAPTURE TIME with a capture-local ``default=``
that maps the two known classes and RAISES on anything else, loudly, at the
dispatch that produced it. A half-written capture that reads as a complete
"before" is this gate's worst possible failure.

``identity`` IS TOKENIZED, NOT RECORDED. It rides in the dispatched kwargs bag on
84 dispatches but is not a request field -- ``_deliver_via_client`` pops it before
the wire. Recording its contents would (i) write auth tokens into an artifact and
(ii) mark every such row CHANGED forever, since the tenant, principal and token
are all factory-minted. What a migration could actually change here is whether a
scenario dispatches WITHOUT auth, so that is what is kept: absent / none /
supplied. See the ``_IDENTITY_*`` tokens below.

Structure is modelled on ``tests/bdd/scenario_liveness.py``, which already solved
xdist shipping (workers ship on ``config.workeroutput``, the controller merges in
``pytest_testnodedown``), controller-only writing, the collect-only guard and the
run-scope block. Each of those was re-read against that file rather than assumed.

Writes ``$BDD_PAYLOAD_ARTIFACT`` (tox sets it per BDD env, under ``{toxworkdir}``;
``run_all_tests.sh`` copies it into ``$RESULTS_DIR/payloads/``). It must NOT land
at the ``test-results/`` root the way ``bdd_scenario_liveness.json`` does: that
file is overwritten every run, which would destroy the "before" this gate needs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from tests._collection_manifest import write_artifact
from tests.factories.mint import begin_test, install_declaration_recording, minted_forms
from tests.harness._base import json_safe

#: Path override. Set by tox per BDD env; the default is a last resort for an
#: ad-hoc local run, deliberately named so ``compare_runs.py``'s ``*.json`` glob
#: over a results directory can never pick a payload artifact up as a pytest report.
ARTIFACT_ENV_VAR = "BDD_PAYLOAD_ARTIFACT"
_DEFAULT_ARTIFACT_PATH = Path("test-results") / "bdd_payloads.json"

_WORKEROUTPUT_KEY = "bdd_payload_capture"

#: What the ``identity`` kwarg becomes. Three states, because the fact a migration
#: could change is whether the dispatch carried auth at all.
_IDENTITY_NONE = "<identity:none>"
_IDENTITY_SUPPLIED = "<identity:supplied>"

#: What a generated value becomes: a token that is STABLE across runs but still
#: DISTINGUISHES two different generated values within one nodeid, so a broken
#: cross-reference (two fields that named the same id now naming different ones)
#: is still a diff. Indexes are assigned by first occurrence in dispatch order,
#: which is deterministic for a nodeid because its steps are.
_MINTED_TOKEN = "<minted:{index}>"

#: Sentinels meaning "this key is absent on the wire" -- the single most important
#: fact this gate watches. Interned BY IDENTITY: they are bare ``object()``s, and
#: ``repr()`` carries a ``0x`` address that changes every run, which would make
#: every such row permanently CHANGED.
_SENTINEL_TOKENS: dict[int, str] = {}
_SENTINELS_LOADED = False

_PENDING: list[Any] = []
_RECORDS: dict[str, list[Any]] = {}
_SHARDS: dict[str, list[Any]] = {}

#: Whether THIS process attributes dispatches to nodeids. True in a serial run and in
#: every xdist worker; False on the xdist controller, which sees every worker's reports
#: and none of their dispatches. Set in ``pytest_configure``; defaults to True so a
#: direct unit test of the hooks records without a session around it.
_RECORD_LOCALLY = True


def _load_sentinels() -> None:
    """Resolve the OMIT sentinels to their identities, once, on first need.

    Lazily rather than at import: this plugin is loaded by ``tests/bdd/conftest.py``
    and the harness modules that own these objects are heavy. Resolving on the first
    unserializable value keeps plugin import cheap and still fires before any
    artifact is written.
    """
    global _SENTINELS_LOADED
    if _SENTINELS_LOADED:
        return
    _SENTINELS_LOADED = True
    from tests.factories.request import OMIT
    from tests.harness.media_buy_create import OMIT_ACCOUNT, OMIT_IDEMPOTENCY_KEY

    _SENTINEL_TOKENS[id(OMIT)] = "<omit>"
    _SENTINEL_TOKENS[id(OMIT_ACCOUNT)] = "<omit:account>"
    _SENTINEL_TOKENS[id(OMIT_IDEMPOTENCY_KEY)] = "<omit:idempotency_key>"


def _capture_default(value: Any) -> Any:
    """``json.dumps`` fallback: map what we know, REFUSE what we do not.

    The refusal is the point. A serializer that silently drops or ``repr()``s an
    unknown object produces an artifact that parses, looks complete, and is
    missing exactly the field the migration touched.
    """
    from pydantic import AnyUrl

    _load_sentinels()
    token = _SENTINEL_TOKENS.get(id(value))
    if token is not None:
        return token
    if isinstance(value, AnyUrl):
        # ``_call_via``'s MCP branch used to dump mode=PYTHON, so an AnyUrl survived
        # into the bag on 30 payloads while a2a/rest carried the same field as a
        # string. Both spell the same URL; ``str`` is the wire's spelling.
        return str(value)
    raise TypeError(
        f"payload_capture cannot serialize {type(value).__module__}.{type(value).__name__}: "
        f"{value!r:.200}. It is dispatched as part of a request payload, so the capture must "
        "record it faithfully or refuse. Add a rule to _capture_default (map a NEW sentinel by "
        "IDENTITY, never by repr — the 0x address changes every run), or convert the value at "
        "the step that builds it. Dropping it would leave a complete-looking artifact with a "
        "missing field."
    )


def capture_payload(payload: Any) -> Any:
    """The JSON form of one dispatched payload, or a raised TypeError.

    ``json_safe`` first (it already speaks the wire's conversions), then a full
    ``json`` round trip so that unserializable residue fails HERE, at the dispatch
    that produced it, rather than at session end when 8 worker shards are being
    written at once and a truncated file is the only symptom.
    """
    return json.loads(json.dumps(json_safe(payload), default=_capture_default, sort_keys=True))


def _tokenize_identity(payload: Any) -> Any:
    """Replace a top-level ``identity`` value with its three-state token."""
    if not isinstance(payload, dict) or "identity" not in payload:
        return payload
    tokenized = dict(payload)
    tokenized["identity"] = _IDENTITY_NONE if payload["identity"] is None else _IDENTITY_SUPPLIED
    return tokenized


def record_dispatched_request(payload: Any) -> None:
    """Record ONE dispatched request payload against the test now running.

    Called from the dispatch seams themselves (``tests/bdd/steps/generic/_dispatch.py``)
    rather than from a plugin that patches them: the 30 BDD step modules are loaded
    through ``conftest.py``'s ``pytest_plugins`` list and bind the dispatch helpers BY
    VALUE before any ``-p`` plugin's configure hook runs, so a patch-based recorder
    measures a fraction of the traffic and says nothing about the rest (measured: 771
    of 2509 events -- salesagent-ryzil.2).

    A step function has no ``item``, so records land in ``_PENDING`` and are attributed
    at ``pytest_runtest_logfinish``, which is ``scenario_liveness``'s two-phase pattern.
    """
    _PENDING.append(capture_payload(_tokenize_identity(payload)))


def _intern_minted(dispatches: list[Any]) -> list[Any]:
    """Replace values the FACTORIES minted with stable, equality-preserving tokens.

    Membership comes from the mint record (``tests/factories/mint.py``), never from a
    regex or a field name: the same field carries a pinned literal and a generated
    value on this tree, in both directions, so no rule over the value can classify
    both correctly. A value nobody minted is by definition not run-variant and is
    diffed verbatim.
    """
    minted = minted_forms()
    if not minted:
        return dispatches
    index: dict[str, str] = {}

    def walk(value: Any) -> Any:
        if isinstance(value, str):
            if value not in minted:
                return value
            token = index.get(value)
            if token is None:
                token = _MINTED_TOKEN.format(index=len(index))
                index[value] = token
            return token
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value

    return [walk(d) for d in dispatches]


# ---------------------------------------------------------------------------
# pytest hooks
# ---------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    """Start recording factory declarations, and decide whether THIS process records.

    ``install_declaration_recording`` is called here, not at import:
    ``BaseDeclaration.evaluate_pre`` is a CLASS attribute resolved at call time, so a
    late install still reaches every factory already imported, and confining the
    wrapper to sessions that load this plugin keeps the rest of the suite unwrapped.

    ``_RECORD_LOCALLY`` is the fix for a defect this gate's own first full run
    produced, which is the best possible argument for the gate: the artifact came back
    with all 8182 nodeid rows present and ZERO dispatches on any of them. ``logstart``
    and ``logfinish`` fire on the xdist CONTROLLER too — xdist forwards every worker's
    report through them — so the controller built a complete set of EMPTY rows and, at
    session end, wrote them over the rows the workers had shipped. A file that says
    "8182 tests ran and none of them dispatched anything" parses, looks complete, and
    is a lie. The controller now records nothing and only merges.
    """
    del config
    install_declaration_recording()


def pytest_sessionstart(session: pytest.Session) -> None:
    """Decide whether THIS process records, once xdist has finished registering.

    NOT in ``pytest_configure``, and this was measured rather than reasoned: xdist
    registers its ``dsession`` plugin from its own ``pytest_configure``, and pluggy
    calls hookimpls in reverse registration order, so a conftest-loaded plugin's
    ``pytest_configure`` runs FIRST and sees no ``dsession`` yet. Deciding there made
    the controller believe it was a serial run, and a second full-scale run came back
    with the same all-empty artifact as the first. ``sessionstart`` runs after every
    ``configure``, so the question has an answer by the time it is asked.
    """
    global _RECORD_LOCALLY
    _RECORD_LOCALLY = not _is_xdist_controller(session.config)


def pytest_runtest_logstart(nodeid: str, location: Any) -> None:
    """Reset the per-test mint record and drop anything left pending.

    ``logstart`` fires BEFORE the item's setup, so a value minted by a Given step or
    by a function-scoped fixture belongs to this test. Pending records that survived
    the previous test's ``logfinish`` came from outside any test and are dropped
    rather than misattributed.
    """
    del nodeid, location
    if not _RECORD_LOCALLY:
        return
    _PENDING.clear()
    begin_test()


def pytest_runtest_logfinish(nodeid: str, location: Any) -> None:
    """Attribute this test's dispatches to its nodeid -- EMPTY LIST INCLUDED.

    The empty row is not bookkeeping: without it, "this scenario ran and dispatched
    nothing" is indistinguishable from "this scenario did not run", and the whole
    NOT_MEASURED contract collapses into silence.

    Skipped on the xdist controller, where this hook fires for a test that ran in
    ANOTHER process and would record an empty row for it -- see
    :func:`pytest_configure`. A worker that dies therefore leaves its nodeids ABSENT,
    which the comparator reads as NOT_MEASURED and fails on. That is the intended
    reading: a dead worker measured nothing, and must not be able to say it did.
    """
    del location
    if not _RECORD_LOCALLY:
        return
    _RECORDS[nodeid] = _intern_minted(list(_PENDING))
    _PENDING.clear()


def pytest_testnodedown(node: Any, error: Any) -> None:
    """Collect a finished xdist worker's rows on the CONTROLLER.

    xdist calls this for every node before the controller's ``runtestloop`` returns,
    i.e. before the controller's own ``pytest_sessionfinish`` -- the same ordering
    ``scenario_liveness`` and pytest-cov rely on.
    """
    del error
    workeroutput = getattr(node, "workeroutput", None)
    if workeroutput and _WORKEROUTPUT_KEY in workeroutput:
        _SHARDS.update(workeroutput[_WORKEROUTPUT_KEY])


def _run_scope(session: pytest.Session) -> dict[str, Any]:
    """What this session actually covered, recorded beside what it observed.

    Copied in intent from ``scenario_liveness._run_scope``: the comparator refuses an
    artifact whose own scope says it is not a measurement of the suite, rather than
    inferring narrowness from a short file.
    """
    config = session.config
    return {
        "collected": int(session.testscollected or 0),
        "selection": getattr(config.option, "keyword", "") or "",
        "markers": getattr(config.option, "markexpr", "") or "",
        "deselected": len(getattr(session, "deselected", ()) or ()),
        "workers": len(getattr(config, "workerinput", {})) or int(getattr(config.option, "numprocesses", 0) or 0),
        "exitstatus": int(session.exitstatus or 0),
        "testsfailed": int(session.testsfailed or 0),
    }


def artifact_path() -> Path:
    override = os.environ.get(ARTIFACT_ENV_VAR)
    return Path(override) if override else _DEFAULT_ARTIFACT_PATH


def _is_xdist_worker(config: pytest.Config) -> bool:
    return hasattr(config, "workeroutput")


def _is_xdist_controller(config: pytest.Config) -> bool:
    """True in the process that DISTRIBUTES tests rather than running them.

    Asked of the ``dsession`` plugin xdist registers only when it is actually
    distributing (``-n0`` runs in-process and registers none), not of ``numprocesses``,
    which is set to ``"auto"`` before it is resolved.
    """
    return not _is_xdist_worker(config) and config.pluginmanager.getplugin("dsession") is not None


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Ship this worker's rows, or (on the controller) write the merged artifact."""
    config = session.config
    if _is_xdist_worker(config):
        config.workeroutput[_WORKEROUTPUT_KEY] = _RECORDS
        return

    # A collect-only session observes nothing by construction rather than by
    # measurement; writing would replace a real artifact with an empty one, and the
    # comparator fails CLOSED on empty. ``make quality`` shells out to
    # ``pytest tests/bdd --collect-only``, so this is not hypothetical.
    if getattr(config.option, "collectonly", False):
        return

    _SHARDS.update(_RECORDS)
    artifact = {"run": _run_scope(session), "nodes": {k: _SHARDS[k] for k in sorted(_SHARDS)}}
    write_artifact(artifact_path(), json.dumps(artifact, indent=2, sort_keys=False) + "\n")
