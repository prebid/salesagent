"""Dispatcher classes — one per transport.

Each dispatcher calls the env's transport-specific method and wraps the
result in a TransportResult. The env subclass provides the actual call logic;
the dispatcher only handles result wrapping and error capture.

On error, dispatchers capture the wire error envelope (the raw two-layer dict
the buyer would see) alongside the reconstructed exception.  New tests should
assert on ``result.wire_error_envelope`` via ``assert_envelope_shape()`` — see
``tests/CLAUDE.md`` § Error Verification Policy.

Usage (internal — called by BaseTestEnv.call_via)::

    dispatcher = DISPATCHERS[Transport.A2A]
    result = dispatcher.dispatch(env, **kwargs)
"""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING, Any

from tests.harness.transport import Transport, TransportResult

if TYPE_CHECKING:
    from tests.harness._base import BaseTestEnv


def _envelope_from_adcp_error(exc: Exception) -> dict[str, Any] | None:
    """Build a SYNTHESIZED envelope from an AdCPError instance.

    Used by ImplDispatcher to populate the separate
    ``synthesized_error_envelope`` field — IMPL has no wire by definition
    and ``wire_error_envelope`` is reserved for real wire bytes captured
    by REST/MCP/A2A. Production code uses the same
    ``build_two_layer_error_envelope`` helper at the boundary, so the
    synthesized envelope matches what production would emit for the same
    exception. It does NOT verify that a regression in
    ``build_two_layer_error_envelope`` actually reaches the wire.

    A2A and REST tests asserting on ``result.wire_error_envelope`` see
    REAL wire bytes:
        - A2A: the artifact DataPart, attached to the reconstructed
          ``AdCPError`` as ``_wire_error_envelope`` by
          ``tests.harness._base._envelope_to_adcp_error``.
        - REST: the HTTP response body, captured directly by RestDispatcher.
        - MCP: the JSON string in ``ToolError``, parsed by McpDispatcher.
    """
    from src.core.exceptions import AdCPError, build_two_layer_error_envelope

    if isinstance(exc, AdCPError):
        return build_two_layer_error_envelope(exc)
    return None


def _wire_envelope_from_exception(exc: Exception) -> dict[str, Any] | None:
    """Prefer the REAL wire envelope stashed by the harness; fall back to synthesized.

    When the A2A pipeline reconstructs an AdCPError from a failed Task's
    artifact DataPart, ``tests.harness._base._envelope_to_adcp_error``
    attaches the captured envelope to the exception as
    ``_wire_error_envelope``. This helper returns that real wire envelope
    if present; otherwise falls back to ``_envelope_from_adcp_error``
    (synthesized — same helper production calls). The fallback covers envs
    whose ``call_a2a`` uses the direct ``*_raw`` path (no Task framing, so
    no stash), e.g. CreativeSyncEnv (#1417).
    """
    real_wire = getattr(exc, "_wire_error_envelope", None)
    if isinstance(real_wire, dict):
        return real_wire
    return _envelope_from_adcp_error(exc)


def _envelope_from_mcp_error(exc: Exception) -> dict[str, Any] | None:
    """Extract the wire envelope from an MCP ToolError's JSON string."""
    from fastmcp.exceptions import ToolError

    if not isinstance(exc, ToolError):
        return None
    try:
        envelope = json.loads(str(exc))
        if isinstance(envelope, dict) and "errors" in envelope:
            return envelope
    except (json.JSONDecodeError, TypeError):
        pass
    return None


class ImplDispatcher:
    """Dispatch via direct ``_impl()`` call.

    IMPL is the in-process direct call — there is no wire by definition.
    ``wire_error_envelope`` is left ``None`` on this transport; the envelope
    that production WOULD emit at the boundary is exposed on the separate
    ``synthesized_error_envelope`` field so tests cannot accidentally lean
    on IMPL to catch real-wire regressions (a regression in the production
    boundary translator would not change what this dispatcher computes,
    because both call ``build_two_layer_error_envelope`` on the same
    in-memory exception). Use A2A, REST, or MCP for wire-shape coverage.
    """

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        try:
            payload = env.call_impl(**kwargs)
        except Exception as exc:
            return TransportResult(
                error=exc,
                synthesized_error_envelope=_envelope_from_adcp_error(exc),
            )
        return TransportResult(payload=payload, envelope={"transport": "impl"})


