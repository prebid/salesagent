"""Every step-level dispatch entry gates and records — none of them is a side door.

``assert_declared_malformations`` shipped with ONE call site while three step-level
entries reached a transport (salesagent-99w2t). Nothing committed was wrong at the
time: all five declared malformations happened to dispatch through the gated entry.
But the gate covered one path of three, and the two ungated ones are where the next
scenario gets written. The same shape would have made the payload capture measure a
subset of the traffic and say nothing about the rest.

The fix was not three hooks. ``when_request._call_via`` was FOLDED into
``dispatch_request`` (three entries became two), ``dispatch_via_client`` — which
cannot be folded, because ``AccountListDispatchMixin.is_list_request``
discriminates on ``isinstance(kwargs["req"], ListAccountsRequest)`` and would
misroute a raw list payload to ``sync_accounts`` — was gated in place, and the two
read-back sites call the shared ``gate_and_record`` directly.

These tests exist to be BROKEN on purpose. Delete either obligation from
``gate_and_record``, or route a seam around it, and a named test goes red.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.bdd import payload_capture
from tests.bdd.steps.generic._dispatch import dispatch_request, dispatch_via_client, gate_and_record
from tests.bdd.steps.generic.when_request import _call_via
from tests.harness.transport import Transport

#: A creative the pinned request model REJECTS, undeclared. ``creative_id``/``name``
#: alone omit the required asset content, which is exactly what a migration dropping
#: an override leaves behind.
UNDECLARED_MALFORMATION = {"creative_id": "c1", "name": "n"}


class _Detonate(Exception):
    """Raised by the stub transport, to prove what ran BEFORE the dispatch."""


class _StubEnv:
    """An env whose ``call_via`` records its kwargs and then refuses to go further."""

    def __init__(self) -> None:
        self.seen: list[tuple[Transport, dict[str, Any]]] = []

    def call_via(self, transport: Transport, **kwargs: Any) -> Any:
        self.seen.append((transport, kwargs))
        raise _Detonate


class _StubClient:
    def __init__(self) -> None:
        self.seen: list[tuple[str, dict[str, Any]]] = []

    def call(self, tool: str, payload: dict[str, Any], transport: Transport, **kwargs: Any) -> Any:
        del transport, kwargs
        self.seen.append((tool, payload))
        raise _Detonate


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Drain what the capture recorded, without a pytest session around it.

    Patches the module attribute the seam RESOLVES AT CALL TIME
    (``_dispatch.record_dispatched_request``) rather than a dotted path nothing
    consults — the 30 BDD step modules bind their imports by value, and a probe that
    patched the wrong name measured 771 of 2509 real dispatches and read the rest as
    zero (salesagent-ryzil.2).
    """
    recorded: list[Any] = []
    monkeypatch.setattr(
        "tests.bdd.steps.generic._dispatch.record_dispatched_request",
        lambda payload: recorded.append(payload_capture.capture_payload(payload)),
    )
    return recorded


# ── The gate reaches every entry ─────────────────────────────────────


def test_dispatch_request_refuses_an_undeclared_malformation(captured: list[Any]) -> None:
    ctx = {"env": _StubEnv(), "transport": Transport.MCP}
    with pytest.raises(AssertionError, match="not declared malformed"):
        dispatch_request(ctx, creatives=[UNDECLARED_MALFORMATION])
    assert ctx["env"].seen == [], "the gate must fire BEFORE anything reaches a transport"


def test_call_via_refuses_an_undeclared_malformation(captured: list[Any]) -> None:
    """FAILS before the fold: ``_call_via`` reached ``env.call_via`` ungated."""
    ctx: dict[str, Any] = {"env": _StubEnv()}
    with pytest.raises(AssertionError, match="not declared malformed"):
        _call_via(ctx, Transport.MCP, creatives=[UNDECLARED_MALFORMATION])
    assert ctx["env"].seen == []


def test_dispatch_via_client_refuses_an_undeclared_malformation(captured: list[Any]) -> None:
    """FAILS before part B: the client seam dispatched anything it was handed."""
    ctx = {"client": _StubClient(), "transport": Transport.REST}
    with pytest.raises(AssertionError, match="not declared malformed"):
        dispatch_via_client(ctx, "sync_creatives", {"creatives": [UNDECLARED_MALFORMATION]})
    assert ctx["client"].seen == []


def test_gate_and_record_is_the_shared_obligation(captured: list[Any]) -> None:
    """The read-back sites call this directly; it must do BOTH halves."""
    with pytest.raises(AssertionError, match="not declared malformed"):
        gate_and_record({"creatives": [UNDECLARED_MALFORMATION]})
    gate_and_record({"req": None, "filters": {"concept_ids": ["x"]}})
    assert captured == [{"req": None, "filters": {"concept_ids": ["x"]}}]


# ── The capture sees every entry ─────────────────────────────────────


def test_every_entry_records_the_payload_it_dispatched(captured: list[Any]) -> None:
    ctx_env: dict[str, Any] = {"env": _StubEnv(), "transport": Transport.A2A}
    for call in (
        lambda: dispatch_request(ctx_env, name_search="alpha"),
        lambda: _call_via(ctx_env, Transport.A2A, min_width=-1),
        lambda: dispatch_via_client({"client": _StubClient(), "transport": Transport.REST}, "t", {"page": 2}),
    ):
        with pytest.raises(_Detonate):
            call()
    assert captured == [{"name_search": "alpha"}, {"min_width": -1}, {"page": 2}]


# ── The fold's one behavioural equivalence, pinned ───────────────────


def test_call_via_forwards_req_unshaped_and_names_the_transport() -> None:
    """The MCP branch no longer flattens ``req`` here — the env's dispatcher does.

    ``_call_via`` used to merge ``req.model_dump(exclude_none=True)`` into the kwargs
    for MCP only, so the same scenario dispatched ``format_ids[]`` on MCP and
    ``req.format_ids[]`` on a2a/rest: a different key path and a different value type
    for 335 events, which would make ``compare_payloads``' transport-twin rule compare
    unlike with unlike. Forwarding ``req=`` unchanged is equivalent for both remaining
    callers because both dispatch on ``CreativeFormatsEnv``, whose ``deliver_mcp``
    routes to ``_run_mcp_client`` — which pops ``req`` and performs the identical dump.

    It is NOT equivalent for an env on the base client-core path, which would send
    ``{"req": <model>}`` as the MCP arguments, so the OTHER half of the equivalence --
    that the env really does the dump -- is measured against a live dispatch in
    ``tests/integration/test_bdd_dispatch_seam.py`` rather than asserted about source.
    """
    from src.core.schemas import ListCreativeFormatsRequest

    req = ListCreativeFormatsRequest()
    ctx: dict[str, Any] = {"env": _StubEnv()}
    with pytest.raises(_Detonate):
        _call_via(ctx, "mcp", req=req)

    ((transport, kwargs),) = ctx["env"].seen
    assert transport is Transport.MCP, "the string transport must reach _as_transport, not a private map"
    assert kwargs == {"req": req}, "req must travel whole; the per-transport flattening belongs to the env"
    assert ctx["transport"] == "mcp", "the transport argument is written where the single normalizer reads it"
