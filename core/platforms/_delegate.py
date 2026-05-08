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
import logging
from typing import Any

from adcp.decisioning import AdcpError, RequestContext
from adcp.server.auth import current_principal

from core.middleware.transport_detect import current_transport
from src.core.config_loader import get_tenant_by_id
from src.core.exceptions import AdCPError
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    CreateMediaBuyRequest,
    GetMediaBuyDeliveryRequest,
    GetMediaBuysRequest,
    GetProductsRequest,
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

logger = logging.getLogger(__name__)

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
    # ``McpWebhookPayload``. ``TransportDetectMiddleware`` (added per #202)
    # populates the ``current_transport`` ContextVar based on URL path;
    # we read it here and stamp ``identity.protocol`` so every downstream
    # impl sees the actual transport. Falls back to "mcp" when the
    # ContextVar is unset (lifespan events, unit-test harness paths,
    # admin requests that somehow reach here).
    detected_transport = current_transport.get()
    if detected_transport in ("mcp", "a2a"):
        protocol: str = detected_transport
    else:
        protocol = "mcp"
        # Forward-compat guard: if the auth chain populated
        # ``current_principal`` (only set inside HTTP requests by
        # ``BearerTokenAuthMiddleware``) but transport detection didn't,
        # the middleware chain is misconfigured — A2A buyers will silently
        # receive MCP-shaped webhooks. Surface once per process so the
        # operator gets a clear signal before the next #64-style
        # silent-drop bug. Lifespan / unit-test / admin paths don't
        # populate ``current_principal``, so they skip this branch.
        # See #221 for the rationale.
        global _TRANSPORT_FALLBACK_WARNED
        if not _TRANSPORT_FALLBACK_WARNED and current_principal.get() is not None:
            _TRANSPORT_FALLBACK_WARNED = True
            logger.warning(
                "_build_identity falling back to protocol='mcp' inside an "
                "authenticated request scope — TransportDetectMiddleware may "
                "not be wired or has been reordered after the auth chain. "
                "A2A buyers will silently receive MCP-shaped webhooks. "
                "Check core.main:_serve_kwargs middleware order. "
                "See salesagent issue #221."
            )

    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant=tenant_dict,
        protocol=protocol,
        testing_context=AdCPTestContext(),
    )


def _coerce_to_request_model(req: Any, model_cls: type) -> Any:
    """Coerce ``req`` (dict OR Pydantic model OR generated model) into
    the Pydantic model class the impl expects."""
    if isinstance(req, model_cls):
        return req
    if isinstance(req, dict):
        return model_cls(**req)
    if hasattr(req, "model_dump"):
        # Different but related model type (e.g. another version of
        # GetProductsRequest from a sibling module). Round-trip
        # through dict to coerce.
        return model_cls(**req.model_dump(exclude_none=True))
    return model_cls.model_validate(req)


def _to_wire(response: Any) -> dict[str, Any]:
    """Project a Pydantic response model onto a wire dict the framework
    can serialize. Returns the response unchanged if it's already a
    dict."""
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json", exclude_none=True)
    return dict(response)  # last-ditch coerce


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


