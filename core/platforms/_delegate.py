"""Bridge from the framework's :class:`RequestContext` to the existing
salesagent ``src/core/tools/*`` _impl functions.

Greenfield ``core/`` platforms are thin AdCP-shape adapters; the brain
stays in ``src/``. Each ``DecisioningPlatform`` method on
:class:`MockSellerPlatform` / :class:`GamPlatform`
delegates here, which:

1. Builds a :class:`ResolvedIdentity` from ``ctx`` (principal_id +
   tenant_id from the framework's auth/account chain, tenant dict
   loaded by the existing config_loader).
2. Coerces the request into the Pydantic model the impl wants.
3. Calls the existing _impl from ``src/core/tools/*``.
4. Projects the response back to a wire dict the framework can
   serialize.

When a SalesAgentProposalManager lands (#38), the get_products
delegation moves into ``ProposalManager.create_proposal`` — at that
point the platform method becomes a one-liner that calls into the
proposal manager. Until then this is the right shape: reuse
``_get_products_impl`` directly without making it implicit-state.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from adcp.decisioning import AdcpError, RequestContext
from adcp.server import UnsupportedVersionError, current_transport, resolve_requested_adcp_version
from adcp.server.auth import current_principal
from pydantic import BaseModel, ValidationError

from src.core.config_loader import get_tenant_by_id
from src.core.exceptions import AdCPError
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    CreateMediaBuyRequest,
    GetMediaBuyDeliveryRequest,
    GetMediaBuysRequest,
    GetProductsRequest,
    GetSignalsRequest,
    ListCreativeFormatsRequest,
    UpdateMediaBuyRequest,
)
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.creative_formats import _list_creative_formats_impl
from src.core.tools.creatives._sync import _sync_creatives_impl
from src.core.tools.creatives.listing import _list_creatives_impl
from src.core.tools.media_buy_create import _create_media_buy_impl
from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl
from src.core.tools.media_buy_list import _get_media_buys_impl
from src.core.tools.media_buy_update import _update_media_buy_impl
from src.core.tools.products import _get_products_impl
from src.core.tools.signals import _get_signals_impl

logger = logging.getLogger(__name__)

# Release-precision AdCP versions this agent serves on the native v3 surface.
SUPPORTED_ADCP_VERSIONS: tuple[str, ...] = ("3.0", "3.1-beta.3")

# Process-singleton guard so the misconfig WARNING (see _build_identity)
# fires once per process rather than once per request — repeated logs
# add noise without adding signal once the operator has seen it. One
# warning per worker is a feature for multi-proc deploys: each worker
# independently confirms its own middleware chain.
_TRANSPORT_FALLBACK_WARNED: bool = False


def _build_identity(ctx: RequestContext[Any]) -> ResolvedIdentity:
    """Build a :class:`ResolvedIdentity` from a framework
    :class:`RequestContext`. Used by every delegated _impl call.

    Raises :class:`AdcpError` ``ACCOUNT_NOT_FOUND`` when the auth
    chain didn't populate ``ctx.account.metadata.tenant_id`` — that's
    a wiring bug in serve()'s asgi_middleware, not a buyer error.
    """
    tenant_id = ctx.account.metadata.get("tenant_id") if ctx.account else None
    if not tenant_id:
        raise AdcpError(
            "ACCOUNT_NOT_FOUND",
            message=(
                "ctx.account.metadata.tenant_id missing — auth chain didn't "
                "populate it. Check SubdomainTenantMiddleware wiring."
            ),
            recovery="terminal",
            field="account",
        )

    # The salesagent _impl functions read tenant policy fields off the
    # tenant dict (brand_manifest_policy, advertising_policy, etc.), so
    # we hydrate via the existing config_loader rather than passing the
    # bare framework Tenant object.
    tenant_dict = get_tenant_by_id(tenant_id)

    # ctx.caller_identity is the framework's COMPOSITE cache-scope key
    # (``<module>.<qualname>:<account_id>``) used by idempotency
    # middleware — NOT the bare principal_id. salesagent's
    # _create_media_buy_impl looks up the Principal row by id and the
    # composite would 404. The bare principal_id lives on the
    # ``current_principal`` contextvar that BearerTokenAuthMiddleware
    # set; it propagates via Python's native asyncio context so we
    # can read it from the dispatched _impl without any threading.
    principal_id = current_principal.get() or getattr(ctx, "auth_principal", None)

    # Inbound transport drives webhook payload shape: A2A buyers receive
    # ``Task``/``TaskStatusUpdateEvent``, MCP buyers receive
    # ``McpWebhookPayload``. adcp 5.0 exposes ``adcp.server.current_transport``
    # (#627): the dispatcher itself populates this ContextVar based on which
    # transport invoked the handler. Falls back to "mcp" when unset (lifespan
    # events, unit-test harness paths, admin requests that somehow reach here).
    ctx_transport = getattr(ctx, "transport", None)
    detected_transport = ctx_transport if ctx_transport in ("mcp", "a2a") else current_transport.get()
    if detected_transport in ("mcp", "a2a"):
        protocol: str = detected_transport
    else:
        protocol = "mcp"
        # Forward-compat guard: if the auth chain populated
        # ``current_principal`` (only set inside HTTP requests by
        # ``BearerTokenAuthMiddleware``) but transport detection didn't,
        # the SDK dispatcher chain is misconfigured — A2A buyers will silently
        # receive MCP-shaped webhooks. Surface once per process. Lifespan /
        # unit-test / admin paths don't populate ``current_principal``, so
        # they skip this branch. See #221 for the rationale.
        global _TRANSPORT_FALLBACK_WARNED
        if not _TRANSPORT_FALLBACK_WARNED and current_principal.get() is not None:
            _TRANSPORT_FALLBACK_WARNED = True
            logger.warning(
                "_build_identity falling back to protocol='mcp' inside an "
                "authenticated request scope — adcp.server.current_transport "
                "ContextVar was not set by the dispatcher. A2A buyers will "
                "silently receive MCP-shaped webhooks. See salesagent issue #221."
            )

    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant=tenant_dict,
        protocol=protocol,
        testing_context=AdCPTestContext(),
    )


def _coerce_to_request_model(req: Any, model_cls: type[BaseModel]) -> Any:
    """Coerce ``req`` (dict OR Pydantic model OR generated model) into
    the Pydantic model class the impl expects.

    When ``req`` is a different Pydantic model (e.g. the framework's
    library type) we dump it and filter to fields ``model_cls`` actually
    declares. The framework can inject default-valued fields that are
    in the spec but our impl-local schema deliberately doesn't expose
    (``include_snapshot``, ``include_history``, ``adcp_major_version``
    on ``GetMediaBuysRequest``) — those defaults would otherwise blow
    up dev-mode ``extra='forbid'`` validation and surface as
    INTERNAL_ERROR (#273).
    """
    if isinstance(req, model_cls):
        return req
    if isinstance(req, dict):
        return model_cls(**req)
    if hasattr(req, "model_dump"):
        dumped = req.model_dump(exclude_none=True)
        allowed = set(model_cls.model_fields.keys())
        filtered = {k: v for k, v in dumped.items() if k in allowed}
        return model_cls(**filtered)
    return model_cls.model_validate(req)


def _request_payload(req: Any) -> dict[str, Any]:
    """Return a dict-shaped request payload for version negotiation."""
    if isinstance(req, dict):
        return dict(req)
    if hasattr(req, "model_dump"):
        return req.model_dump(mode="json", exclude_none=True)
    return {}


def _resolve_requested_version(req: Any) -> str:
    """Resolve the AdCP release requested by the buyer.

    The SDK helper preserves the important compatibility contract:
    no ``adcp_version`` signal means legacy 3.0, while explicit 3.1 beta
    opts into the newer response envelope shape.
    """
    payload = _request_payload(req)
    try:
        return resolve_requested_adcp_version(payload, supported=SUPPORTED_ADCP_VERSIONS)
    except UnsupportedVersionError as exc:
        field = "adcp_version" if isinstance(exc.wire_value, str) else "adcp_major_version"
        raise AdcpError(
            "VERSION_UNSUPPORTED",
            message=str(exc),
            recovery="correctable",
            field=field,
            details={"supported_versions": list(exc.supported)},
        ) from exc


def _to_wire(
    response: Any,
    *,
    requested_adcp_version: str | None = None,
    tool_name: str | None = None,
) -> dict[str, Any]:
    """Project a Pydantic response model onto a wire dict the framework
    can serialize.

    Legacy impls return a success-shaped wrapper carrying ``errors=[...]``
    when validation fails; that path predates the AdCP 3.0.11 transport
    binding which projects rejections onto a singular ``adcp_error``
    envelope built by the framework dispatcher. Raise an :class:`AdcpError`
    here so the dispatcher's :func:`build_mcp_error_result` produces the
    spec envelope (``CallToolResult.isError=True`` + ``structuredContent.adcp_error``)
    and bypasses success-schema validation. Returning the legacy dict
    inline doesn't take that bypass — FastMCP would then reject the
    response on ``status='failed'`` not matching ``MediaBuyStatus``.
    """
    if isinstance(response, dict):
        wire = response
    elif hasattr(response, "model_dump"):
        wire = response.model_dump(mode="json", exclude_none=True)
    else:
        wire = dict(response)  # last-ditch coerce
    if requested_adcp_version is not None and tool_name is not None:
        wire = _adapt_response_for_requested_version(wire, requested_adcp_version, tool_name=tool_name)
    _maybe_raise_legacy_errors(wire)
    return wire


def _adapt_response_for_requested_version(
    wire: dict[str, Any],
    requested_adcp_version: str,
    *,
    tool_name: str,
) -> dict[str, Any]:
    """Project newer media-buy response fields back to the requested release."""
    if requested_adcp_version != "3.0" or tool_name not in {"create_media_buy", "update_media_buy"}:
        return wire
    if wire.get("status") != "completed" or "media_buy_status" not in wire:
        return wire

    adapted = dict(wire)
    media_buy_status = adapted.pop("media_buy_status")
    adapted["status"] = media_buy_status
    return adapted


# AdCP 3.0.11 standard error codes are uppercase snake_case. Some internal
# call sites still pass lowercase legacy strings (``"validation_error"``,
# ``"authentication_error"``, etc.) into the ``errors=[Error(code=...)]``
# slot — the boundary translator maps them to spec-canonical codes so
# buyer-side ``STANDARD_ERROR_CODES`` switches match.
#
# Targets MUST be members of ``error-code.json`` (the spec's
# :data:`STANDARD_ERROR_CODES` enum). ``AUTH_REQUIRED`` covers both
# missing and rejected credentials in 3.0.x — 3.1+ splits into
# ``AUTH_MISSING``/``AUTH_INVALID``, but emitting either today produces
# unknown-code handling on buyer agents. ``INVALID_REQUEST`` covers the
# GAM product-config rejection path because the buyer can recover by
# removing the misconfigured product from the request.
_LEGACY_CODE_REMAP: dict[str, str] = {
    "validation_error": "VALIDATION_ERROR",
    "authentication_error": "AUTH_REQUIRED",
    "invalid_configuration": "INVALID_REQUEST",
    "invalid_datetime": "VALIDATION_ERROR",
}


def _maybe_raise_legacy_errors(wire: dict[str, Any]) -> None:
    """Promote a legacy ``{"errors": [...], "status": "failed"}`` error-envelope
    wrapper to a framework :class:`AdcpError` raise so the dispatcher emits
    the spec ``adcp_error`` shape. Only the first entry is projected — the
    spec ``adcp_error`` is a single object.

    Discriminates against partial-success responses that legitimately
    carry an ``errors[]`` array alongside data (e.g.,
    ``GetMediaBuyDeliveryResponse`` returns per-buy errors with valid
    ``media_buy_deliveries`` and ``aggregated_totals``). The discriminator
    is ``status == "failed"`` — set by the impl's
    :class:`CreateMediaBuyResult` / :class:`UpdateMediaBuyResult`
    wrappers when the inner payload is a typed error variant. Without
    this guard, a partial-success delivery report would be incorrectly
    promoted to a hard error and the buyer would lose the successful
    rows.
    """
    if wire.get("status") != "failed":
        return
    errors = wire.get("errors")
    if not errors or not isinstance(errors, list):
        return
    first = errors[0] if isinstance(errors[0], dict) else {}
    code_raw = first.get("code") or "INTERNAL_ERROR"
    code = _LEGACY_CODE_REMAP.get(code_raw, code_raw) if isinstance(code_raw, str) else "INTERNAL_ERROR"
    field = first.get("field") if isinstance(first.get("field"), str) else None
    details = first.get("details") if isinstance(first.get("details"), dict) else None
    raise AdcpError(
        code,
        message=first.get("message") or code,
        recovery=first.get("recovery") or "correctable",
        field=field,
        details=details,
    )


# AdCP major versions this agent serves. Single source of truth — imported
# by :func:`core.main.build_router` to populate ``DecisioningCapabilities.adcp.major_versions``
# so the wire declaration and the runtime check cannot drift. The check helper
# below rejects request payloads carrying an out-of-set value with
# ``VERSION_UNSUPPORTED`` per spec — without this check, buyers sending a future
# ``adcp_major_version`` silently get an old-protocol response and retry forever.
SUPPORTED_MAJOR_VERSIONS: frozenset[int] = frozenset({3})
# Legacy alias retained for internal callers; public name is uppercase.
_SUPPORTED_MAJOR_VERSIONS = SUPPORTED_MAJOR_VERSIONS


def _check_major_version(req: Any) -> None:
    """Raise ``VERSION_UNSUPPORTED`` when a buyer's ``adcp_major_version`` is
    outside what this agent declares in capabilities. ``None``/missing is
    allowed — the spec says the seller assumes its highest supported version
    when the field is omitted.

    Defensive: ignore non-int values (Mock objects in tests, malformed
    payloads). The spec types ``adcp_major_version`` as ``int | None`` and
    the SDK validates wire requests against that model before they reach
    here, so anything else is either test scaffolding or a corner case
    upstream — raising here would surface as a confusing version error
    when the real failure is elsewhere.
    """
    if isinstance(req, dict):
        version = req.get("adcp_major_version")
    else:
        version = getattr(req, "adcp_major_version", None)
    # bool is an int subclass — reject it explicitly so True/False on a
    # mock-shaped request isn't read as version 1/0.
    if version is None or isinstance(version, bool) or not isinstance(version, int):
        return
    if version in _SUPPORTED_MAJOR_VERSIONS:
        return
    raise AdcpError(
        "VERSION_UNSUPPORTED",
        message=(
            f"adcp_major_version={version!r} is not supported by this agent. "
            f"Supported versions: {sorted(_SUPPORTED_MAJOR_VERSIONS)}."
        ),
        recovery="correctable",
        field="adcp_major_version",
        details={"supported_major_versions": sorted(_SUPPORTED_MAJOR_VERSIONS)},
    )


# Map salesagent recovery hints to the framework's wire vocabulary. Both
# strings exist in the AdcpError recovery enum, but framework callers default
# to ``"terminal"`` when unrecognized — pin our values explicitly.
_RECOVERY_HINT_MAP: dict[str, str] = {
    "transient": "transient",
    "correctable": "correctable",
    "terminal": "terminal",
}


def _translate_adcp_error(exc: AdCPError) -> AdcpError:
    """Translate a salesagent ``AdCPError`` into the framework's wire-shaped
    :class:`AdcpError` so the dispatcher projects ``error_code`` onto the
    AdCP error envelope's ``code`` field. Without this translation,
    salesagent's ``AdCPError`` reaches the framework as a generic
    ``Exception`` and gets coded as ``INTERNAL_ERROR``.
    """
    recovery = _RECOVERY_HINT_MAP.get(exc.recovery, "terminal")
    field: str | None = None
    details: dict[str, Any] | None = None
    if isinstance(exc.details, dict):
        details = dict(exc.details)
        # Hoist a top-level ``field`` key when the impl tucked it into details
        # — keeps the wire projection's ``field`` attribute populated without
        # adding another constructor parameter to AdCPError.
        candidate = details.pop("field", None)
        if isinstance(candidate, str):
            field = candidate
        if not details:
            details = None
    return AdcpError(
        exc.error_code,
        message=exc.message or str(exc),
        recovery=recovery,  # type: ignore[arg-type]
        field=field,
        details=details,
    )


def _translate_validation_error(exc: ValidationError) -> AdcpError:
    """Translate a pydantic :class:`ValidationError` raised during request
    coercion into the framework's wire-shaped :class:`AdcpError` with
    ``INVALID_REQUEST`` / ``recovery="correctable"``.

    Without this translation, a wire patch carrying a field our stricter
    request schema rejects (type mismatch, unknown field under
    ``extra='forbid'``, etc.) reaches the framework dispatcher as a bare
    ``ValidationError``, which gets wrapped as opaque ``INTERNAL_ERROR``
    ("Platform method 'X' raised ValidationError"). The spec-correct shape
    for a buyer-fixable bad request is ``INVALID_REQUEST`` with the offending
    field path so the buyer agent knows which field to repair — matching
    the body-validation rejection already produced upstream for spec-level
    schema violations.

    The first error's ``loc`` tuple is joined into a dotted field path
    (e.g. ``packages.0.budget``); ``details.errors`` carries the full
    pydantic error list so the buyer agent can repair every offending
    field in one round-trip.

    Pydantic's :meth:`pydantic.ValidationError.errors` returns dicts that
    can embed Python objects (raw user input, raised exception instances
    via ``ctx['error']``) — those are NOT JSON-serializable, so passing
    the raw list into ``AdcpError.details`` crashes the framework
    dispatcher's wire serializer with ``PydanticSerializationError:
    Unable to serialize unknown type: <class 'ValueError'>`` (#355).
    Strip with ``include_input=False, include_context=False,
    include_url=False`` to keep only JSON-safe primitives (loc, msg, type).
    """
    errors = exc.errors(include_input=False, include_context=False, include_url=False)
    field: str | None = None
    message: str = "Request validation failed"
    if errors:
        first = errors[0]
        loc = first.get("loc") or ()
        if loc:
            field = ".".join(str(part) for part in loc)
        msg_text = first.get("msg") or message
        if field:
            message = f"{field}: {msg_text}"
        else:
            message = str(msg_text)
    return AdcpError(
        "INVALID_REQUEST",
        message=message,
        recovery="correctable",
        field=field,
        details={"errors": errors} if errors else None,
    )


def translate_adcp_errors[DelegateFn: Callable[..., Awaitable[Any]]](fn: DelegateFn) -> DelegateFn:
    """Decorator that translates structured rejections raised by a delegate
    into the framework's wire-shaped :class:`AdcpError`.

    Catches two exception classes:

    * Salesagent ``AdCPError`` subclasses → translated by
      :func:`_translate_adcp_error` to preserve typed codes
      (``MEDIA_BUY_NOT_FOUND``, ``PACKAGE_NOT_FOUND``, ``TERMS_REJECTED``, etc.).
    * Pydantic ``ValidationError`` raised by request coercion → translated by
      :func:`_translate_validation_error` to ``INVALID_REQUEST`` with
      ``recovery="correctable"`` and the offending field path.

    Both the MCP and A2A dispatchers project the framework ``AdcpError`` onto
    the ``adcp_error`` envelope (MCP: ``CallToolResult.structuredContent``;
    A2A: ``Task.artifacts[0].parts[0].data``). Untranslated exceptions bubble
    through both dispatchers' generic ``except Exception`` and surface as
    opaque ``INTERNAL_ERROR`` — for ``ValidationError`` that masquerades as
    "Platform method 'X' raised ValidationError" on the wire, which is wrong
    for both transports (MCP should see ``INVALID_REQUEST``; A2A buyers see
    "Task failed" with no actionable signal).

    Wrapping the delegate — which is the single shared entry point used by
    both ``MockSellerPlatform`` and ``GamPlatform`` — guarantees both
    transports translate identically. New delegates inherit the translation
    by adding the decorator.
    """

    @functools.wraps(fn)
    async def _wrapper(*args: Any, **kwargs: Any) -> Any:
        # AdCP version negotiation runs before the impl: reject any
        # incoming ``adcp_major_version`` that's outside this agent's
        # declared major_versions before the impl burns work on a
        # request shaped for a different protocol. Different delegates
        # have different positional signatures (most are ``(req, ctx)``,
        # update_media_buy is ``(media_buy_id, patch, ctx)``) so probe
        # every non-scalar arg for the field rather than hard-coding
        # positions.
        for candidate in args:
            if hasattr(candidate, "adcp_major_version") or (
                isinstance(candidate, dict) and "adcp_major_version" in candidate
            ):
                _check_major_version(candidate)
        try:
            return await fn(*args, **kwargs)
        except AdCPError as exc:
            raise _translate_adcp_error(exc) from exc
        except ValidationError as exc:
            raise _translate_validation_error(exc) from exc

    return _wrapper  # type: ignore[return-value]


@translate_adcp_errors
async def _delegate_get_products(req: GetProductsRequest, ctx: RequestContext[Any]) -> dict[str, Any]:
    """Forward to ``src/core/tools/products.py:_get_products_impl``.

    Note: typed ``req: GetProductsRequest`` here documents intent but
    the SDK resolves ``params_model`` from the platform router's base
    class advertisement, not from this delegate or the platform
    subclass override. Dev-mode unknown-field rejection is restored at
    the pre-validation-hook boundary in ``core.main`` before the SDK's
    permissive library model can accept or drop extra fields.
    """
    identity = _build_identity(ctx)
    req_model = _coerce_to_request_model(req, GetProductsRequest)
    response = await _get_products_impl(req_model, identity)
    return _to_wire(response)


@translate_adcp_errors
async def _delegate_create_media_buy(req: Any, ctx: RequestContext[Any]) -> dict[str, Any]:
    """Forward to ``src/core/tools/media_buy_create.py:_create_media_buy_impl``.

    ``push_notification_config`` is a top-level AdCP request field that
    the impl reads as a separate kwarg (NOT off ``req``) — it registers
    a ``PushNotificationConfig`` DB row used by ``context_manager.
    _send_push_notifications`` to fire workflow-step webhooks. We
    extract it from ``req`` here and forward as a dict; without this,
    buyers who set ``push_notification_config`` on the request body
    silently get no completion webhooks.

    The framework's idempotency wrap on the caller layer scopes
    retries; the impl's own transactional semantics handle the
    create-once invariant.

    Salesagent ``AdCPError`` exceptions are translated by the
    :func:`translate_adcp_errors` decorator into the framework's
    :class:`AdcpError` so structured rejections (TERMS_REJECTED, etc.)
    project to the correct wire ``code`` instead of leaking through as
    generic ``INTERNAL_ERROR``.
    """
    identity = _build_identity(ctx)
    req_model = _coerce_to_request_model(req, CreateMediaBuyRequest)
    requested_adcp_version = _resolve_requested_version(req_model)
    pnc = req_model.push_notification_config
    pnc_dict: dict[str, Any] | None
    if pnc is None:
        pnc_dict = None
    elif hasattr(pnc, "model_dump"):
        pnc_dict = pnc.model_dump(mode="json", exclude_none=True)
    else:
        pnc_dict = dict(pnc)
    response = await _create_media_buy_impl(req_model, push_notification_config=pnc_dict, identity=identity)
    _emit_media_buy_created_if_success(identity.tenant_id, response)
    return _to_wire(response, requested_adcp_version=requested_adcp_version, tool_name="create_media_buy")


def _emit_media_buy_created_if_success(tenant_id: str, result: Any) -> None:
    """Fire ``media_buy.created`` when a buy was actually committed.

    The wrapper sits at the framework/_impl boundary (not inside _impl)
    so it can import the admin-layer publisher without violating the
    transport-agnostic _impl guard. ``CreateMediaBuyResult.response`` is
    a discriminated union — only the ``CreateMediaBuySuccess`` variant
    means a real ad-server row was created. ``CreateMediaBuySubmitted``
    is pending-approval (no media_buy yet) and ``CreateMediaBuyError``
    is failure — neither fires the event.

    Best-effort: webhook delivery failures don't propagate back to the
    buyer (the buy itself succeeded; the event is observability).
    """
    from src.core.schemas import CreateMediaBuySuccess

    inner = getattr(result, "response", None)
    if not isinstance(inner, CreateMediaBuySuccess):
        return

    media_buy_id = getattr(inner, "media_buy_id", None)
    if not media_buy_id:
        return

    from src.admin.services.webhook_publisher import emit_event

    emit_event(
        tenant_id,
        "media_buy.created",
        {
            "media_buy_id": media_buy_id,
            "buyer_ref": getattr(inner, "buyer_ref", None),
            "status": getattr(inner, "media_buy_status", None) or getattr(inner, "status", None),
        },
    )


@translate_adcp_errors
async def _delegate_update_media_buy(
    media_buy_id: str,
    patch: Any,
    ctx: RequestContext[Any],
) -> dict[str, Any]:
    """Forward to the sync ``_update_media_buy_impl`` via
    ``asyncio.to_thread`` so DB calls don't block the event loop.

    The framework's arg-projector unpacks update_media_buy into
    ``(media_buy_id, patch, ctx)`` separate kwargs (which is why
    @IdempotencyStore.wrap doesn't compose — see #559). We rebuild
    a single :class:`UpdateMediaBuyRequest` here for the impl, since
    the impl wants the unified wire shape.

    AdCPError translation runs in :func:`translate_adcp_errors`: the impl
    raises typed AdCPError subclasses (e.g. AdCPMediaBuyNotFoundError,
    AdCPPackageNotFoundError) for structured rejections, the decorator
    projects them onto the framework's ``AdcpError`` so the dispatcher
    emits a spec-compliant ``adcp_error.code`` envelope. Without this
    translation a typed not-found surfaces as an opaque INTERNAL_ERROR —
    losing the tenant-isolation guarantee that cross-tenant probing
    returns ``MEDIA_BUY_NOT_FOUND``, not ``AUTHORIZATION_ERROR``.
    """
    identity = _build_identity(ctx)
    if isinstance(patch, dict):
        patch_dict = dict(patch)
    elif hasattr(patch, "model_dump"):
        patch_dict = patch.model_dump(exclude_unset=True)
    else:
        patch_dict = dict(patch)
    patch_dict["media_buy_id"] = media_buy_id
    requested_adcp_version = _resolve_requested_version(patch_dict)
    req_model = _coerce_to_request_model(patch_dict, UpdateMediaBuyRequest)
    response = await asyncio.to_thread(_update_media_buy_impl, req_model, identity)
    return _to_wire(response, requested_adcp_version=requested_adcp_version, tool_name="update_media_buy")


@translate_adcp_errors
async def _delegate_sync_creatives(req: Any, ctx: RequestContext[Any]) -> dict[str, Any]:
    """Forward to ``src/core/tools/creatives/_sync.py:_sync_creatives_impl``.

    The impl takes individual kwargs (creatives, assignments,
    creative_ids, ...) rather than a single request model — we
    unpack the wire shape into those kwargs.

    Strict-mode validation failures (AdCPNotFoundError /
    AdCPValidationError raised by the assignment loop) are translated by
    :func:`translate_adcp_errors` into structured wire codes — without
    translation the framework surfaces an opaque INTERNAL_ERROR and
    buyers can't tell missing-package from internal failure.
    """
    identity = _build_identity(ctx)
    if hasattr(req, "model_dump"):
        body = req.model_dump(exclude_unset=True)
    elif isinstance(req, dict):
        body = dict(req)
    else:
        body = dict(req)
    # ``validation_mode`` arrives as a ``ValidationMode`` enum on the wire
    # path (the spec schema is an enum; pydantic preserves it through
    # ``model_dump(exclude_unset=True)``). Normalize to the underlying
    # string value so the impl's ``validation_mode == "strict"`` checks
    # work — without this, strict mode silently degrades to lenient and
    # the assignment loop never raises ``AdCPNotFoundError``.
    raw_mode = body.get("validation_mode")
    validation_mode_str = getattr(raw_mode, "value", raw_mode) or "strict"
    response = await asyncio.to_thread(
        _sync_creatives_impl,
        creatives=body.get("creatives") or [],
        assignments=body.get("assignments"),
        creative_ids=body.get("creative_ids"),
        delete_missing=bool(body.get("delete_missing", False)),
        dry_run=bool(body.get("dry_run", False)),
        validation_mode=validation_mode_str,
        push_notification_config=body.get("push_notification_config"),
        context=body.get("context"),
        identity=identity,
    )
    _emit_creative_created_for_new_creatives(identity.tenant_id, response, dry_run=bool(body.get("dry_run", False)))
    return _to_wire(response)


def _emit_creative_created_for_new_creatives(tenant_id: str, result: Any, *, dry_run: bool) -> None:
    """Fire ``creative.created`` per newly-created creative.

    sync_creatives is a bulk op: some creatives are created, some
    updated, some failed. Only the ``action="created"`` rows correspond
    to a new database row, so only those fire the event. Updates fire
    ``creative.status_changed`` from the admin approve/reject path
    (PR #446), not from here.

    Skipped for dry_run since no row is actually created. Best-effort
    delivery; failures don't propagate back to the buyer.
    """
    if dry_run:
        return

    creatives = getattr(result, "creatives", None)
    if not creatives:
        return

    from src.admin.services.webhook_publisher import emit_event

    for creative in creatives:
        action = getattr(creative, "action", None)
        # action is a CreativeAction enum; both enum and string forms are
        # safe to compare against the literal value.
        if getattr(action, "value", action) != "created":
            continue
        creative_id = getattr(creative, "creative_id", None)
        if not creative_id:
            continue
        emit_event(
            tenant_id,
            "creative.created",
            {
                "creative_id": creative_id,
                "platform_id": getattr(creative, "platform_id", None),
                "status": getattr(creative, "status", None),
            },
        )


@translate_adcp_errors
async def _delegate_get_media_buys(req: Any, ctx: RequestContext[Any]) -> dict[str, Any]:
    """Forward to ``src/core/tools/media_buy_list.py:_get_media_buys_impl``."""
    identity = _build_identity(ctx)
    req_model = _coerce_to_request_model(req, GetMediaBuysRequest)
    response = await asyncio.to_thread(_get_media_buys_impl, req_model, identity)
    return _to_wire(response)


@translate_adcp_errors
async def _delegate_get_media_buy_delivery(req: Any, ctx: RequestContext[Any]) -> dict[str, Any]:
    """Forward to ``src/core/tools/media_buy_delivery.py:_get_media_buy_delivery_impl``."""
    identity = _build_identity(ctx)
    req_model = _coerce_to_request_model(req, GetMediaBuyDeliveryRequest)
    response = await asyncio.to_thread(_get_media_buy_delivery_impl, req_model, identity)
    return _to_wire(response)


@translate_adcp_errors
async def _delegate_list_creative_formats(req: Any, ctx: RequestContext[Any]) -> dict[str, Any]:
    """Forward to ``src/core/tools/creative_formats.py:_list_creative_formats_impl``."""
    identity = _build_identity(ctx)
    req_model: Any = None
    if req is not None:
        req_model = _coerce_to_request_model(req, ListCreativeFormatsRequest)
    response = await asyncio.to_thread(_list_creative_formats_impl, req_model, identity)
    return _to_wire(response)


@translate_adcp_errors
async def _delegate_provide_performance_feedback(req: Any, ctx: RequestContext[Any]) -> dict[str, Any]:
    """Stub — salesagent doesn't yet have a performance-feedback impl.

    Required by the v6.0-rc.1 SalesPlatform Protocol; the soft-warn at boot
    fires when the platform omits it. Returns an acknowledgement matching
    the protocol's response shape so the framework's validator + buyer
    contract test pass. When a real performance-feedback pipeline lands
    upstream, this delegate becomes a forward to the new ``_impl``.
    """
    # Coerce to dict for inspection. The library response type accepts
    # status + ext, so we acknowledge the receipt without persisting.
    if hasattr(req, "model_dump"):
        payload = req.model_dump(exclude_none=True)
    elif isinstance(req, dict):
        payload = dict(req)
    else:
        payload = {}
    return {
        "status": "acknowledged",
        "message": "performance feedback receipt is not yet wired in this salesagent",
        "echo": payload,
    }


@translate_adcp_errors
async def _delegate_list_creatives(req: Any, ctx: RequestContext[Any]) -> dict[str, Any]:
    """Forward to ``src/core/tools/creatives/listing.py:_list_creatives_impl``.

    The impl decomposes into individual kwargs (no single request
    model). Default each from the wire shape; callers that send a
    Pydantic model get round-tripped through model_dump.
    """
    identity = _build_identity(ctx)
    if hasattr(req, "model_dump"):
        body = req.model_dump(exclude_unset=True)
    elif isinstance(req, dict):
        body = dict(req)
    else:
        body = {}
    response = await asyncio.to_thread(
        _list_creatives_impl,
        media_buy_id=body.get("media_buy_id"),
        media_buy_ids=body.get("media_buy_ids"),
        status=body.get("status"),
        format=body.get("format"),
        tags=body.get("tags"),
        created_after=body.get("created_after"),
        created_before=body.get("created_before"),
        search=body.get("search"),
        filters=body.get("filters"),
        fields=body.get("fields"),
        include_performance=bool(body.get("include_performance", False)),
        include_assignments=bool(body.get("include_assignments", False)),
        include_sub_assets=bool(body.get("include_sub_assets", False)),
        page=int(body.get("page") or 1),
        limit=int(body.get("limit") or 50),
        sort_by=body.get("sort_by") or "created_date",
        sort_order=body.get("sort_order") or "desc",
        context=body.get("context"),
        identity=identity,
    )
    return _to_wire(response)


@translate_adcp_errors
async def _delegate_get_signals(req: Any, ctx: RequestContext[Any]) -> dict[str, Any]:
    """Forward to ``src/core/tools/signals.py:_get_signals_impl``."""
    identity = _build_identity(ctx)
    req_model = _coerce_to_request_model(req, GetSignalsRequest)
    response = await _get_signals_impl(req_model, identity)
    return _to_wire(response)


# Account dispatch (sync_accounts / list_accounts) lives on the
# ``SalesagentAccountStore`` instance via the framework's
# AccountStoreUpsert / AccountStoreList Protocols, not in this delegate
# module — adcp >= 4.6.1 wires the framework dispatchers natively.