class A2ADispatcher:
    """Dispatch via ``handler.on_message_send`` — exercises the full A2A pipeline.

    ``env.call_a2a`` drives ``AdCPRequestHandler.on_message_send`` end-to-end
    (message parsing → skill routing → handler dispatch → ``_serialize_for_a2a``
    → Task/Artifact framing). On a failed Task, the harness reconstructs the
    ``AdCPError`` from the artifact DataPart and stashes the real wire
    envelope on the exception via ``_wire_error_envelope`` — captured here
    by ``_wire_envelope_from_exception``.
    """

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        try:
            payload = env.call_a2a(**kwargs)
        except Exception as exc:
            return TransportResult(
                error=exc,
                wire_error_envelope=_wire_envelope_from_exception(exc),
            )
        # Real A2A wire: the artifact DataPart dict stashed by _run_a2a_handler
        # (declared on BaseTestEnv, reset per call_via — read directly so a
        # missed capture surfaces as None against a known attribute, not getattr).
        return TransportResult(
            payload=payload,
            envelope={"transport": "a2a"},
            wire_response=env._last_wire_response,
        )


class RestDispatcher:
    """Dispatch via FastAPI TestClient → route → _raw() → _impl().

    Identity flows through kwargs to env._run_rest_request(), which pops it
    and configures the FastAPI auth dep override per-request.

    Unlike other dispatchers, REST includes HTTP metadata in the envelope
    (status_code, content_type) since tests may assert on these.
    """

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        try:
            endpoint = env.REST_ENDPOINT  # type: ignore[attr-defined]
            response = env._run_rest_request(endpoint, **kwargs)

            envelope = {
                "transport": "rest",
                "status_code": response.status_code,
                "content_type": response.headers.get("content-type", ""),
            }

            if response.status_code >= 400:
                body = response.json()
                error = env.parse_rest_error(response.status_code, body)
                return TransportResult(
                    error=error,
                    envelope=envelope,
                    raw_response=response,
                    wire_error_envelope=body,
                )

            body = response.json()
            # Parse a COPY: env parsers strip envelope keys in place (e.g.
            # _parse_update_rest_response pops "status", #1417), which
            # would silently delete fields from the stashed wire capture — the
            # dispatcher owns the pristine-wire guarantee (#1417).
            payload = env.parse_rest_response(copy.deepcopy(body))
            # Real REST wire: the HTTP JSON body dict.
            return TransportResult(payload=payload, envelope=envelope, raw_response=response, wire_response=body)
        except Exception as exc:
            return TransportResult(error=exc)


class McpDispatcher:
    """Dispatch via Client(mcp) — full FastMCP pipeline.

    Identity flows through kwargs to env.call_mcp() → _run_mcp_client(),
    which pops it and dispatches via FastMCP in-memory transport.
    """

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        try:
            payload = env.call_mcp(**kwargs)
        except Exception as exc:
            # REAL wire only: the raw MCP ToolError JSON when present, else
            # the envelope the harness reconstruction stashed on the AdCPError
            # as ``_wire_error_envelope`` (same stash A2A uses). NEVER the
            # synthesized fallback — a dead MCP wire path must yield None here
            # (failing assert_envelope_shape), not an envelope regenerated
            # from the lossy reconstructed exception.
            wire = _envelope_from_mcp_error(exc) or getattr(exc, "_wire_error_envelope", None)
            # When a wire envelope came from the raw ToolError JSON, exc is an
            # AdCPToolError carrying that envelope (an env that dispatched through
            # the production with_error_logging boundary). Unwrap it so
            # result.error is the typed AdCPError — error-code assertions resolve
            # to the real wire code, not "AdCPToolError". Typed errors (raw JSON
            # absent, the path taken by every _run_mcp_client-based env, which
            # unwraps internally) pass through unchanged, so this is a no-op for
            # them.
            error = exc
            if _envelope_from_mcp_error(exc) is not None:
                from tests.harness._base import _unwrap_mcp_tool_error

                error = _unwrap_mcp_tool_error(exc)
            return TransportResult(
                error=error,
                wire_error_envelope=wire,
                # What production WOULD emit for the same exception — see the
                # ImplDispatcher caveat; never a substitute for the wire field.
                synthesized_error_envelope=_envelope_from_adcp_error(exc),
            )
        # Real MCP wire: the structured_content dict stashed by _run_mcp_client
        # (declared on BaseTestEnv, reset per call_via — read directly).
        return TransportResult(
            payload=payload,
            envelope={"transport": "mcp"},
            wire_response=env._last_wire_response,
        )


