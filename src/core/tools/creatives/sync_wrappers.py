"""MCP and A2A wrapper functions for sync_creatives."""

from adcp import PushNotificationConfig
from adcp.types.generated_poc.core.context import ContextObject
from adcp.types.generated_poc.core.creative_asset import CreativeAsset
from adcp.types.generated_poc.enums.validation_mode import ValidationMode
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult

from src.core.tool_context import ToolContext

from ._sync import _sync_creatives_impl


async def sync_creatives(
    creatives: list[CreativeAsset],
    assignments: dict[str, list[str]] | None = None,
    creative_ids: list[str] | None = None,
    delete_missing: bool = False,
    dry_run: bool = False,
    validation_mode: ValidationMode | None = None,
    push_notification_config: PushNotificationConfig | None = None,
    context: ContextObject | None = None,  # Application level context per adcp spec
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
        ctx: FastMCP context (automatically provided)

    Returns:
        ToolResult with SyncCreativesResponse data
    """
    # Phase 1a: Pass typed models directly to impl (no more model_dump conversion)
    validation_mode_str = validation_mode.value if validation_mode else "strict"

    response = _sync_creatives_impl(
        creatives=creatives,
        assignments=assignments,
        creative_ids=creative_ids,
        delete_missing=delete_missing,
        dry_run=dry_run,
        validation_mode=validation_mode_str,
        push_notification_config=push_notification_config,
        context=context,
        ctx=ctx,
    )
    return ToolResult(content=str(response), structured_content=response)


def sync_creatives_raw(
    creatives: list[CreativeAsset],
    assignments: dict = None,
    creative_ids: list[str] = None,
    delete_missing: bool = False,
    dry_run: bool = False,
    validation_mode: str = "strict",
    push_notification_config: PushNotificationConfig | None = None,
    context: ContextObject | None = None,
    ctx: Context | ToolContext | None = None,
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
        ctx: FastMCP context (automatically provided)

    Returns:
        SyncCreativesResponse with synced creatives and assignments
    """
    return _sync_creatives_impl(
        creatives=creatives,
        assignments=assignments,
        creative_ids=creative_ids,
        delete_missing=delete_missing,
        dry_run=dry_run,
        validation_mode=validation_mode,
        push_notification_config=push_notification_config,
        context=context,
        ctx=ctx,
    )