async def _delegate_get_products(req: GetProductsRequest, ctx: RequestContext[Any]) -> dict[str, Any]:
    """Forward to ``src/core/tools/products.py:_get_products_impl``.

    Note: typed ``req: GetProductsRequest`` here documents intent but
    the SDK resolves ``params_model`` from the platform router's base
    class advertisement, not from this delegate or the platform
    subclass override. Unknown-field rejection (dev-mode strict-extra)
    is therefore not enforced at the wire boundary today —
    ``tests/integration/test_mcp_unknown_field_handling.py``'s
    ``test_unknown_field_rejected`` is xfailed pending an upstream
    SDK change.
    """
    identity = _build_identity(ctx)
    req_model = _coerce_to_request_model(req, GetProductsRequest)
    response = await _get_products_impl(req_model, identity)
    return _to_wire(response)


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

    Salesagent ``AdCPError`` exceptions are translated to the framework's
    :class:`AdcpError` so structured rejections (TERMS_REJECTED, etc.)
    project to the correct wire ``code`` instead of leaking through as
    generic ``INTERNAL_ERROR``.
    """
    identity = _build_identity(ctx)
    req_model = _coerce_to_request_model(req, CreateMediaBuyRequest)
    pnc = req_model.push_notification_config
    pnc_dict: dict[str, Any] | None
    if pnc is None:
        pnc_dict = None
    elif hasattr(pnc, "model_dump"):
        pnc_dict = pnc.model_dump(mode="json", exclude_none=True)
    else:
        pnc_dict = dict(pnc)
    try:
        response = await _create_media_buy_impl(req_model, push_notification_config=pnc_dict, identity=identity)
    except AdCPError as exc:
        raise _translate_adcp_error(exc) from exc
    return _to_wire(response)


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

    AdCPError translation: the impl raises typed AdCPError subclasses
    (e.g. AdCPMediaBuyNotFoundError, AdCPPackageNotFoundError) for
    structured rejections. The framework dispatcher only projects
    decisioning AdcpError to the wire ``adcp_error`` envelope, so we
    translate here. Without this translation a typed not-found surfaces
    as an opaque INTERNAL_ERROR — losing the tenant-isolation guarantee
    that cross-tenant probing returns ``MEDIA_BUY_NOT_FOUND``, not
    ``AUTHORIZATION_ERROR``.
    """
    identity = _build_identity(ctx)
    if isinstance(patch, dict):
        patch_dict = dict(patch)
    elif hasattr(patch, "model_dump"):
        patch_dict = patch.model_dump(exclude_unset=True)
    else:
        patch_dict = dict(patch)
    patch_dict["media_buy_id"] = media_buy_id
    req_model = _coerce_to_request_model(patch_dict, UpdateMediaBuyRequest)
    try:
        response = await asyncio.to_thread(_update_media_buy_impl, req_model, identity)
    except AdCPError as exc:
        raise AdcpError(
            exc.error_code,
            message=exc.message or str(exc),
            recovery=exc.recovery,
            details=exc.details,
        ) from exc
    return _to_wire(response)


async def _delegate_sync_creatives(req: Any, ctx: RequestContext[Any]) -> dict[str, Any]:
    """Forward to ``src/core/tools/creatives/_sync.py:_sync_creatives_impl``.

    The impl takes individual kwargs (creatives, assignments,
    creative_ids, ...) rather than a single request model — we
    unpack the wire shape into those kwargs.
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
    try:
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
    except AdCPError as exc:
        # Strict-mode validation failures (AdCPNotFoundError /
        # AdCPValidationError raised by the assignment loop) need
        # structured wire codes — without translation the framework
        # surfaces an opaque INTERNAL_ERROR and buyers can't tell
        # missing-package from internal failure.
        raise _translate_adcp_error(exc) from exc
    return _to_wire(response)


async def _delegate_get_media_buys(req: Any, ctx: RequestContext[Any]) -> dict[str, Any]:
    """Forward to ``src/core/tools/media_buy_list.py:_get_media_buys_impl``."""
    identity = _build_identity(ctx)
    req_model = _coerce_to_request_model(req, GetMediaBuysRequest)
    response = await asyncio.to_thread(_get_media_buys_impl, req_model, identity)
    return _to_wire(response)


async def _delegate_get_media_buy_delivery(req: Any, ctx: RequestContext[Any]) -> dict[str, Any]:
    """Forward to ``src/core/tools/media_buy_delivery.py:_get_media_buy_delivery_impl``."""
    identity = _build_identity(ctx)
    req_model = _coerce_to_request_model(req, GetMediaBuyDeliveryRequest)
    response = await asyncio.to_thread(_get_media_buy_delivery_impl, req_model, identity)
    return _to_wire(response)


async def _delegate_list_creative_formats(req: Any, ctx: RequestContext[Any]) -> dict[str, Any]:
    """Forward to ``src/core/tools/creative_formats.py:_list_creative_formats_impl``."""
    identity = _build_identity(ctx)
    req_model: Any = None
    if req is not None:
        req_model = _coerce_to_request_model(req, ListCreativeFormatsRequest)
    response = await asyncio.to_thread(_list_creative_formats_impl, req_model, identity)
    return _to_wire(response)


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
