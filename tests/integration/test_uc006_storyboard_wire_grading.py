"""RED grader: UC-006 storyboard Thens must grade the WIRE.

Core Invariant under grade (lane plan, verbatim): *every storyboard ``Then``
asserts a transport-observable signal, on every transport, through the guarded
accessors (``wire_dict``/``wire_field``/``assert_wire_error``); setup goes
through the env's per-transport primitive, never a direct ``_impl`` call.*

**Why this module exists rather than the liveness artifact.** The wire-grading §5
declares two graders: the per-scenario ledgered/live partition pinned in
``tests/collection/test_bdd_scenario_liveness_real_run.py`` — which
solution-review pass 3 (P4) defers, and which the wire-grading work **must not
modify** — and the REVERSION TEST, which pass-3 finding (d) requires to be *an
EXECUTED step in the grader, not a prose procedure*. This module is that
executed grader, plus the per-item mutations for C3 and C4 that the partition
artifact cannot see (the artifact records pass/xfail; it cannot tell a Then that
read the wire from one that re-serialized an in-memory object and got lucky).

**Why the Thens are driven directly instead of through pytest-bdd.** Finding (d):
the reversion "must run against a scenario P1 marks EXPECTED-LIVE, because an
xfail-ledgered scenario would SWALLOW the loud failure". Driving the step
functions against a ctx built from the LIVE scenario's own Given/When
(``T-UC-006-storyboard-format-id-roundtrip-on-sync``) satisfies that and, in
addition, puts the assertions out of reach of *any* ledger route — the conftest
tag sets and ``tests/bdd/e2e_rest_known_failures.txt`` both key on scenario
identity, and no scenario is being collected here. Same reasoning as
``tests/unit/test_bdd_uc006_storyboard_dispatch_fault_is_not_xfail.py``, which
drives the same module's Thens against an injected fault.

**Measured pre-Lane-C state** (this box, ``CreativeSyncEnv``, storyboard payload):

    transport   TransportResult.wire_response
    mcp         {'dry_run', 'creatives', 'status'}   real wire
    rest        {'dry_run', 'creatives', 'status'}   real wire
    a2a         None                                 <- the gap C1 closes

``CreativeSyncEnv``'s A2A leg calls ``sync_creatives_raw`` directly (its own
docstring says why), so it never routes through ``_run_a2a_handler`` and never
stashes a wire. ``then_response_envelope_schema_valid`` therefore falls back to
``resp.model_dump(mode="json")`` — a re-serialization of the in-process object,
which cannot catch an A2A framing regression because no A2A framing was
exercised.

**CB1 compatibility.** Every mutation below leaves the dispatch's TYPED PAYLOAD
populated, so a C2 implementation that keeps a payload guard (today's
``_require_response(ctx, ...)``) ahead of the wire read — which pass-3 CB1 makes
BINDING — passes these graders unchanged. Nothing here requires or rewards
deleting that guard.

**Where the payload lives.** The dispatch seams no longer stash a detached
``ctx["response"]`` copy: a provenance-stripped payload cannot tell a Then
whether it is reading a wire fact or an in-process reconstruction, so the
payload is read off the dispatch's own ``TransportResult`` through the guarded
accessors in ``tests/bdd/steps/_outcome_helpers.py`` (``payload_or_none``), and
the mutations below are applied to that result rather than to a ctx key. This
grader must not reintroduce ``ctx["response"]`` — the ambiguity it removes is
the same one C2 exists to grade — and ``tests/unit/test_architecture_bdd_wire_discipline.py``
pins the rule for the step modules these tests drive.

**Out of scope, deliberately.** C6 (the phantom-transport assertion) is graded by
the transport-set assertion already living in the Lane-D-owned liveness block;
this module does not restate it and does not touch that file. C5 (context echo
compared against the captured wire) is a test-side assertion-source obligation on
``CapabilitiesEnv``, graded structurally by
``tests/unit/test_architecture_context_echo_wire_grading.py`` — a behavioral
byte-for-byte echo test would redden for a PRODUCTION normalization defect
(measured: ``context={"trace_id": "t1", "channel": None}`` comes back
``{"trace_id": "t1"}`` on the mcp and a2a wire alike), which wire grading does not own.
"""

