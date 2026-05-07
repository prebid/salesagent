"""Starlette ASGI middleware for inbound RFC 9421 signature verification.

Per-buyer-agent trust model. Each :class:`Principal` optionally carries a
``brand_domain`` (operator-typed buyer domain — the trust anchor) plus a
``signing_required`` flag. When the verifier sees a signature, it walks
``https://<brand_domain>/.well-known/brand.json`` via
:class:`adcp.signing.BrandJsonJwksResolver`, which auto-refreshes on
cooldown + unknown-kid cascade so JWKS rotation propagates without
operator action.

Enforcement is per-principal — there is no tenant-wide kill switch and no
per-operation gating list. An operator marks an individual buyer agent as
"must sign" by setting ``signing_required=True`` on their principal row.

Buffers the request body up front (so the verifier reads digest-covered body
AND downstream handlers see the same bytes via a replay-receive callable),
extracts the AdCP operation name, then:

* Signed request → verify against ``brand.json`` (walked per-request,
  library-cached) or 401.
* Unsigned request from principal with ``signing_required=True`` → 401
  ``request_signature_required``.
* Anything else → pass through (bearer auth handles the principal lookup).

Verifier tuning (skew window, replay window, content-digest policy) is
deployment-level, read from ``ADCP_SIGNING_*`` env vars on construction.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Literal

from adcp.signing import (
    SignatureVerificationError,
    StaticJwksResolver,
    VerifierCapability,
    VerifyOptions,
    parse_signature_input_header,
    unauthorized_response_headers,
    verify_starlette_request,
)
from adcp.signing.errors import (
    REQUEST_SIGNATURE_HEADER_MALFORMED,
    REQUEST_SIGNATURE_KEY_UNKNOWN,
    REQUEST_SIGNATURE_REQUIRED,
)

logger = logging.getLogger(__name__)


# Paths the verifier acts on. AdminWSGIMount runs first in the asgi_middleware
# list and short-circuits /admin, /static, /auth, /api, /tenant, /health, etc.
# — so anything reaching this middleware is either /mcp/* or A2A traffic at /.
_BUYER_PROTOCOL_PREFIXES: tuple[str, ...] = ("/mcp", "/a2a")

# Body buffering cap. AdCP requests are JSON-RPC envelopes — well under 1MB
# in practice. Cap at 4MB so a malicious unbounded body can't OOM the worker
# while we buffer for parsing.
MAX_BUFFERED_BODY_BYTES = 4 * 1024 * 1024


def _is_buyer_protocol_path(path: str) -> bool:
    """True if ``path`` is a buyer-protocol POST target.

    ``/`` (A2A root) is included; ``/.well-known/*`` is excluded so AAO
    discovery + agent-card fetches stay unsigned.
    """
    if path == "/":
        return True
    if path.startswith("/.well-known"):
        return False
    for prefix in _BUYER_PROTOCOL_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def _peek_kid_from_signature_input(headers: dict[str, str]) -> str | None:
    """Extract ``keyid`` from the ``Signature-Input`` header, or ``None`` on parse failure."""
    raw = None
    for k, v in headers.items():
        if k.lower() == "signature-input":
            raw = v
            break
    if raw is None:
        return None
    try:
        labels = parse_signature_input_header(raw)
    except (ValueError, KeyError):
        return None
    if not labels:
        return None
    parsed = next(iter(labels.values()))
    keyid = parsed.params.get("keyid")
    return str(keyid) if keyid is not None else None


def _decode_scope_headers(scope: dict) -> dict[str, str]:
    """Convert ASGI scope ``[(b"name", b"value"), ...]`` to ``{name: value}``.

    Both keys and values are latin-1 decoded; keys are NOT lowercased here —
    callers that need case-insensitive lookup should use the headers dict
    case-insensitively.
    """
    out: dict[str, str] = {}
    for name, value in scope.get("headers", []):
        out[name.decode("latin-1")] = value.decode("latin-1")
    return out


def _has_signature_headers(scope: dict) -> bool:
    """Cheap pre-check: return True only if both Signature + Signature-Input are present."""
    seen_sig = False
    seen_sig_input = False
    for name, _value in scope.get("headers", []):
        lname = name.decode("latin-1").lower() if isinstance(name, bytes) else name.lower()
        if lname == "signature":
            seen_sig = True
        elif lname == "signature-input":
            seen_sig_input = True
        if seen_sig and seen_sig_input:
            return True
    return False


async def _read_body(receive: Any, *, max_bytes: int = MAX_BUFFERED_BODY_BYTES) -> bytes:
    """Drain the ASGI ``receive`` into a single ``bytes`` blob, capped.

    The verifier needs the raw body for digest checking AND downstream handlers
    need it for their own parsing. We buffer once, then use :func:`_replay_receive`
    to re-emit the bytes.
    """
    body = bytearray()
    while True:
        msg = await receive()
        if msg["type"] != "http.request":
            # http.disconnect or anything else — bail; the connection's gone.
            break
        chunk = msg.get("body", b"")
        body.extend(chunk)
        if len(body) > max_bytes:
            raise ValueError(
                f"request body exceeded buffering cap of {max_bytes} bytes",
            )
        if not msg.get("more_body", False):
            break
    return bytes(body)


def _replay_receive(body: bytes) -> Any:
    """Return an ASGI ``receive`` callable that yields ``body`` once then closes.

    Downstream handlers that call ``await receive()`` get the buffered body;
    the next call returns an empty ``http.disconnect`` to signal EOF.
    """
    sent = False
    closed = False

    async def replay() -> dict:
        nonlocal sent, closed
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        if not closed:
            closed = True
            return {"type": "http.disconnect"}
        # ASGI doesn't define repeated receive after disconnect; mimic Starlette's
        # behavior of returning the disconnect indefinitely so callers don't hang.
        return {"type": "http.disconnect"}

    return replay


def _extract_operation(path: str, body: bytes) -> str | None:
    """Best-effort extraction of the AdCP operation name from the request body.

    MCP (JSON-RPC over HTTP at ``/mcp/*``):
        ``{"jsonrpc":"2.0","method":"tools/call","params":{"name":"<op>", ...}}``
        Returns ``<op>`` (e.g. ``create_media_buy``).

    A2A (at ``/`` per AdCP convention):
        Messages may carry a ``method`` or ``skill`` field naming the operation.
        We try a few common shapes; on miss, return ``None`` and the caller
        can decide whether to enforce against the empty-operation case.

    Returns:
        The operation name when extractable, else ``None``.
    """
    if not body:
        return None
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None

    # MCP JSON-RPC tools/call envelope
    method = payload.get("method")
    if method == "tools/call":
        params = payload.get("params") or {}
        if isinstance(params, dict):
            name = params.get("name")
            if isinstance(name, str):
                return name

    # A2A: ``method`` is the skill name when no JSON-RPC envelope wraps it.
    if isinstance(method, str) and method != "tools/call":
        return method

    # A2A v0.3+: messages with ``skill`` / ``message_type`` fields.
    for key in ("skill", "message_type", "operation"):
        value = payload.get(key)
        if isinstance(value, str):
            return value

    return None


class SigningVerifyMiddleware:
    """Verify RFC 9421 signatures on inbound buyer-protocol requests.

    Mounted in :func:`core.main.main` via ``serve(asgi_middleware=[...])``.
    Place AFTER ``AdminWSGIMount`` and ``SubdomainTenantMiddleware`` so it
    only sees buyer-protocol traffic that's already been tenant-routed.
    """

    def __init__(
        self,
        app: Any,
        *,
        max_skew_seconds: int | None = None,
        max_window_seconds: int | None = None,
        covers_digest: Literal["required", "forbidden", "either"] | None = None,
    ) -> None:
        import os

        self.app = app
        self._max_skew = (
            max_skew_seconds
            if max_skew_seconds is not None
            else int(os.environ.get("ADCP_SIGNING_MAX_SKEW_SECONDS", "60"))
        )
        self._max_window = (
            max_window_seconds
            if max_window_seconds is not None
            else int(os.environ.get("ADCP_SIGNING_MAX_WINDOW_SECONDS", "300"))
        )
        # Narrow the env-var read to VerifierCapability's Literal so mypy is
        # happy at the call site. Reject anything outside the spec set.
        env_digest = covers_digest or os.environ.get("ADCP_SIGNING_COVERS_DIGEST", "either")
        if env_digest not in ("required", "forbidden", "either"):
            raise ValueError(
                f"ADCP_SIGNING_COVERS_DIGEST must be one of 'required'/'forbidden'/'either' (got {env_digest!r})"
            )
        self._covers_digest: Literal["required", "forbidden", "either"] = env_digest  # type: ignore[assignment]

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not _is_buyer_protocol_path(path):
            await self.app(scope, receive, send)
            return

        # Always reset the verified-state contextvar at the START of every
        # buyer-protocol request. ContextVar values can leak across requests
        # when ASGI servers reuse asyncio tasks (HTTP/1.1 keep-alive, FastMCP
        # task pooling, lifespan-driven test harnesses) — a verified state
        # from request N must not be observable on request N+1. Defense in
        # depth: we also use a reset token to restore on exit.
        from src.core.signing.verified_state import _verified_state

        token = _verified_state.set(None)
        try:
            await self._dispatch(scope, receive, send, path)
        finally:
            _verified_state.reset(token)

    async def _dispatch(self, scope: dict, receive: Any, send: Any, path: str) -> None:
        # Buffer the body once. The verifier reads it for digest coverage AND
        # downstream handlers need the same bytes — we re-emit via _replay_receive.
        try:
            body = await _read_body(receive)
        except ValueError:
            await self._send_413(send)
            return
        replayed = _replay_receive(body)

        operation = _extract_operation(path, body)
        signed = _has_signature_headers(scope)

        # Resolve per-principal signing config. Returns ``None`` if we can't
        # even identify a tenant.
        try:
            ctx = await asyncio.to_thread(self._resolve_principal_context_sync, scope)
        except Exception:
            logger.exception("signing principal lookup crashed")
            ctx = None

        tenant_id = ctx.get("tenant_id") if ctx else None
        principal_id = ctx.get("principal_id") if ctx else None
        agent_url = ctx.get("agent_url") if ctx else None
        brand_domain = ctx.get("brand_domain") if ctx else None
        signing_required = bool(ctx and ctx.get("signing_required"))

        # Per-principal enforcement: if the principal is marked must-sign,
        # an unsigned request is rejected. signing_required without
        # brand_domain is a misconfiguration — also reject (fail closed).
        if signing_required and not signed:
            await self._send_401(
                send,
                SignatureVerificationError(
                    REQUEST_SIGNATURE_REQUIRED,
                    step=0,
                    message=(
                        "principal requires signed requests"
                        if brand_domain
                        else "principal marked signing_required but has no brand_domain"
                    ),
                ),
            )
            return

        if not signed:
            # No signature on the wire and no enforcement triggered — pass
            # through. Bearer auth (downstream) handles principal identification.
            await self.app(scope, replayed, send)
            return

        # Signature is present. Must verify or reject — no silent-drop path.
        # If we have no brand_domain (bearer-only principal sent a signature,
        # or tenant lookup failed), we have no trust root: reject.
        if brand_domain is None or tenant_id is None or principal_id is None:
            await self._send_401(
                send,
                SignatureVerificationError(
                    REQUEST_SIGNATURE_REQUIRED,
                    step=0,
                    message="signed request received but principal has no brand_domain",
                ),
            )
            return

        try:
            verified = await self._verify(scope, body, tenant_id, principal_id, brand_domain, agent_url, operation)
        except SignatureVerificationError as exc:
            await self._send_401(send, exc)
            return
        except Exception:
            logger.exception("signing verifier crashed on signed request")
            await self._send_401(
                send,
                SignatureVerificationError(
                    REQUEST_SIGNATURE_REQUIRED,
                    step=0,
                    message="verifier crashed; refusing signed request",
                ),
            )
            return

        if verified is not None:
            # Defense against in-flight brand_domain rotation: re-read the
            # principal's brand_domain from the DB and reject if it diverged
            # from the value we verified against. Closes a race where the
            # operator switches trust root mid-verify (after we've already
            # walked brand.json against the old domain). Without this, a
            # concurrent operator edit pointing at a legit domain would let
            # the in-flight verify complete with verified_state set against
            # attacker-controlled keys from the OLD domain. The conditional
            # UPDATE in _record_verified_timestamp_sync only protects the
            # cache column; the request itself needs this gate.
            try:
                live_brand_domain = await asyncio.to_thread(
                    self._read_principal_brand_domain_sync, tenant_id, principal_id
                )
            except Exception:
                logger.exception("brand_domain re-read crashed; rejecting signed request")
                await self._send_401(
                    send,
                    SignatureVerificationError(
                        REQUEST_SIGNATURE_REQUIRED,
                        step=0,
                        message="brand_domain re-check crashed; refusing signed request",
                    ),
                )
                return
            if live_brand_domain != brand_domain:
                logger.info(
                    "brand_domain changed mid-verify (%s -> %s); rejecting",
                    brand_domain,
                    live_brand_domain,
                )
                await self._send_401(
                    send,
                    SignatureVerificationError(
                        REQUEST_SIGNATURE_REQUIRED,
                        step=0,
                        message="brand_domain changed during verify; rejecting signed request",
                    ),
                )
                return

            from src.core.signing.verified_state import (
                VerifiedRequestState,
                set_verified_state,
            )

            state = scope.setdefault("state", {})
            state["verified_principal_id"] = verified["principal_id"]
            state["verified_agent_url"] = verified["agent_url"]
            state["verified_key_id"] = verified["key_id"]

            set_verified_state(
                VerifiedRequestState(
                    principal_id=verified["principal_id"],
                    agent_url=verified["agent_url"],
                    key_id=verified["key_id"],
                )
            )

            # Cache the verification timestamp on the principal row so the
            # admin UI's strict-admit guard can read a single column. Three
            # belt-and-suspenders properties on this write:
            #
            # 1. Race-safe: the UPDATE is conditional on
            #    ``brand_domain == :verified_brand_domain`` — if the operator
            #    changed brand_domain mid-flight, the WHERE filters out and
            #    we don't stamp evidence against the new (un-verified) trust
            #    root.
            # 2. Write-amplification-safe: we only stamp when the existing
            #    timestamp is older than ~60s. A buyer doing 100 req/sec
            #    triggers 1 UPDATE/min/principal instead of 100/sec.
            # 3. Best-effort: a failed update here must never block the
            #    verified request from proceeding.
            if tenant_id and principal_id and brand_domain:
                try:
                    await asyncio.to_thread(
                        self._record_verified_timestamp_sync,
                        tenant_id,
                        principal_id,
                        brand_domain,
                    )
                except Exception:
                    logger.exception("failed to cache last_signed_verified_at")

        await self.app(scope, replayed, send)

    # Don't UPDATE the cache column on every signed request — collapses
    # write amplification under high-traffic verifies. Cache-skew of up to
    # this window is fine for the strict-admit guard (it's UX, not auth).
    _CACHE_REFRESH_WINDOW_SECONDS = 60

    def _record_verified_timestamp_sync(self, tenant_id: str, principal_id: str, verified_brand_domain: str) -> None:
        """Stamp ``principals.last_signed_verified_at = now()`` for this caller.

        Run inside ``asyncio.to_thread`` after a successful verify. The UPDATE
        is conditional on ``brand_domain == :verified_brand_domain`` so a
        concurrent operator brand_domain change doesn't get stamped with
        stale evidence (race fix). Skipped entirely when an existing stamp
        is younger than ``_CACHE_REFRESH_WINDOW_SECONDS`` (write-storm fix).
        """
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import select, update

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Principal

        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=self._CACHE_REFRESH_WINDOW_SECONDS)

        with get_db_session() as session:
            current_ts = session.scalars(
                select(Principal.last_signed_verified_at).filter_by(tenant_id=tenant_id, principal_id=principal_id)
            ).first()
            # Skip the UPDATE entirely if a recent stamp already covers us.
            if current_ts is not None and current_ts >= cutoff:
                return
            session.execute(
                update(Principal)
                .where(Principal.tenant_id == tenant_id)
                .where(Principal.principal_id == principal_id)
                .where(Principal.brand_domain == verified_brand_domain)
                .values(last_signed_verified_at=now)
            )
            session.commit()

    def _read_principal_brand_domain_sync(self, tenant_id: str, principal_id: str) -> str | None:
        """Re-read ``principals.brand_domain`` post-verify.

        Run inside ``asyncio.to_thread`` after the verifier succeeds. Returns
        the current value so the caller can compare against the value used
        for verification — if it changed mid-flight, the verify is treated
        as failed and the request is rejected. Closes the operator-rotates-
        brand-domain-mid-verify race where attacker-controlled keys from
        the old domain would otherwise produce a verified-state for the
        new (legit) trust root.
        """
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Principal

        with get_db_session() as session:
            return session.scalars(
                select(Principal.brand_domain).filter_by(tenant_id=tenant_id, principal_id=principal_id)
            ).first()

    def _resolve_principal_context_sync(self, scope: dict) -> dict[str, Any] | None:
        """Sync DB lookup of the calling principal's signing config.

        Run inside ``asyncio.to_thread``. Returns a context dict whenever the
        tenant resolves; ``agent_url`` / ``signing_required`` / ``principal_id``
        are populated only when the bearer maps to a known principal.

        Shape::

            {
              "tenant_id": str,
              "principal_id": str | None,
              "agent_url": str | None,
              "signing_required": bool,
            }

        Returns ``None`` only when we couldn't even identify a tenant.
        """
        from sqlalchemy import select

        from src.core.database.database_session import get_db_session
        from src.core.database.models import Principal, Tenant
        from src.core.resolved_identity import _detect_tenant, _extract_auth_token

        headers: dict[str, str] = {}
        for name, value in scope.get("headers", []):
            headers[name.decode("latin-1")] = value.decode("latin-1")

        tenant_id, _ = _detect_tenant(headers)
        if not tenant_id:
            return None
        token, _ = _extract_auth_token(headers)

        principal_id: str | None = None
        if token:
            from src.core.auth_utils import get_principal_from_token

            principal_id, _ = get_principal_from_token(token, tenant_id)

        with get_db_session() as session:
            tenant_row = session.scalars(select(Tenant).filter_by(tenant_id=tenant_id)).first()
            if tenant_row is None:
                return None

            ctx: dict[str, Any] = {
                "tenant_id": tenant_id,
                "principal_id": principal_id,
                "agent_url": None,
                "brand_domain": None,
                "signing_required": False,
            }

            if not principal_id:
                return ctx

            principal = session.scalars(
                select(Principal).filter_by(tenant_id=tenant_id, principal_id=principal_id)
            ).first()
            if principal is None:
                return ctx
            ctx["agent_url"] = principal.agent_url
            ctx["brand_domain"] = principal.brand_domain
            ctx["signing_required"] = bool(principal.signing_required)
            return ctx

    async def _verify(
        self,
        scope: dict,
        body: bytes,
        tenant_id: str,
        principal_id: str,
        brand_domain: str,
        agent_url: str | None,
        operation: str | None,
    ) -> dict[str, str] | None:
        """Run the verifier checklist. Return verified state or raise.

        Walks the buyer's brand.json via :class:`BrandJsonJwksResolver` —
        the library handles cooldown re-walks + unknown-kid cascade, so
        rotation of ``agent_url`` or ``jwks_uri`` in brand.json propagates
        without operator action. ``agent_url`` here is informational only
        (audit-log stamping); the trust root is ``brand_domain``.
        """
        from src.core.signing import get_buyer_agent_jwks_cache, get_replay_store

        headers = _decode_scope_headers(scope)

        cache = get_buyer_agent_jwks_cache()
        async_brand_resolver = cache.resolver_for(tenant_id, principal_id, brand_domain)
        replay_store = get_replay_store()

        kid = _peek_kid_from_signature_input(headers)
        if kid is None:
            raise SignatureVerificationError(
                REQUEST_SIGNATURE_HEADER_MALFORMED,
                step=1,
                message="could not parse keyid from Signature-Input",
            )
        # BrandJsonJwksResolver is async + handles cooldown + unknown-kid
        # cascade internally. ``await resolver(kid)`` triggers a brand.json
        # re-walk if the kid hasn't been seen, so JWKS rotation propagates
        # without operator action.
        jwk = await async_brand_resolver(kid)
        if jwk is None:
            raise SignatureVerificationError(
                REQUEST_SIGNATURE_KEY_UNKNOWN,
                step=7,
                message=f"kid {kid!r} not found in JWKS resolved from {brand_domain}/.well-known/brand.json",
            )
        sync_resolver = StaticJwksResolver({"keys": [jwk]})

        capability = VerifierCapability(
            supported=True,
            covers_content_digest=self._covers_digest,
            required_for=frozenset(),
            supported_for=frozenset(),
        )

        from starlette.requests import Request

        verify_receive = _replay_receive(body)
        request: Request = Request(scope, verify_receive)

        options = VerifyOptions(
            now=time.time(),
            capability=capability,
            operation=operation or "unknown",
            jwks_resolver=sync_resolver,
            replay_store=replay_store,
            max_skew_seconds=self._max_skew,
            max_window_seconds=self._max_window,
        )

        signer = await verify_starlette_request(request, options=options)

        # Prefer the resolver's live agent_url (last known good from
        # brand.json) over the cached column — the column is informational
        # and may lag rotation. Falls back to the column when the resolver
        # hasn't snapshotted yet (cold start / failure).
        live_agent_url = async_brand_resolver.agent_url or agent_url or ""
        return {
            "principal_id": principal_id,
            "agent_url": live_agent_url,
            "key_id": signer.key_id,
        }

    async def _send_401(self, send: Any, exc: SignatureVerificationError) -> None:
        """Emit the spec-mandated 401 + ``WWW-Authenticate`` header."""
        headers = unauthorized_response_headers(exc)
        body = b'{"error":{"code":"' + exc.code.encode("ascii") + b'","message":"signature verification failed"}}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    *((k.encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def _send_413(self, send: Any) -> None:
        """Reject oversized bodies before any verification work."""
        body = b'{"error":{"code":"request_body_too_large","message":"body exceeds signing-buffer cap"}}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})
