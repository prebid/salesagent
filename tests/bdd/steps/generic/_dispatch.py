"""Shared dispatch helpers for BDD domain step definitions.

Named entry points (salesagent-hwji), replacing the single polymorphic
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
- :func:`dispatch_typed_or_malformed` — tries building the request model; falls
  back to :func:`dispatch_malformed_request` on validation failure. For
  scenario-outline sites whose Examples rows mix well-formed and deliberately
  malformed payloads, where the step can't know in advance which it got.
- :func:`dispatch_request_with_extra` — :func:`dispatch_request` plus a real
  transport-level parameter that is NOT part of the AdCP request model (e.g.
  ``include_snapshot`` on ``get_media_buys``). Keeps :func:`dispatch_request`'s
  own signature clean for the common case.

All entry points share the same transport-dispatch mechanics (:func:`_dispatch`):
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


def dispatch_typed_or_malformed(
    ctx: dict, model_cls: type[BaseModel], *, identity: Any = _SENTINEL, **kwargs: Any
) -> None:
    """Dispatch ``kwargs`` typed if it validates, malformed otherwise.

    For scenario-outline sites where SOME Examples rows are deliberately
    malformed and others are well-formed, and the step itself has no way to
    know in advance which shape a given row produced. Tries
    ``model_cls(**kwargs)``; on success, dispatches it through
    :func:`dispatch_request`. On ``ValidationError`` (Pydantic field-shape
    rejection) or ``AdCPValidationError`` (a ``@model_validator``, e.g.
    ``idempotency_key``'s pattern check), falls back to
    :func:`dispatch_malformed_request` with the same raw kwargs — production,
    not the test process, is what rejects them.

    Do not reach for this when a site's kwargs are always well-formed (use
    :func:`dispatch_request` directly) or always malformed (use
    :func:`dispatch_malformed_request` directly) — it exists for the sites
    that are genuinely either, depending on the Examples row.
    """
    from pydantic import ValidationError

    from src.core.exceptions import AdCPValidationError

    try:
        req = model_cls(**kwargs)
    except (ValidationError, AdCPValidationError):
        dispatch_malformed_request(ctx, identity=identity, **kwargs)
        return
    dispatch_request(ctx, req=req, identity=identity)


def dispatch_request_with_extra(ctx: dict, *, req: BaseModel, identity: Any = _SENTINEL, **extra: Any) -> None:
    """:func:`dispatch_request`, plus dispatch-level kwargs that are real transport
    parameters but NOT AdCP request-model fields (e.g. ``include_snapshot`` on
    ``get_media_buys`` — a genuine MCP/A2A/REST tool parameter forwarded
    separately from the request body to the ``_impl`` call).

    Keeps :func:`dispatch_request`'s own signature clean — this is the escape
    hatch for the rare non-model param, not a second general-purpose dispatch
    shape. Most sites should use :func:`dispatch_request` directly.
    """
    ctx["dispatched_request"] = req
    _dispatch(ctx, {"req": req, **extra}, identity)