from __future__ import annotations

import copy
import dataclasses
from dataclasses import replace
from typing import Any

import pytest

from tests.bdd.steps._outcome_helpers import payload_or_none
from tests.bdd.steps.domain import uc006_storyboard_creative_sync as steps
from tests.bdd.steps.generic._dispatch import _populate_ctx_from_result
from tests.harness.transport import (
    DERIVED_STATUS_ADCP_ERROR,
    DERIVED_STATUS_TRANSPORT_FAULT,
    Transport,
    TransportResult,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

#: The three in-process wire transports the storyboard slice parametrizes over.
#: IMPL is deliberately absent — it has no wire by definition (tests/CLAUDE.md
#: "TransportResult.wire_response"), so it cannot grade this invariant.
WIRE_TRANSPORTS = (Transport.MCP, Transport.A2A, Transport.REST)

#: The scenario whose Given/When builds every ctx below. P1 (solution review
#: pass 2) marks this the one EXPECTED-LIVE member of the UC-006 storyboard
#: partition; the other five are ledgered against named production defects and
#: would swallow a loud failure (finding (d)).
LIVE_SCENARIO_TAG = "T-UC-006-storyboard-format-id-roundtrip-on-sync"


class _NeverSerialized:
    """Stand-in for the dispatch's typed payload whose re-serialization is a loud failure.

    The payload guard (``_require_response`` -> ``payload_or_none``) only checks
    that a payload is not None, so this object satisfies the CB1-mandated guard
    while making any *use* of the in-memory object as an assertion SOURCE
    observable: ``model_dump`` records the call and returns a payload that is not
    schema-valid, so a Then that still re-serializes both trips the recorder and
    fails validation. ``wire_dict``'s no-wire fallback is exactly
    ``require_payload(ctx).model_dump(mode="json")``, so installing this as
    ``TransportResult.payload`` is what makes that fallback observable.
    """

    def __init__(self) -> None:
        self.model_dump_calls: list[dict[str, Any]] = []

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        self.model_dump_calls.append(kwargs)
        return {"not": "a sync-creatives response envelope"}


def _live_scenario_ctx(env: Any, transport: Transport) -> dict[str, Any]:
    """Run the LIVE storyboard scenario's own Given + When and return its ctx.

    Uses the production step functions verbatim — no re-derived payload — so a
    change to the scenario's setup moves this grader with it instead of leaving
    it grading a stale payload.

    Returns a ctx guaranteed to hold the dispatch's ``TransportResult`` carrying
    a typed payload — the two things every mutation below acts on. The payload is
    read through ``payload_or_none``, the same accessor the step definitions use,
    so this grader cannot go on measuring a ctx key the dispatch seams have
    stopped writing (which is precisely how a detached ``ctx["response"]`` copy
    could look green while grading nothing).
    """
    ctx: dict[str, Any] = {"env": env, "transport": transport}
    steps.given_captured_format_id_from_get_products_for_sync(ctx)
    steps.when_sync_creative_with_captured_format_id(ctx)
    assert ctx.get("error") is None, (
        f"{transport.value}: the LIVE storyboard scenario's When dispatch errored — "
        f"this grader cannot measure wire discipline against a failed dispatch: {ctx['error']!r}"
    )
    assert isinstance(ctx.get("result"), TransportResult), (
        f"{transport.value}: the When step stashed no TransportResult, so there is no "
        f"dispatch provenance to grade (ctx keys: {sorted(ctx)})"
    )
    assert payload_or_none(ctx) is not None, f"{transport.value}: dispatch produced neither response nor error"
    return ctx


def _sync_env(name: str) -> Any:
    from tests.harness.creative_sync import CreativeSyncEnv

    return CreativeSyncEnv(tenant_id=name, principal_id="wire_grader_principal")


# ═══════════════════════════════════════════════════════════════════════
# C1 — the A2A leg must capture a real success-path wire
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("transport", WIRE_TRANSPORTS, ids=lambda t: t.value)
def test_storyboard_dispatch_captures_a_real_success_path_wire(integration_db, transport: Transport) -> None:
    """Every wire transport must stash ``TransportResult.wire_response`` for sync_creatives.

    This is C1's grader. ``wire_response`` is populated on A2A *only* when the env
    routes through ``_run_a2a_handler`` (tests/CLAUDE.md, "Authenticity per
    transport") — the raw-wrapper bypass produces ``None``, which is exactly the
    condition ``wire_dict``/``wire_field`` raise on. Asserting the top-level
    envelope keys rather than mere non-None keeps a future empty-dict stash from
    satisfying this.
    """
    with _sync_env(f"wire-c1-{transport.value}") as env:
        ctx = _live_scenario_ctx(env, transport)
        wire = ctx.get("wire_response")

    assert isinstance(wire, dict), (
        f"{transport.value}: no success-path wire captured for sync_creatives "
        f"(ctx['wire_response']={wire!r}). The storyboard Then steps cannot assert a "
        "transport-observable signal on a transport that stashes no wire."
    )
    assert "creatives" in wire, (
        f"{transport.value}: captured wire has no top-level 'creatives' key — "
        f"got keys {sorted(wire)}; this is not a sync-creatives response envelope."
    )


# ═══════════════════════════════════════════════════════════════════════
# C2 — the envelope-schema Then reads the wire, never the in-memory object
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("transport", WIRE_TRANSPORTS, ids=lambda t: t.value)
def test_envelope_schema_then_validates_the_wire_not_the_in_memory_object(integration_db, transport: Transport) -> None:
    """``then_response_envelope_schema_valid`` must validate ``wire_dict(ctx)``.

    The mutation: keep the real captured wire, but replace the dispatch's TYPED
    PAYLOAD with an object whose ``model_dump`` is a tripwire returning a payload
    that is NOT schema-valid. A Then that reads the wire passes and never calls
    it; the ``model_dump`` fallback both trips the recorder and fails validation.

    The tripwire is installed on the ``TransportResult`` rather than under a
    detached ``ctx["response"]`` key, because that is where the accessors read
    the payload from: ``wire_dict``'s fallback is
    ``require_payload(ctx).model_dump(mode="json")``, so a ctx key nothing reads
    would make this mutation inert and the grader vacuous.

    The payload stays non-None on purpose so a CB1-compliant implementation (a
    payload guard retained ahead of the wire read) is graded identically to one
    without it — this grader pins the assertion SOURCE, not the guard order.
    """
    with _sync_env(f"wire-c2-{transport.value}") as env:
        ctx = _live_scenario_ctx(env, transport)
        tripwire = _NeverSerialized()
        ctx["result"] = dataclasses.replace(ctx["result"], payload=tripwire)

        steps.then_response_envelope_schema_valid(ctx)

    assert tripwire.model_dump_calls == [], (
        f"{transport.value}: then_response_envelope_schema_valid re-serialized the in-memory "
        f"response (model_dump called with {tripwire.model_dump_calls}) instead of validating the "
        "captured wire. A schema check against a re-serialization of the in-process object cannot "
        "catch a transport-framing regression — it exercises the serializer twice."
    )


def test_reverting_the_a2a_wire_capture_makes_the_envelope_schema_then_fail_loudly(integration_db) -> None:
    """THE REVERSION TEST (lane §5): un-stash the A2A wire; the Then must fail LOUDLY.

    Executed, not prose (pass-3 finding (d)). The reversion is applied at the
    single observable the A2A wire capture produces — ``_run_a2a_handler``'s
    ``env._last_wire_response`` stash — rather than by name-patching the env's A2A
    primitive, because that primitive is renamed in the commit immediately
    before this lane. Clearing the stash reproduces exactly the pre-C1 observable
    that the ``sync_creatives_raw`` bypass produces: a real typed response, and no
    wire (measured above: ``wire_response=None`` on a2a today, a real dict on
    mcp/rest).

    Run on the EXPECTED-LIVE scenario (finding (d)) and driven directly, so no
    ledger tag can absorb the failure.

    Direction: with no wire, ``wire_dict``'s guard (``_outcome_helpers.py:53-56``)
    must raise. Silence here means the ``model_dump`` fallback is still in place
    and the A2A leg's wire capture is decorative — the precise failure mode C2
    exists to remove.
    """
    with _sync_env("wire-c2-revert-a2a") as env:
        ctx = _live_scenario_ctx(env, Transport.A2A)
        # THE REVERSION: the A2A leg stashed no success-path wire.
        #
        # Applied at BOTH observables, and that is not belt-and-braces. The
        # guarded read moved onto ``TransportResult.require_wire()``, and
        # ``wire_dict`` prefers it over ``ctx["wire_response"]`` whenever a
        # result is present — which it always is for a real-wire transport.
        # Clearing only the ctx key therefore reverts nothing: the accessor
        # still reads a real wire off the result, the Then passes, and this
        # test grades nothing. (Caught exactly that way when main's
        # guarded-accessor refactor merged into this branch: auto-merged with
        # no conflict, and only this test failed.)
        ctx["wire_response"] = None
        ctx["result"] = dataclasses.replace(ctx["result"], wire_response=None)
        assert payload_or_none(ctx) is not None, "reversion must leave the typed response intact"

        with pytest.raises(AssertionError) as excinfo:
            steps.then_response_envelope_schema_valid(ctx)

    message = str(excinfo.value)
    assert "no wire body was stashed" in message or "wire_response missing" in message, (
        "the envelope-schema Then failed, but not through a missing-wire guard "
        f"(require_wire's, or wire_dict's own when no result is present): {excinfo.value!r}"
    )


# ═══════════════════════════════════════════════════════════════════════
# C3 — the format_id roundtrip reads the creative back ON THE WIRE
# ═══════════════════════════════════════════════════════════════════════


def _spy_on_client_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every tool name dispatched through ``AdCPTestClient.call``.

    ``AdCPTestClient.call`` is the seam pass-3 CB3 binds C3 to — either via a
    ctx-seeded client (mirroring ``tests/bdd/conftest.py:3123``) or via
    ``AdCPTestClient(ctx["env"])`` built at the step. Both route through this one
    method, so spying here does not presume which of the two C3 picks.
    """
    from tests.harness.client import AdCPTestClient

    seen: list[str] = []
    original = AdCPTestClient.call

    def _spy(self: Any, tool: str, payload: dict[str, Any], transport: Transport, **kwargs: Any) -> TransportResult:
        seen.append(tool)
        return original(self, tool, payload, transport, **kwargs)

    monkeypatch.setattr(AdCPTestClient, "call", _spy)
    return seen


@pytest.mark.parametrize("transport", WIRE_TRANSPORTS, ids=lambda t: t.value)
def test_format_id_roundtrip_then_reads_the_creative_back_over_the_wire(
    integration_db, monkeypatch: pytest.MonkeyPatch, transport: Transport
) -> None:
    """``then_format_id_roundtrips_verbatim`` must read the persisted creative on the wire.

    C3: the ``CreativeRepository`` read stays only as a redundant in-process check;
    the PRIMARY assertion is a ``list_creatives`` dispatch plus a wire read.
    ``CreativeSyncEnv`` exposes no ``list_creatives`` primitive (its MCP_TOOL /
    REST_ENDPOINT are sync-only), but ``AdCPTestClient.call`` resolves any tool off
    the address table on the SAME env — measured working on all three transports
    against this env (pass-2 finding (c) names it; CB3 makes the wiring explicit).
    """
    seen = _spy_on_client_calls(monkeypatch)

    with _sync_env(f"wire-c3-{transport.value}") as env:
        ctx = _live_scenario_ctx(env, transport)
        steps.then_format_id_roundtrips_verbatim(ctx)

    assert "list_creatives" in seen, (
        f"{transport.value}: the format_id-roundtrip Then never dispatched list_creatives "
        f"(tools dispatched through AdCPTestClient: {seen}). It graded only the DB row, so it "
        "cannot detect a seller that persists the format_id correctly and then serializes it "
        "wrong on the wire — the exact roundtrip the storyboard step grades."
    )


#: A foreign value per half of the v3.1 ``format_id`` federation pair, for the
#: "changed" corruption below.
_FOREIGN_FORMAT_ID = {
    "id": "format_id_from_another_seller",
    "agent_url": "https://another-seller.example.com",
}

#: How a seller can break the roundtrip on each half. BOTH modes are graded on
#: BOTH halves: ``agent_url`` used to be asserted only ``if wire_agent_url is not
#: None``, which caught a CHANGED value but not a DROPPED one — the same
#: grades-nothing-when-absent defect as C4's status check, one field over. A
#: mutation that only substitutes values cannot tell the two forms apart, so the
#: drop is a separate, named case.
FORMAT_ID_CORRUPTIONS = ("changed", "dropped")


def _corrupt_format_id_on_wire(wire: dict[str, Any], half: str, mode: str) -> dict[str, Any]:
    """A copy of *wire* whose every creative's ``format_id`` *half* is broken.

    Targets the ``format_id`` object specifically rather than string-replacing
    across the whole body, so "dropped" is expressible at all and "changed"
    cannot accidentally rewrite an unrelated field that happens to share a value.
    """
    corrupted = copy.deepcopy(wire)
    for creative in corrupted.get("creatives") or []:
        format_id = creative.get("format_id")
        if not isinstance(format_id, dict):
            continue
        if mode == "dropped":
            format_id.pop(half, None)
        else:
            format_id[half] = _FOREIGN_FORMAT_ID[half]
    return corrupted


@pytest.mark.parametrize("transport", WIRE_TRANSPORTS, ids=lambda t: t.value)
@pytest.mark.parametrize("mode", FORMAT_ID_CORRUPTIONS)
@pytest.mark.parametrize("half", sorted(_FOREIGN_FORMAT_ID))
def test_format_id_roundtrip_then_fails_when_the_wire_contradicts_the_captured_id(
    integration_db, monkeypatch: pytest.MonkeyPatch, transport: Transport, half: str, mode: str
) -> None:
    """A wire that breaks EITHER half of the captured format_id, either way, must FAIL the Then.

    The complement of the spy test: proves the wire read is the PRIMARY assertion
    and not a decorative extra call. The DB row is left correct and only the
    ``list_creatives`` wire is corrupted, so a Then that still grades the
    repository read passes green on a wire that contradicts it.
    """
    from tests.harness.client import AdCPTestClient

    original = AdCPTestClient.call

    def _lying_call(self: Any, tool: str, payload: dict[str, Any], tr: Transport, **kwargs: Any) -> TransportResult:
        result = original(self, tool, payload, tr, **kwargs)
        if tool != "list_creatives" or result.wire_response is None:
            return result
        return replace(result, wire_response=_corrupt_format_id_on_wire(result.wire_response, half, mode))

    with _sync_env(f"wire-c3-lie-{half}-{mode}-{transport.value}") as env:
        ctx = _live_scenario_ctx(env, transport)
        monkeypatch.setattr(AdCPTestClient, "call", _lying_call)

        with pytest.raises(AssertionError):
            steps.then_format_id_roundtrips_verbatim(ctx)


# ═══════════════════════════════════════════════════════════════════════
# C4 — a DERIVED status enum on the error envelope, never a fabricated code
# ═══════════════════════════════════════════════════════════════════════

#: The two members of C4's derived enum, read off the harness constants rather
#: than restated as literals here. Deliberately NOT an integer ``status_code``:
#: synthesizing an HTTP status for MCP/A2A would turn a silent no-op into a loud
#: tautology — the harness asserting != 500 against a number it invented.
DERIVED_STATUS_VALUES = (DERIVED_STATUS_ADCP_ERROR, DERIVED_STATUS_TRANSPORT_FAULT)


#: The C4 error payload: one creative, dispatched unauthenticated so the seller
#: answers with a structured AdCP rejection rather than a fault.
def _c4_payload(env: Any) -> dict[str, Any]:
    return {
        "creatives": [
            {
                "creative_id": "creative-c4-001",
                "name": "C4 Grader Creative",
                "format_id": {"id": "display_300x250", "agent_url": env.DEFAULT_AGENT_URL},
            }
        ]
    }


def _dispatch_call_via(env: Any, transport: Transport, payload: dict[str, Any]) -> TransportResult:
    """``env.call_via`` — the dispatcher path (``tests/harness/dispatchers.py``)."""
    return env.call_via(transport, identity=None, **payload)


def _dispatch_client(env: Any, transport: Transport, payload: dict[str, Any]) -> TransportResult:
    """``AdCPTestClient.call`` — the path ``dispatch_via_client`` takes.

    THE path both UC-003 storyboard scenarios that bind
    ``then_response_not_500_or_non_adcp_shape`` actually use
    (``tests/bdd/steps/domain/uc003_storyboard_generic_client.py``'s When steps
    call ``dispatch_via_client`` -> ``AdCPTestClient.call``). Grading only
    ``call_via`` certified a mechanism the graded scenarios never reach — which
    is how the derived status came to be wired on one path and absent on the
    other while the grader stayed green.
    """
    from tests.harness.client import AdCPTestClient

    return AdCPTestClient(env).call("sync_creatives", payload, transport, credential={})


#: Both dispatch paths a storyboard scenario can take to the seller. Every C4
#: obligation below is parametrized over BOTH — a signal that exists on one path
#: and not the other grades nothing on the scenarios that take the other one.
DISPATCH_PATHS = {"call_via": _dispatch_call_via, "client": _dispatch_client}

#: Where a genuine transport fault is injected, per transport: the env primitive
#: that transport's DELIVER calls on BOTH dispatch paths (``_deliver_mcp`` /
#: ``CreativeSyncEnv.deliver_mcp`` both reach ``_run_mcp_client``; the two REST
#: legs both reach ``get_rest_client``). Raising there reproduces the real
#: failure mode the derived status names — the request died before any AdCP
#: envelope existed — rather than hand-setting ``status`` on the result, which
#: would grade the assertion against a value the test itself invented.
_FAULT_INJECTION_POINT = {
    Transport.MCP: "_run_mcp_client",
    Transport.A2A: "_run_a2a_handler",
    Transport.REST: "get_rest_client",
}

_FAULT_MESSAGE = "simulated transport fault: the seller died before emitting an AdCP envelope"


def _dispatch_with_transport_fault(
    monkeypatch: pytest.MonkeyPatch, env: Any, transport: Transport, dispatch_path: str
) -> TransportResult:
    """Dispatch with *transport*'s delivery primitive faulted, via *dispatch_path*."""

    def _fault(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(_FAULT_MESSAGE)

    monkeypatch.setattr(env, _FAULT_INJECTION_POINT[transport], _fault)
    return DISPATCH_PATHS[dispatch_path](env, transport, _c4_payload(env))


@pytest.mark.parametrize("transport", WIRE_TRANSPORTS, ids=lambda t: t.value)
@pytest.mark.parametrize("dispatch_path", sorted(DISPATCH_PATHS))
def test_error_envelope_carries_a_derived_status_on_every_transport(
    integration_db, transport: Transport, dispatch_path: str
) -> None:
    """``TransportResult.envelope['status']`` must be derived on mcp/a2a/rest, on BOTH paths.

    Measured before the wire-grading fix: ``envelope`` was ``{}`` on mcp, ``{'transport':
    'a2a'}`` on a2a, and carried a real ``status_code`` only on rest. Measured
    after the first wire-grading pass: derived on the ``call_via`` path only, still
    ``{}`` on mcp and a2a through ``AdCPTestClient.call`` — the path the graded
    storyboard scenarios take. Hence the ``dispatch_path`` parametrization: this
    obligation is about the signal existing everywhere a scenario can observe it,
    not about one dispatcher having it.

    The authentic per-transport sources are named by the design: REST's real HTTP
    body, A2A's failed-Task artifact DataPart, MCP's ToolError. A structured AdCP
    rejection must read ``adcp_error`` on all three.
    """
    with _sync_env(f"wire-c4-{dispatch_path}-{transport.value}") as env:
        env.setup_default_data()
        result = DISPATCH_PATHS[dispatch_path](env, transport, _c4_payload(env))

    assert result.is_error, f"{transport.value}/{dispatch_path}: expected an AdCP rejection, got {result.payload!r}"
    status = result.envelope.get("status")
    assert status in DERIVED_STATUS_VALUES, (
        f"{transport.value}/{dispatch_path}: TransportResult.envelope carries no derived status "
        f"(envelope={result.envelope!r}). Without it, 'the response should NOT be a 500 or "
        "non-AdCP error shape' grades nothing on this transport."
    )
    assert status == DERIVED_STATUS_ADCP_ERROR, (
        f"{transport.value}/{dispatch_path}: a structured AdCP rejection must derive "
        f"status={DERIVED_STATUS_ADCP_ERROR!r}, got {status!r}"
    )


@pytest.mark.parametrize("transport", WIRE_TRANSPORTS, ids=lambda t: t.value)
@pytest.mark.parametrize("dispatch_path", sorted(DISPATCH_PATHS))
def test_a_real_transport_fault_derives_transport_fault_on_every_path(
    integration_db, monkeypatch: pytest.MonkeyPatch, transport: Transport, dispatch_path: str
) -> None:
    """A delivery that dies before producing an envelope must derive ``transport_fault``.

    The complement of the test above, and the half that proves the enum is a
    MEASUREMENT rather than a constant: with the transport's delivery primitive
    raising, no AdCP envelope is recoverable on any path, so both the derived
    status and ``wire_error_envelope`` must say so.
    """
    with _sync_env(f"wire-c4-fault-{dispatch_path}-{transport.value}") as env:
        env.setup_default_data()
        result = _dispatch_with_transport_fault(monkeypatch, env, transport, dispatch_path)

    assert result.is_error, f"{transport.value}/{dispatch_path}: the injected fault produced no error result"
    assert result.wire_error_envelope is None, (
        f"{transport.value}/{dispatch_path}: a faulted delivery recovered a wire envelope "
        f"({result.wire_error_envelope!r}) — the fault injection is not reaching the transport"
    )
    assert result.envelope.get("status") == DERIVED_STATUS_TRANSPORT_FAULT, (
        f"{transport.value}/{dispatch_path}: a delivery that produced no AdCP envelope must derive "
        f"status={DERIVED_STATUS_TRANSPORT_FAULT!r}, got envelope={result.envelope!r}"
    )


@pytest.mark.parametrize("transport", WIRE_TRANSPORTS, ids=lambda t: t.value)
@pytest.mark.parametrize("dispatch_path", sorted(DISPATCH_PATHS))
def test_not_500_then_fails_on_a_real_transport_fault(
    integration_db, monkeypatch: pytest.MonkeyPatch, transport: Transport, dispatch_path: str
) -> None:
    """``then_response_not_500_or_non_adcp_shape`` must REDDEN on a real transport fault.

    The mutation is a genuine fault at the transport (see
    ``_dispatch_with_transport_fault``), not a hand-set ``status`` key — an
    assertion can only be trusted to observe a state if the state is produced the
    way production produces it.

    The failure must come from THIS step's own obligation, so the message is
    pinned. Written with the wire read first, the step was structurally
    unreachable: ``wire_error_dict``'s missing-wire guard fired before the status
    was ever examined, so "the seller returned a transport fault" could not be
    observed at the assertion point on ANY path — a grader that cannot see the
    failure state is not a grader. Asserting on the message is what distinguishes
    the fixed step from the broken one, since both raise ``AssertionError``.
    """
    from tests.bdd.steps.domain import uc003_storyboard_generic_client as uc003_steps

    with _sync_env(f"wire-c4-then-{dispatch_path}-{transport.value}") as env:
        env.setup_default_data()
        result = _dispatch_with_transport_fault(monkeypatch, env, transport, dispatch_path)

        ctx: dict[str, Any] = {"env": env, "transport": transport}
        _populate_ctx_from_result(ctx, result)

        with pytest.raises(AssertionError) as excinfo:
            uc003_steps.then_response_not_500_or_non_adcp_shape(ctx)

    message = str(excinfo.value)
    assert "not a 500 or non-AdCP error shape" in message, (
        f"{transport.value}/{dispatch_path}: the step failed, but not through its own "
        f"not-a-500 obligation — the derived status is still unreachable behind another guard: {message!r}"
    )
    assert DERIVED_STATUS_TRANSPORT_FAULT in message, (
        f"{transport.value}/{dispatch_path}: the failure did not report the derived transport fault: {message!r}"
    )
