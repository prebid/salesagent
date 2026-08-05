"""Shared dispatch helpers for BDD domain step definitions.

Three named entry points (salesagent-hwji), replacing the single polymorphic
``dispatch_request`` that used to accept either a validated request model OR raw
flat kwargs indistinguishably:

- :func:`dispatch_request` — the ONLY form for well-formed requests. Takes a
  validated AdCP request model (``req=``). Records ``ctx["dispatched_request"]``.
- :func:`dispatch_malformed_request` — the negative path. Takes raw field values
  that could NOT become a request model. Records ``ctx["dispatched_malformed"]``,
  never ``dispatched_request`` — this is the explicit statement that no request
  model exists for this dispatch, so the expected-side accessor
  (``dispatched_request(ctx)`` in ``_outcome_helpers.py``) can raise loudly naming
  the malformed channel instead of silently handing a Then-step a shape it never
  asked for.
- :func:`dispatch_raw_kwargs` — DEPRECATED. The old polymorphic form, kept ONLY
  for call sites outside UC-004 that salesagent-oyiv.4 has not yet migrated.
  Records ``ctx["dispatched_kwargs"]``. Do not add new call sites.

All three share the same transport-dispatch mechanics (:func:`_dispatch`):
resolve ``ctx["transport"]``, call ``env.call_via``, store the outcome. Used
across UC-004, UC-011, and other domain step files.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic import BaseModel

_SENTINEL = object()


def _dispatch(ctx: dict, kwargs: dict[str, Any], identity: Any) -> None:
    """Shared transport-dispatch mechanics for all three entry points below.

    Resolves ``ctx["transport"]``, calls ``env.call_via(transport, **kwargs)``, and
    stores the outcome on ``ctx`` (``result``/``response``/``wire_response`` on
    success, ``error``/``wire_error_envelope``/``synthesized_error_envelope`` on
    failure). Does NOT record what was dispatched — callers do that themselves,
    before calling this, so the recorded snapshot is taken before the identity
    merge below (#1728's property, preserved).
    """
    if identity is not _SENTINEL:
        kwargs["identity"] = identity

    transport = ctx.get("transport")
    env = ctx["env"]
    # BDD dispatches on a wire transport only (IMPL was dropped from the default
    # parametrization, #1417). A missing transport is a wiring bug, not
    # an IMPL fallback — fail loudly rather than silently bypassing the wire.
    if transport is None:
        raise RuntimeError(
            "dispatch: ctx['transport'] is unset. BDD scenarios must dispatch "
            "through a wire transport (a2a/mcp/rest); the IMPL call_impl fallback was removed."
        )

    from tests.harness.transport import Transport

    if isinstance(transport, Transport):
        pass  # Already a Transport enum — use as-is
    elif isinstance(transport, str):
        transport_map = {
            "MCP": Transport.MCP,
            "mcp": Transport.MCP,
            "A2A": Transport.A2A,
            "a2a": Transport.A2A,
            "REST": Transport.REST,
            "rest": Transport.REST,
        }
        if transport not in transport_map:
            raise RuntimeError(f"dispatch: unrecognized wire transport {transport!r}")
        transport = transport_map[transport]
    try:
        result = env.call_via(transport, **kwargs)
        # Expose the normalized TransportResult so Then-steps can use the
        # harness-provided, transport-independent assertions (result.assert_wire_error)
        # instead of hand-rolling envelope parsing.
        ctx["result"] = result
        if result.is_error:
            ctx["error"] = result.error
            # Capture the real wire envelope (A2A/REST/MCP) and the
            # synthesized envelope (IMPL has no wire) so Then steps can
            # assert the two-layer AdCP shape per the Error Verification
            # Policy. Both are None-safe; absent keys mean "no envelope".
            ctx["wire_error_envelope"] = result.wire_error_envelope
            ctx["synthesized_error_envelope"] = result.synthesized_error_envelope
        else:
            ctx["response"] = result.payload
            # Propagate the real serialized success-path wire body so Then steps
            # can assert on what the buyer actually receives (ctx["wire_response"]),
            # not the reconstructed typed payload (REST HTTP body; A2A/MCP artifact
            # only when the env routes through _run_a2a_handler/_run_mcp_client).
            # None on IMPL / non-stashing envs; the wire_field() helper guards
            # against silent tautologies (#1417). See tests/CLAUDE.md
            # "TransportResult.wire_response".
            ctx["wire_response"] = result.wire_response
    except Exception as exc:
        ctx["error"] = exc


def dispatch_request(ctx: dict, *, req: BaseModel, identity: Any = _SENTINEL) -> None:
    """Dispatch a validated AdCP request model — the only form for well-formed requests.

    Records ``ctx["dispatched_request"] = req`` BEFORE the identity merge and BEFORE
    ``env.call_via`` (both #1728 properties, preserved), so Then-steps can derive
    their expected values from the request the scenario actually built instead of
    hardcoding them. An oracle built on a literal default is indistinguishable from
    no assertion at all — see GH #1749, where a step compared against the constants
    7/"days" that happened to match its only scenario.

    Use :func:`dispatch_malformed_request` for values that could NOT become a
    validated request model — that is a different, explicit channel, not a second
    shape this function should also accept.
    """
    ctx["dispatched_request"] = req
    _dispatch(ctx, {"req": req}, identity)


def dispatch_malformed_request(ctx: dict, *, identity: Any = _SENTINEL, **raw: Any) -> None:
    """Dispatch raw field values that could NOT become a validated request model.

    Records ``ctx["dispatched_malformed"] = dict(raw)``, NEVER ``dispatched_request``
    — this is not a second request shape, it is the explicit statement that no
    request model exists for this dispatch. It makes the "the buyer sent garbage"
    case unreadable by the expected-side oracle (``dispatched_request(ctx)`` raises
    loudly naming this channel) instead of silently readable as if it were well-formed.

    The raw values are sent through unchanged on every transport: a2a/mcp receive
    them as flat arguments (no ``req`` present, so the harness's own
    ``arguments = dict(kwargs)`` fallback applies); REST/e2e_rest receive them
    verbatim as the JSON body (``BaseTestEnv.build_rest_body``'s no-``req`` fallback),
    so production — not the test process — is what rejects them.
    """
    ctx["dispatched_malformed"] = dict(raw)
    _dispatch(ctx, dict(raw), identity)


def dispatch_raw_kwargs(ctx: dict, *, identity: Any = _SENTINEL, **kwargs: Any) -> None:
    """DEPRECATED — salesagent-oyiv.4 deletes this; do not add call sites.

    The old polymorphic dispatch form: accepted either flat kwargs or a whole
    ``req=`` Pydantic model indistinguishably, so the expected-side accessor could
    never be single-typed. Kept only for the non-UC-004 call sites
    salesagent-oyiv.4 has not yet migrated onto :func:`dispatch_request` /
    :func:`dispatch_malformed_request`. Records ``ctx["dispatched_kwargs"]`` — the
    legacy channel ``dispatched_field`` (in ``_outcome_helpers.py``) still reads for
    those un-migrated call sites.
    """
    ctx["dispatched_kwargs"] = dict(kwargs)
    _dispatch(ctx, dict(kwargs), identity)
