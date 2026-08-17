"""Shared dispatch helper for BDD domain step definitions.

Provides a single implementation of the transport-aware dispatch pattern
used across UC-004, UC-011, and future domain step files.
"""

from __future__ import annotations

from typing import Any

_SENTINEL = object()


def dispatch_request(ctx: dict, *, identity: Any = _SENTINEL, **kwargs: Any) -> None:
    """Dispatch a request through ctx['transport'] via call_via, or direct call_impl.

    Stores result in ctx["response"] on success, ctx["error"] on failure.
    If ctx["transport"] is a Transport enum, uses call_via directly.
    If it's a string, maps to Transport enum first.
    If absent, falls back to call_impl.

    The ``identity`` kwarg overrides the default identity for multi-agent
    and no-auth scenarios. When provided, it flows through to call_via
    (which uses kwargs.setdefault, so an explicit identity won't be clobbered).
    Use ``identity=None`` for no-auth scenarios.

    Records what was dispatched in ``ctx["dispatched_kwargs"]`` so Then steps can
    derive their expected values from the request instead of hardcoding them. An
    oracle built on a literal default is indistinguishable from no assertion at
    all — see GH #1749, where a step compared against the constants 7/"days" that
    happened to match its only scenario.

    This is a DISTINCT channel. Do not conflate it with:

    - ``ctx["request_params"]`` — an EXPECTATION channel. It deliberately holds
      values that were never dispatched (``["(field absent)"]``) and, in
      ``_dispatch_resolution``, deliberately fewer ids than were sent.
    - ``ctx["request_kwargs"]`` — the request-builder channel, which UC-026
      ``pop``s to reset between requests and asserts on directly.

    The recorded dict is HETEROGENEOUS — readers must not assume flat kwargs. It
    may hold plain kwargs, a whole Pydantic request object (``req=``), or be empty
    for a no-argument dispatch. ``identity`` is excluded: it is a dispatch control
    kwarg, not a request field, so the snapshot is taken before it is merged in.
    """
    ctx["dispatched_kwargs"] = dict(kwargs)

    if identity is not _SENTINEL:
        kwargs["identity"] = identity

    transport = ctx.get("transport")
    env = ctx["env"]
    # BDD dispatches on a wire transport only (IMPL was dropped from the default
    # parametrization, #1417). A missing transport is a wiring bug, not
    # an IMPL fallback — fail loudly rather than silently bypassing the wire.
    if transport is None:
        raise RuntimeError(
            "dispatch_request: ctx['transport'] is unset. BDD scenarios must dispatch "
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
            raise RuntimeError(f"dispatch_request: unrecognized wire transport {transport!r}")
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
