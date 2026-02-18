"""Sync creatives orchestrator: main _sync_creatives_impl function."""

import logging
import time
from collections.abc import Sequence
from typing import Any

from adcp import PushNotificationConfig
from adcp.types.generated_poc.core.context import ContextObject
from adcp.types.generated_poc.core.creative_asset import CreativeAsset
from adcp.types.generated_poc.enums.creative_action import CreativeAction
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from pydantic import BaseModel
from sqlalchemy import select

from src.core.config_loader import get_current_tenant
from src.core.database.database_session import get_db_session
from src.core.helpers import get_principal_id_from_context, log_tool_activity
from src.core.schemas import SyncCreativeResult, SyncCreativesResponse
from src.core.tool_context import ToolContext
from src.core.validation_helpers import format_validation_error, run_async_in_sync_context

from ._assignments import _process_assignments
from ._processing import _create_new_creative, _update_existing_creative
from ._validation import _get_field, _validate_creative_input
from ._workflow import _audit_log_sync, _create_sync_workflow_steps, _send_creative_notifications

logger = logging.getLogger(__name__)


def _sync_creatives_impl(
    creatives: Sequence[CreativeAsset | BaseModel | dict[str, Any]],
    assignments: dict | None = None,
    creative_ids: list[str] | None = None,
    delete_missing: bool = False,
    dry_run: bool = False,
    validation_mode: str = "strict",
    push_notification_config: PushNotificationConfig | dict | None = None,
    context: ContextObject | dict | None = None,
    ctx: Context | ToolContext | None = None,
) -> SyncCreativesResponse:
    """Sync creative assets to centralized library (AdCP v2.5 spec compliant endpoint).

    Primary creative management endpoint that handles:
    - Bulk creative upload/update with upsert semantics
    - Creative assignment to media buy packages via assignments dict
    - Support for both hosted assets (media_url) and third-party tags (snippet)
    - Scoped updates via creative_ids filter, dry-run mode, and validation options

    Args:
        creatives: Array of creative assets to sync
        assignments: Bulk assignment map of creative_id to package_ids (spec-compliant)
        creative_ids: Filter to limit sync scope to specific creatives (AdCP 2.5).
            - None (default): Process all creatives in payload
            - Empty list []: Process no creatives (filter matches nothing)
            - List of IDs: Only process creatives whose IDs appear in both payload AND this filter
        delete_missing: Delete creatives not in sync payload (use with caution)
        dry_run: Preview changes without applying them
        validation_mode: Validation strictness (strict or lenient)
        push_notification_config: Push notification config for status updates (AdCP spec, optional)
        context: Application level context per adcp spec
        ctx: FastMCP context (automatically provided)

    Returns:
        SyncCreativesResponse with synced creatives and assignments
    """
    from pydantic import ValidationError

    # Phase 1a: Models flow through to helpers (which convert via isinstance guard).
    # No model_dump at orchestrator level — helpers handle dict conversion transitionally.

    # AdCP 2.5: Filter creatives by creative_ids if provided
    # This allows scoped updates to specific creatives without affecting others
    if creative_ids:
        creative_ids_set = set(creative_ids)
        creatives = [c for c in creatives if _get_field(c, "creative_id") in creative_ids_set]
        logger.info(f"[sync_creatives] Filtered to {len(creatives)} creatives by creative_ids filter")

    start_time = time.time()

    # Authentication
    principal_id = get_principal_id_from_context(ctx)

    # CRITICAL: principal_id is required for creative sync (NOT NULL in database)
    if not principal_id:
        raise ToolError(
            "Authentication required: Missing or invalid x-adcp-auth header. "
            "Creative sync requires authentication to associate creatives with an advertiser principal."
        )

    # Get tenant information
    # If context is ToolContext (A2A), tenant is already set, but verify it matches
    from src.core.tool_context import ToolContext

    if isinstance(ctx, ToolContext):
        # Tenant context should already be set by A2A handler, but verify
        tenant = get_current_tenant()
        if not tenant or tenant.get("tenant_id") != ctx.tenant_id:
            # Tenant context wasn't set properly - this shouldn't happen but handle it
            logger.warning(f"Warning: Tenant context mismatch, setting from ToolContext: {ctx.tenant_id}")
            # We need to load the tenant properly - for now use the ID from context
            tenant = {"tenant_id": ctx.tenant_id}
    else:
        # FastMCP path - tenant should be set by get_principal_from_context
        tenant = get_current_tenant()

    if not tenant:
        raise ToolError("No tenant context available")

    # Track actions per creative for AdCP-compliant response

    results: list[SyncCreativeResult] = []
    created_count = 0
    updated_count = 0
    unchanged_count = 0
    failed_count = 0

    # Legacy tracking (still used internally)
    synced_creatives = []
    failed_creatives: list[dict[str, Any]] = []

    # Track creatives requiring approval for workflow creation
    creatives_needing_approval = []

    # Extract webhook URL from push_notification_config for AI review callbacks
    webhook_url = None
    if push_notification_config:
        # Transitional: accept both PushNotificationConfig model and dict
        if isinstance(push_notification_config, dict):
            webhook_url = push_notification_config.get("url")
        else:
            webhook_url = str(push_notification_config.url) if push_notification_config.url else None
        logger.info(f"[sync_creatives] Push notification webhook URL: {webhook_url}")

    # Get tenant creative approval settings
    # approval_mode: "auto-approve", "require-human", "ai-powered"
    logger.info(f"[sync_creatives] Tenant dict keys: {list(tenant.keys())}")
    logger.info(f"[sync_creatives] Tenant approval_mode field: {tenant.get('approval_mode', 'NOT FOUND')}")
    approval_mode = tenant.get("approval_mode", "require-human")
    logger.info(f"[sync_creatives] Final approval mode: {approval_mode} (from tenant: {tenant.get('tenant_id')})")

    # Fetch creative formats ONCE before processing loop (outside any transaction)
    # This avoids async HTTP calls inside database savepoints which cause transaction errors
    from src.core.creative_agent_registry import get_creative_agent_registry

    registry = get_creative_agent_registry()
    all_formats = run_async_in_sync_context(registry.list_all_formats(tenant_id=tenant["tenant_id"]))

    with get_db_session() as session:
        # Process each creative with proper transaction isolation
        for raw_creative in creatives:
            try:
                # Normalize to CreativeAsset model (handles dicts from A2A raw, BaseModel subclasses)
                if isinstance(raw_creative, CreativeAsset):
                    creative = raw_creative
                elif isinstance(raw_creative, dict):
                    # Default required fields for raw dicts missing them
                    creative_data = raw_creative.copy()
                    creative_data.setdefault("assets", {})
                    creative = CreativeAsset(**creative_data)
                else:
                    creative = CreativeAsset.model_validate(raw_creative, from_attributes=True)

                # Validate the creative against schema and business rules
                try:
                    validated_creative = _validate_creative_input(creative, registry, principal_id)
                    format_value = validated_creative.format

                except (ValidationError, ValueError) as validation_error:
                    # Creative failed validation - add to failed list
                    creative_id = creative.creative_id or "unknown"
                    # Format ValidationError nicely for clients, pass through ValueError as-is
                    if isinstance(validation_error, ValidationError):
                        error_msg = format_validation_error(validation_error, context=f"creative {creative_id}")
                    else:
                        error_msg = str(validation_error)
                    failed_creatives.append({"creative_id": creative_id, "error": error_msg})
                    failed_count += 1
                    results.append(
                        SyncCreativeResult(
                            creative_id=creative_id,
                            action="failed",
                            status=None,
                            platform_id=None,
                            errors=[error_msg],
                            review_feedback=None,
                            assigned_to=None,
                            assignment_errors=None,
                        )
                    )
                    continue  # Skip to next creative

                # Use savepoint for individual creative transaction isolation
                with session.begin_nested():
                    # Check if creative already exists (always check for upsert/patch behavior)
                    # SECURITY: Must filter by principal_id to prevent cross-principal modification
                    existing_creative = None
                    if creative.creative_id:
                        from src.core.database.models import Creative as DBCreative

                        # Query for existing creative with security filter
                        stmt = select(DBCreative).filter_by(
                            tenant_id=tenant["tenant_id"],
                            principal_id=principal_id,  # SECURITY: Prevent cross-principal modification
                            creative_id=creative.creative_id,
                        )
                        existing_creative = session.scalars(stmt).first()

                    if existing_creative:
                        update_result, needs_approval = _update_existing_creative(
                            creative=creative,
                            existing_creative=existing_creative,
                            session=session,
                            format_value=format_value,
                            approval_mode=approval_mode,
                            tenant=tenant,
                            webhook_url=webhook_url,
                            context=context,
                            all_formats=all_formats,
                            registry=registry,
                            principal_id=principal_id,
                        )

                        # Handle failed updates
                        if update_result.action == CreativeAction.failed:
                            failed_creatives.append(
                                {
                                    "creative_id": existing_creative.creative_id,
                                    "error": update_result.errors[0] if update_result.errors else "Unknown error",
                                    "format": creative.format_id,
                                }
                            )
                            failed_count += 1
                            results.append(update_result)
                            continue

                        # Track counts
                        if update_result.action == CreativeAction.updated:
                            updated_count += 1
                        else:
                            unchanged_count += 1

                        # Track creatives needing approval for workflow creation
                        if needs_approval:
                            creative_info: dict[str, Any] = {
                                "creative_id": existing_creative.creative_id,
                                "format": creative.format_id,
                                "name": creative.name,
                                "status": existing_creative.status,
                            }
                            # Include AI review reason if available
                            if (
                                approval_mode == "ai-powered"
                                and existing_creative.data
                                and existing_creative.data.get("ai_review")
                            ):
                                creative_info["ai_review_reason"] = existing_creative.data["ai_review"].get("reason")
                            creatives_needing_approval.append(creative_info)

                        results.append(update_result)

                    else:
                        # Create new creative
                        create_result, needs_approval = _create_new_creative(
                            creative=creative,
                            session=session,
                            format_value=format_value,
                            approval_mode=approval_mode,
                            tenant=tenant,
                            webhook_url=webhook_url,
                            context=context,
                            all_formats=all_formats,
                            registry=registry,
                            principal_id=principal_id,
                        )

                        # Handle failed creates
                        if create_result.action == CreativeAction.failed:
                            creative_id = creative.creative_id or "unknown"
                            failed_creatives.append(
                                {
                                    "creative_id": creative_id,
                                    "error": create_result.errors[0] if create_result.errors else "Unknown error",
                                    "format": creative.format_id,
                                }
                            )
                            failed_count += 1
                            results.append(create_result)
                            continue

                        # Track counts
                        created_count += 1

                        # Track creatives needing approval for workflow creation
                        if needs_approval:
                            creative_info = {
                                "creative_id": create_result.creative_id,
                                "format": creative.format_id,
                                "name": creative.name,
                                "status": create_result.status,
                            }
                            # AI review reason will be added asynchronously when review completes
                            # No ai_result available yet in async mode
                            creatives_needing_approval.append(creative_info)

                        results.append(create_result)

                    # If we reach here, creative processing succeeded
                    synced_creatives.append(creative)

            except Exception as e:
                # Savepoint automatically rolls back this creative only
                creative_id = _get_field(raw_creative, "creative_id", "unknown")
                error_msg = str(e)
                failed_creatives.append(
                    {"creative_id": creative_id, "name": _get_field(raw_creative, "name"), "error": error_msg}
                )
                failed_count += 1
                results.append(
                    SyncCreativeResult(
                        creative_id=creative_id,
                        action="failed",
                        status=None,
                        platform_id=None,
                        errors=[error_msg],
                        review_feedback=None,
                        assigned_to=None,
                        assignment_errors=None,
                    )
                )

        # Commit all successful creative operations
        session.commit()

    # Process assignments (spec-compliant: creative_id → package_ids mapping)
    assignment_list = _process_assignments(
        assignments=assignments,
        results=results,
        tenant=tenant,
        validation_mode=validation_mode,
    )

    # Create workflow steps and send notifications for creatives requiring approval
    if creatives_needing_approval:
        _create_sync_workflow_steps(
            creatives_needing_approval=creatives_needing_approval,
            principal_id=principal_id,
            tenant=tenant,
            approval_mode=approval_mode,
            push_notification_config=push_notification_config,
            context=context,
            ctx=ctx,
        )
        _send_creative_notifications(
            creatives_needing_approval=creatives_needing_approval,
            tenant=tenant,
            approval_mode=approval_mode,
            principal_id=principal_id,
        )

    # Audit logging
    _audit_log_sync(
        tenant=tenant,
        principal_id=principal_id,
        synced_creatives=synced_creatives,
        failed_creatives=failed_creatives,
        assignment_list=assignment_list,
        creative_ids=creative_ids,
        dry_run=dry_run,
        created_count=created_count,
        updated_count=updated_count,
        unchanged_count=unchanged_count,
        failed_count=failed_count,
        creatives_needing_approval=creatives_needing_approval,
    )

    # Log activity
    if ctx is not None:
        log_tool_activity(ctx, "sync_creatives", start_time)

    # Build message
    message = f"Synced {created_count + updated_count} creatives"
    if created_count:
        message += f" ({created_count} created"
        if updated_count:
            message += f", {updated_count} updated"
        message += ")"
    elif updated_count:
        message += f" ({updated_count} updated)"
    if unchanged_count:
        message += f", {unchanged_count} unchanged"
    if failed_count:
        message += f", {failed_count} failed"
    if assignment_list:
        message += f", {len(assignment_list)} assignments created"
    if creatives_needing_approval:
        message += f", {len(creatives_needing_approval)} require approval"

    # Build AdCP-compliant response (per official spec)
    return SyncCreativesResponse(  # type: ignore[call-arg]  # RootModel auto-wrapping accepts variant kwargs
        creatives=results,
        dry_run=dry_run,
        context=context,
    )