class RestE2EDispatcher:
    """Dispatch via real HTTP through nginx to the Docker stack.

    Uses httpx to send POST requests to the live server, exercising the full
    stack: nginx -> UnifiedAuthMiddleware -> resolve_identity() ->
    get_principal_from_token() DB lookup -> route handler -> _impl().

    Reuses the env's REST contract (build_rest_body / REST_ENDPOINT /
    parse_rest_response / parse_rest_error); the only e2e-specific dependency is
    ``env.e2e_config`` (base_url of the live stack), set by the bdd conftest for
    E2E scenarios. Ported from feature/media-buy-refactoring (PR #1360 lineage).
    """

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        import httpx

        from tests.harness.transport import WireAuth

        if not env.e2e_config:
            return TransportResult(error=RuntimeError("E2E dispatch requires env.e2e_config (pass e2e_config= to env)"))

        identity = kwargs.pop("identity", None)
        base_url = env.e2e_config.base_url

        # identity=None means "send without auth headers" (no-auth test) — let the
        # server's auth middleware return 401/structured error. When identity exists
        # but auth_token is None (principal_id=None boundary tests), omit the header
        # so the server rejects gracefully instead of httpx raising on a None header.
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if isinstance(identity, WireAuth):
            headers.update(identity.headers)
        elif identity is not None:
            if identity.auth_token is not None:
                headers["x-adcp-auth"] = identity.auth_token
            tenant = getattr(identity, "tenant", None)
            if tenant is not None:
                subdomain = tenant.get("subdomain") if isinstance(tenant, dict) else getattr(tenant, "subdomain", None)
                if subdomain is not None:
                    headers["x-adcp-tenant"] = subdomain
            tc = getattr(identity, "testing_context", None)
            if tc is not None and getattr(tc, "dry_run", False):
                headers["x-dry-run"] = "true"

        body = env.build_rest_body(**kwargs)
        endpoint = env.REST_ENDPOINT  # type: ignore[attr-defined]

        with httpx.Client(base_url=base_url, timeout=30) as client:
            method = getattr(env, "REST_METHOD", "post")
            request_kwargs: dict[str, Any] = {"headers": headers}
            if method == "get":
                request_kwargs["params"] = body
            else:
                request_kwargs["json"] = body
            response = getattr(client, method)(endpoint, **request_kwargs)

        envelope = {
            "transport": "e2e_rest",
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type", ""),
        }

        if response.status_code >= 400:
            try:
                body = response.json()
            except Exception:
                # Non-JSON error (e.g. 500 with empty body) — wrap as AdCPError so
                # Then steps detect the error type and xfail spec-production gaps.
                # No wire_error_envelope: there is no structured body to expose, and
                # the INTERNAL_ERROR/5xx shape lets the "invalid" Then-step tell a
                # server crash apart from a real validation rejection (#1420).
                from src.core.exceptions import AdCPError

                body_text = response.text or "(empty body)"
                error = AdCPError(
                    f"HTTP {response.status_code}: {body_text}",
                    details={"status_code": response.status_code, "raw_body": body_text},
                )
                error.status_code = response.status_code
                return TransportResult(payload=None, envelope=envelope, error=error, raw_response=response)
            # Structured JSON error: mirror the in-process RestDispatcher and expose
            # the raw two-layer body as wire_error_envelope so error Then-steps assert
            # on the buyer-visible envelope (e.g. uc004 _assert_wire_rejection, or
            # assert_envelope_shape) instead of a lossy reconstructed exception. (#1420)
            error = env.parse_rest_error(response.status_code, body)
            return TransportResult(
                payload=None,
                envelope=envelope,
                error=error,
                wire_error_envelope=body,
                raw_response=response,
            )

        try:
            wire_response = response.json()
            # Parse a COPY — same pristine-wire guarantee as the in-process
            # RestDispatcher (parsers strip envelope keys in place, #1417).
            payload = env.parse_rest_response(copy.deepcopy(wire_response))
        except Exception as exc:
            return TransportResult(payload=None, envelope=envelope, error=exc, raw_response=response)

        # Expose the serialized success body as wire_response (parallel to
        # wire_error_envelope on the error path) so success Then-steps assert on the
        # real HTTP body rather than re-deriving from the typed payload (#rlgl.3).
        return TransportResult(
            payload=payload,
            envelope=envelope,
            error=None,
            wire_response=wire_response,
            raw_response=response,
        )


class McpE2EDispatcher:
    """Placeholder for real MCP E2E dispatch (not yet implemented)."""

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        raise NotImplementedError(
            "E2E_MCP dispatcher is not yet implemented. Use Transport.MCP for in-process MCP dispatch."
        )


class A2AE2EDispatcher:
    """Placeholder for real A2A E2E dispatch (not yet implemented)."""

    def dispatch(self, env: BaseTestEnv, **kwargs: Any) -> TransportResult:
        raise NotImplementedError(
            "E2E_A2A dispatcher is not yet implemented. Use Transport.A2A for in-process A2A dispatch."
        )


DISPATCHERS: dict[
    Transport,
    ImplDispatcher
    | A2ADispatcher
    | RestDispatcher
    | McpDispatcher
    | RestE2EDispatcher
    | McpE2EDispatcher
    | A2AE2EDispatcher,
] = {
    Transport.IMPL: ImplDispatcher(),
    Transport.A2A: A2ADispatcher(),
    Transport.REST: RestDispatcher(),
    Transport.MCP: McpDispatcher(),
    Transport.E2E_REST: RestE2EDispatcher(),
    Transport.E2E_MCP: McpE2EDispatcher(),
    Transport.E2E_A2A: A2AE2EDispatcher(),
}
