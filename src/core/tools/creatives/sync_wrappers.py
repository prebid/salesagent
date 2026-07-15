"""MCP and A2A wrapper functions for sync_creatives."""

from typing import Annotated, Any

from adcp import PushNotificationConfig
from adcp.types import AccountReference as LibraryAccountReference
from adcp.types import ContextObject, CreativeAsset, ValidationMode
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import Field

from src.core.helpers import enum_value
from src.core.resolved_identity import ResolvedIdentity
from src.core.tool_context import ToolContext

from ._sync import _sync_creatives_impl


async def sync_creatives(
    creatives: list[CreativeAsset],
    assignments: dict[str, list[str]] | None = None,
    creative_ids: list[str] | None = None,
    delete_missing: Annotated[
        bool, Field(description="Delete creatives not in the sync payload (use with caution)")
    ] = False,
    dry_run: Annotated[bool, Field(description="Preview changes without applying them")] = False,
    validation_mode: ValidationMode | None = None,
    push_notification_config: PushNotificationConfig | None = None,
    context: ContextObject | None = None,  # Application level context per adcp spec
    account: LibraryAccountReference | None = None,
    idempotency_key: Annotated[
        str | None, Field(description="Client-generated idempotency key (AdCP 3.1.1 REQUIRED, 16-255 chars)")
    ] = None,
    ctx: Context | ToolContext | None = None,
):
    """Sync creative assets to centralized library (AdCP v2.5 spec compliant endpoint).

    MCP tool wrapper that delegates to the shared implementation.
    FastMCP automatically validates and coerces JSON inputs to Pydantic models.

    Args:
        creatives: List of creative assets to sync
        assignments: Bulk assignment map of creative_id to package_ids (spec-compliant)
        creative_ids: Filter to limit sync scope to specific creatives (AdCP 2.5)
        delete_missing: Delete creatives not in sync payload (use with caution)
        dry_run: Preview changes without applying them
        validation_mode: Validation strictness (strict or lenient)
        push_notification_config: Push notification config for async notifications (AdCP spec, optional)
        context: Application level context per adcp spec
        idempotency_key: Client-generated idempotency key (AdCP 3.1.1 REQUIRED, 16-255 chars).
            Declared here so the envelope-tolerance middleware does not strip it, and
            forwarded verbatim — a missing/malformed key rejects as VALIDATION_ERROR.
        ctx: FastMCP context (automatically provided)

    Returns:
        ToolResult with SyncCreativesResponse data
    """
    # AdCP 3.1.1 makes idempotency_key REQUIRED on the sync_creatives request. Enforce
    # required-ness + shape at the protocol boundary where the wire key arrives (never
    # fabricated); the shared impl keeps only a None-tolerant shape check for internal callers.
    from src.core.schemas._base import require_idempotency_key

    require_idempotency_key(idempotency_key)

    identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None

    # Resolve account at transport boundary (before _impl)
    from src.core.transport_helpers import enrich_identity_with_account

    identity = enrich_identity_with_account(identity, account)

    # Phase 1a: Pass typed models directly to impl (no more model_dump conversion)
    validation_mode_str = enum_value(validation_mode) or "strict"

    response = _sync_creatives_impl(
        creatives=creatives,
        assignments=assignments,
        creative_ids=creative_ids,
        delete_missing=delete_missing,
        dry_run=dry_run,
        validation_mode=validation_mode_str,
        push_notification_config=push_notification_config,
        context=context,
        idempotency_key=idempotency_key,
        identity=identity,
    )
    return ToolResult(content=str(response), structured_content=response)


def sync_creatives_raw(
    # A2A/REST send wire dicts; _sync_creatives_impl validates each entry
    # individually (partial-success semantics with per-creative results).
    creatives: list[CreativeAsset] | list[dict[str, Any]],
    assignments: dict = None,
    creative_ids: list[str] = None,
    delete_missing: bool = False,
    dry_run: bool = False,
    validation_mode: str = "strict",
    push_notification_config: PushNotificationConfig | None = None,
    context: ContextObject | None = None,
    account: LibraryAccountReference | None = None,
    idempotency_key: str | None = None,
    ctx: Context | ToolContext | None = None,
    identity: ResolvedIdentity | None = None,
):
    """Sync creative assets to the centralized creative library (raw function for A2A server use).

    Delegates to the shared implementation.

    Args:
        creatives: List of CreativeAsset models
        assignments: Bulk assignment map of creative_id to package_ids (spec-compliant)
        creative_ids: Filter to limit sync scope to specific creatives (AdCP 2.5)
        delete_missing: Delete creatives not in sync payload (use with caution)
        dry_run: Preview changes without applying them
        validation_mode: Validation strictness (strict or lenient)
        push_notification_config: Push notification config for status updates
        context: Application level context per adcp spec
        idempotency_key: Client-generated idempotency key (AdCP 3.1.1 REQUIRED, 16-255 chars);
            forwarded verbatim so a missing/malformed key rejects as VALIDATION_ERROR
        ctx: FastMCP context (automatically provided)
        identity: ResolvedIdentity (transport-agnostic, preferred over ctx)

    Returns:
        SyncCreativesResponse with synced creatives and assignments
    """
    # AdCP 3.1.1 makes idempotency_key REQUIRED on the sync_creatives request. Both the
    # A2A handler and the REST route funnel through this raw wrapper, so enforce required-ness
    # + shape here at the protocol boundary (never fabricated).
    from src.core.schemas._base import require_idempotency_key

    require_idempotency_key(idempotency_key)

    if identity is None:
        from src.core.transport_helpers import resolve_identity_from_context

        identity = resolve_identity_from_context(ctx)

    # Resolve account at transport boundary (before _impl)
    from src.core.transport_helpers import enrich_identity_with_account

    identity = enrich_identity_with_account(identity, account)

    return _sync_creatives_impl(
        creatives=creatives,
        assignments=assignments,
        creative_ids=creative_ids,
        delete_missing=delete_missing,
        dry_run=dry_run,
        validation_mode=validation_mode,
        push_notification_config=push_notification_config,
        context=context,
        idempotency_key=idempotency_key,
        identity=identity,
    )
