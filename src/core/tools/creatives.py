"""Creative Sync and Listing tool implementations.

Handles creative operations including:
- Creative synchronization from buyer creative agents
- Creative asset validation and format conversion
- Creative library management
- Creative discovery and filtering
"""

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import ValidationError
from rich.console import Console
from sqlalchemy import select

from src.core.tool_context import ToolContext

logger = logging.getLogger(__name__)
console = Console()

from src.core.audit_logger import get_audit_logger
from src.core.config_loader import get_current_tenant
from src.core.database.database_session import get_db_session
from src.core.helpers import (
    _extract_format_namespace,
    _validate_creative_assets,
    get_principal_id_from_context,
    log_tool_activity,
)
from src.core.schema_adapters import ListCreativesResponse, SyncCreativesResponse
from src.core.schemas import Creative, SyncCreativeResult
from src.core.schemas_generated._schemas_v1_enums_creative_status_json import (
    CreativeStatus as CreativeStatusEnum,
)
from src.core.validation_helpers import format_validation_error, run_async_in_sync_context


def _sync_creatives_impl(
    creatives: list[dict],
    patch: bool = False,
    assignments: dict | None = None,
    delete_missing: bool = False,
    dry_run: bool = False,
    validation_mode: str = "strict",
    push_notification_config: dict | None = None,
    context: Context | ToolContext | None = None,
) -> SyncCreativesResponse:
    """Sync creative assets to centralized library (AdCP v2.4 spec compliant endpoint).

    Primary creative management endpoint that handles:
    - Bulk creative upload/update with upsert semantics
    - Creative assignment to media buy packages via assignments dict
    - Support for both hosted assets (media_url) and third-party tags (snippet)
    - Patch updates, dry-run mode, and validation options

    Args:
        creatives: Array of creative assets to sync
        patch: When true, only update provided fields (partial update). When false, full upsert.
        assignments: Bulk assignment map of creative_id to package_ids (spec-compliant)
        delete_missing: Delete creatives not in sync payload (use with caution)
        dry_run: Preview changes without applying them
        validation_mode: Validation strictness (strict or lenient)
        push_notification_config: Push notification config for status updates (AdCP spec, optional)
        context: FastMCP context (automatically provided)

    Returns:
        SyncCreativesResponse with synced creatives and assignments
    """
    from pydantic import ValidationError

    # Process raw creative dictionaries without schema validation initially
    # Schema objects will be created later with populated internal fields
    raw_creatives = [creative if isinstance(creative, dict) else creative.model_dump() for creative in creatives]

    start_time = time.time()

    # Authentication
    principal_id = get_principal_id_from_context(context)

    # CRITICAL: principal_id is required for creative sync (NOT NULL in database)
    if not principal_id:
        raise ToolError(
            "Authentication required: Missing or invalid x-adcp-auth header. "
            "Creative sync requires authentication to associate creatives with an advertiser principal."
        )

    # Get tenant information
    # If context is ToolContext (A2A), tenant is already set, but verify it matches
    from src.core.tool_context import ToolContext

    if isinstance(context, ToolContext):
        # Tenant context should already be set by A2A handler, but verify
        tenant = get_current_tenant()
        if not tenant or tenant.get("tenant_id") != context.tenant_id:
            # Tenant context wasn't set properly - this shouldn't happen but handle it
            logger.warning(f"Warning: Tenant context mismatch, setting from ToolContext: {context.tenant_id}")
            # We need to load the tenant properly - for now use the ID from context
            tenant = {"tenant_id": context.tenant_id}
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
    failed_creatives = []

    # Track creatives requiring approval for workflow creation
    creatives_needing_approval = []

    # Extract webhook URL from push_notification_config for AI review callbacks
    webhook_url = None
    if push_notification_config:
        webhook_url = push_notification_config.get("url")
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
        for creative in raw_creatives:
            try:
                # First, validate the creative against the schema before database operations
                try:
                    # Create temporary schema object for validation (AdCP v1 spec compliant)
                    # Only include AdCP spec fields + internal fields
                    schema_data = {
                        "creative_id": creative.get("creative_id") or str(uuid.uuid4()),
                        "name": creative.get("name", ""),  # Ensure name is never None
                        "format_id": creative.get("format_id") or creative.get("format"),  # Support both field names
                        "assets": creative.get("assets", {}),  # Required by AdCP v1 spec
                        # Internal fields (added by sales agent)
                        "principal_id": principal_id,
                        "created_at": datetime.now(UTC),
                        "updated_at": datetime.now(UTC),
                        "status": CreativeStatusEnum.pending_review.value,
                    }

                    # Add optional AdCP v1 fields if provided
                    if creative.get("inputs"):
                        schema_data["inputs"] = creative.get("inputs")
                    if creative.get("tags"):
                        schema_data["tags"] = creative.get("tags")
                    if creative.get("approved") is not None:
                        schema_data["approved"] = creative.get("approved")

                    # Validate by creating a Creative schema object
                    # This will fail if required fields are missing or invalid (like empty name)
                    # Also auto-upgrades string format_ids to FormatId objects via validator
                    validated_creative = Creative(**schema_data)

                    # Additional business logic validation
                    if not creative.get("name") or str(creative.get("name")).strip() == "":
                        raise ValueError("Creative name cannot be empty")

                    if not creative.get("format_id") and not creative.get("format"):
                        raise ValueError("Creative format is required")

                    # Use validated format (auto-upgraded from string if needed)
                    format_value = validated_creative.format

                except (ValidationError, ValueError) as validation_error:
                    # Creative failed validation - add to failed list
                    creative_id = creative.get("creative_id", "unknown")
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
                    if creative.get("creative_id"):
                        from src.core.database.models import Creative as DBCreative

                        # Query for existing creative with security filter
                        stmt = select(DBCreative).filter_by(
                            tenant_id=tenant["tenant_id"],
                            principal_id=principal_id,  # SECURITY: Prevent cross-principal modification
                            creative_id=creative.get("creative_id"),
                        )
                        existing_creative = session.scalars(stmt).first()

                    if existing_creative:
                        # Update existing creative (respects patch vs full upsert)
                        # Update updated_at timestamp
                        now = datetime.now(UTC)
                        existing_creative.updated_at = now  # type: ignore[assignment]

                        # Track changes for result
                        changes = []

                        # Update fields based on patch mode
                        if patch:
                            # Patch mode: only update provided fields
                            if creative.get("name") is not None and creative.get("name") != existing_creative.name:
                                name_value = creative.get("name")
                                if name_value is not None:
                                    existing_creative.name = str(name_value)
                                changes.append("name")
                            if creative.get("format_id") or creative.get("format"):
                                # Use validated format_value (already auto-upgraded from string)
                                new_agent_url, new_format = _extract_format_namespace(format_value)
                                if (
                                    new_agent_url != existing_creative.agent_url
                                    or new_format != existing_creative.format
                                ):
                                    existing_creative.agent_url = new_agent_url
                                    existing_creative.format = new_format
                                    changes.append("format")
                        else:
                            # Full upsert mode: replace all fields
                            if creative.get("name") != existing_creative.name:
                                name_value = creative.get("name")
                                if name_value is not None:
                                    existing_creative.name = str(name_value)
                                changes.append("name")
                            # Use validated format_value (already auto-upgraded from string)
                            new_agent_url, new_format = _extract_format_namespace(format_value)
                            if new_agent_url != existing_creative.agent_url or new_format != existing_creative.format:
                                existing_creative.agent_url = new_agent_url
                                existing_creative.format = new_format
                                changes.append("format")

                        # Determine creative status based on approval mode
                        creative_format = creative.get("format_id") or creative.get("format")
                        if creative_format:  # Only update approval status if format is provided
                            if approval_mode == "auto-approve":
                                existing_creative.status = CreativeStatusEnum.approved.value
                                needs_approval = False
                            elif approval_mode == "ai-powered":
                                # Submit to background AI review (async)

                                from src.admin.blueprints.creatives import (
                                    _ai_review_executor,
                                    _ai_review_lock,
                                    _ai_review_tasks,
                                )

                                # Set status to pending_review for AI review
                                existing_creative.status = CreativeStatusEnum.pending_review.value
                                needs_approval = True

                                # Submit background task
                                task_id = f"ai_review_{existing_creative.creative_id}_{uuid.uuid4().hex[:8]}"

                                # Need to flush to ensure creative_id is available
                                session.flush()

                                # Import the async function
                                from src.admin.blueprints.creatives import _ai_review_creative_async

                                future = _ai_review_executor.submit(
                                    _ai_review_creative_async,
                                    creative_id=existing_creative.creative_id,
                                    tenant_id=tenant["tenant_id"],
                                    webhook_url=webhook_url,
                                    slack_webhook_url=tenant.get("slack_webhook_url"),
                                    principal_name=principal_id,
                                )

                                # Track the task
                                with _ai_review_lock:
                                    _ai_review_tasks[task_id] = {
                                        "future": future,
                                        "creative_id": existing_creative.creative_id,
                                        "created_at": time.time(),
                                    }

                                logger.info(
                                    f"[sync_creatives] Submitted AI review for {existing_creative.creative_id} (task: {task_id})"
                                )
                            else:  # require-human
                                existing_creative.status = CreativeStatusEnum.pending_review.value
                                needs_approval = True
                        else:
                            needs_approval = False

                        # Store creative properties in data field
                        if patch:
                            # Patch mode: merge with existing data
                            data = existing_creative.data or {}
                            if creative.get("url") is not None and data.get("url") != creative.get("url"):
                                data["url"] = creative.get("url")
                                changes.append("url")
                            if creative.get("click_url") is not None and data.get("click_url") != creative.get(
                                "click_url"
                            ):
                                data["click_url"] = creative.get("click_url")
                                changes.append("click_url")
                            if creative.get("width") is not None and data.get("width") != creative.get("width"):
                                data["width"] = creative.get("width")
                                changes.append("width")
                            if creative.get("height") is not None and data.get("height") != creative.get("height"):
                                data["height"] = creative.get("height")
                                changes.append("height")
                            if creative.get("duration") is not None and data.get("duration") != creative.get(
                                "duration"
                            ):
                                data["duration"] = creative.get("duration")
                                changes.append("duration")
                            if creative.get("assets") is not None:
                                data["assets"] = creative.get("assets")
                                changes.append("assets")
                            if creative.get("template_variables") is not None:
                                data["template_variables"] = creative.get("template_variables")
                                changes.append("template_variables")
                        else:
                            # Full upsert mode: replace all data
                            # Extract URL from assets if not provided at top level
                            # Use same priority logic as schema_data above
                            url = creative.get("url")
                            if not url and creative.get("assets"):
                                assets = creative["assets"]

                                # Priority 1: Try common asset_ids
                                for priority_key in ["main", "image", "video", "creative", "content"]:
                                    if priority_key in assets and isinstance(assets[priority_key], dict):
                                        url = assets[priority_key].get("url")
                                        if url:
                                            logger.debug(
                                                f"[sync_creatives] Extracted URL from assets.{priority_key}.url for data storage"
                                            )
                                            break

                                # Priority 2: First available asset URL
                                if not url:
                                    for asset_id, asset_data in assets.items():
                                        if isinstance(asset_data, dict) and asset_data.get("url"):
                                            url = asset_data["url"]
                                            logger.debug(
                                                f"[sync_creatives] Extracted URL from assets.{asset_id}.url for data storage (fallback)"
                                            )
                                            break

                            data = {
                                "url": url,
                                "click_url": creative.get("click_url"),
                                "width": creative.get("width"),
                                "height": creative.get("height"),
                                "duration": creative.get("duration"),
                            }
                            if creative.get("assets"):
                                data["assets"] = creative.get("assets")
                            if creative.get("template_variables"):
                                data["template_variables"] = creative.get("template_variables")

                            # ALWAYS validate updates with creative agent
                            if creative_format:
                                try:
                                    # Use pre-fetched formats (fetched outside transaction at function start)
                                    # This avoids async HTTP calls inside savepoint

                                    # Find matching format
                                    format_obj = None
                                    for fmt in all_formats:
                                        if fmt.format_id == creative_format:
                                            format_obj = fmt
                                            break

                                    if format_obj and format_obj.agent_url:
                                        # Check if format is generative (has output_format_ids)
                                        is_generative = bool(getattr(format_obj, "output_format_ids", None))

                                        if is_generative:
                                            # Generative creative update - rebuild using AI
                                            logger.info(
                                                f"[sync_creatives] Detected generative format update: {creative_format}, "
                                                f"checking for Gemini API key"
                                            )

                                            # Get Gemini API key from config
                                            from src.core.config import get_config

                                            config = get_config()
                                            gemini_api_key = config.gemini_api_key

                                            if not gemini_api_key:
                                                error_msg = (
                                                    f"Cannot update generative creative {creative_format}: "
                                                    f"GEMINI_API_KEY not configured"
                                                )
                                                logger.error(f"[sync_creatives] {error_msg}")
                                                raise ValueError(error_msg)

                                            # Extract message/brief from assets or inputs
                                            message = None
                                            if creative.get("assets"):
                                                assets = creative.get("assets", {})
                                                for role, asset in assets.items():
                                                    if role in ["message", "brief", "prompt"] and isinstance(
                                                        asset, dict
                                                    ):
                                                        message = asset.get("content") or asset.get("text")
                                                        break

                                            if not message and creative.get("inputs"):
                                                inputs = creative.get("inputs", [])
                                                if inputs and isinstance(inputs[0], dict):
                                                    message = inputs[0].get("context_description")

                                            # Extract promoted_offerings from assets if available
                                            promoted_offerings = None
                                            if creative.get("assets"):
                                                assets = creative.get("assets", {})
                                                for role, asset in assets.items():
                                                    if role == "promoted_offerings" and isinstance(asset, dict):
                                                        promoted_offerings = asset
                                                        break

                                            # Get existing context_id for refinement
                                            existing_context_id = None
                                            if existing_creative.data:
                                                existing_context_id = existing_creative.data.get(
                                                    "generative_context_id"
                                                )

                                            # Use provided context_id or existing one
                                            context_id = creative.get("context_id") or existing_context_id

                                            # Only call build_creative if we have a message (refinement)
                                            if message:
                                                logger.info(
                                                    f"[sync_creatives] Calling build_creative for update: "
                                                    f"{existing_creative.creative_id} format {creative_format} "
                                                    f"from agent {format_obj.agent_url}, "
                                                    f"message_length={len(message) if message else 0}, "
                                                    f"context_id={context_id}"
                                                )

                                                build_result = run_async_in_sync_context(
                                                    registry.build_creative(
                                                        agent_url=format_obj.agent_url,
                                                        format_id=creative_format,
                                                        message=message,
                                                        gemini_api_key=gemini_api_key,
                                                        promoted_offerings=promoted_offerings,
                                                        context_id=context_id,
                                                        finalize=creative.get("approved", False),
                                                    )
                                                )

                                                # Store build result in data
                                                if build_result:
                                                    data["generative_build_result"] = build_result
                                                    data["generative_status"] = build_result.get("status", "draft")
                                                    data["generative_context_id"] = build_result.get("context_id")
                                                    changes.append("generative_build_result")

                                                    # Extract creative output if available
                                                    if build_result.get("creative_output"):
                                                        creative_output = build_result["creative_output"]

                                                        # Only use generative assets if user didn't provide their own
                                                        user_provided_assets = creative.get("assets")
                                                        if creative_output.get("assets") and not user_provided_assets:
                                                            data["assets"] = creative_output["assets"]
                                                            changes.append("assets")
                                                            logger.info(
                                                                "[sync_creatives] Using assets from generative output (update)"
                                                            )
                                                        elif user_provided_assets:
                                                            logger.info(
                                                                "[sync_creatives] Preserving user-provided assets in update, "
                                                                "not overwriting with generative output"
                                                            )

                                                        if creative_output.get("output_format"):
                                                            output_format = creative_output["output_format"]
                                                            data["output_format"] = output_format
                                                            changes.append("output_format")

                                                            # Only use generative URL if user didn't provide one
                                                            if isinstance(output_format, dict) and output_format.get(
                                                                "url"
                                                            ):
                                                                if not data.get("url"):
                                                                    data["url"] = output_format["url"]
                                                                    changes.append("url")
                                                                    logger.info(
                                                                        f"[sync_creatives] Got URL from generative output (update): "
                                                                        f"{data['url']}"
                                                                    )
                                                                else:
                                                                    logger.info(
                                                                        "[sync_creatives] Preserving user-provided URL in update, "
                                                                        "not overwriting with generative output"
                                                                    )

                                                    logger.info(
                                                        f"[sync_creatives] Generative creative updated: "
                                                        f"status={data.get('generative_status')}, "
                                                        f"context_id={data.get('generative_context_id')}"
                                                    )
                                            else:
                                                logger.info(
                                                    "[sync_creatives] No message for generative update, "
                                                    "keeping existing creative data"
                                                )

                                            # Skip preview_creative call since we already have the output
                                            preview_result = None
                                        else:
                                            # Static creative - use preview_creative
                                            # Build creative manifest from available data
                                            # Extract string ID from FormatId object if needed
                                            format_id_str = (
                                                creative_format.id
                                                if hasattr(creative_format, "id")
                                                else str(creative_format)
                                            )
                                            creative_manifest = {
                                                "creative_id": existing_creative.creative_id,
                                                "name": creative.get("name") or existing_creative.name,
                                                "format_id": format_id_str,
                                            }

                                            # Add any provided asset data for validation
                                            # Validate assets are in dict format (AdCP v2.4+)
                                            if creative.get("assets"):
                                                validated_assets = _validate_creative_assets(creative.get("assets"))
                                                if validated_assets:
                                                    creative_manifest["assets"] = validated_assets
                                            if data.get("url"):
                                                creative_manifest["url"] = data.get("url")

                                            # Call creative agent's preview_creative for validation + preview
                                            # Extract string ID from FormatId object if needed
                                            format_id_str = (
                                                creative_format.id
                                                if hasattr(creative_format, "id")
                                                else str(creative_format)
                                            )
                                            logger.info(
                                                f"[sync_creatives] Calling preview_creative for validation (update): "
                                                f"{existing_creative.creative_id} format {format_id_str} "
                                                f"from agent {format_obj.agent_url}, has_assets={bool(creative.get('assets'))}, "
                                                f"has_url={bool(data.get('url'))}"
                                            )

                                            preview_result = run_async_in_sync_context(
                                                registry.preview_creative(
                                                    agent_url=format_obj.agent_url,
                                                    format_id=format_id_str,
                                                    creative_manifest=creative_manifest,
                                                )
                                            )

                                        # Extract preview data and store in data field
                                        if preview_result and preview_result.get("previews"):
                                            # Store full preview response for UI (per AdCP PR #119)
                                            # This preserves all variants and renders for UI display
                                            data["preview_response"] = preview_result
                                            changes.append("preview_response")

                                            # Also extract primary preview URL for backward compatibility
                                            first_preview = preview_result["previews"][0]
                                            renders = first_preview.get("renders", [])
                                            if renders:
                                                first_render = renders[0]

                                                # Store preview URL from render ONLY if we don't already have a URL from assets
                                                # This preserves user-provided URLs in assets instead of overwriting with preview URLs
                                                if first_render.get("preview_url") and not data.get("url"):
                                                    data["url"] = first_render["preview_url"]
                                                    changes.append("url")
                                                    logger.info(
                                                        f"[sync_creatives] Got preview URL from creative agent: {data['url']}"
                                                    )
                                                elif data.get("url"):
                                                    logger.info(
                                                        "[sync_creatives] Preserving user-provided URL from assets, "
                                                        "not overwriting with preview URL"
                                                    )

                                                # Extract dimensions from dimensions object
                                                # Only use preview dimensions if not already provided by user
                                                dimensions = first_render.get("dimensions", {})
                                                if dimensions.get("width") and not data.get("width"):
                                                    data["width"] = dimensions["width"]
                                                    changes.append("width")
                                                if dimensions.get("height") and not data.get("height"):
                                                    data["height"] = dimensions["height"]
                                                    changes.append("height")
                                                if dimensions.get("duration") and not data.get("duration"):
                                                    data["duration"] = dimensions["duration"]
                                                    changes.append("duration")

                                        logger.info(
                                            f"[sync_creatives] Preview data populated for update: "
                                            f"url={bool(data.get('url'))}, "
                                            f"width={data.get('width')}, "
                                            f"height={data.get('height')}, "
                                            f"variants={len(preview_result.get('previews', []) if preview_result else [])}"
                                        )
                                    else:
                                        # Preview generation returned no previews
                                        # Only acceptable if creative has a media_url (direct URL to creative asset)
                                        has_media_url = bool(creative.get("url") or data.get("url"))

                                        if has_media_url:
                                            # Static creatives with media_url don't need previews
                                            warning_msg = f"Preview generation returned no previews for {existing_creative.creative_id} (static creative with media_url)"
                                            logger.warning(f"[sync_creatives] {warning_msg}")
                                            # Continue with update - preview is optional for static creatives
                                        else:
                                            # Creative agent should have generated previews but didn't
                                            error_msg = f"Preview generation failed for {existing_creative.creative_id}: no previews returned and no media_url provided"
                                            logger.error(f"[sync_creatives] {error_msg}")
                                            failed_creatives.append(
                                                {
                                                    "creative_id": existing_creative.creative_id,
                                                    "error": error_msg,
                                                    "format": creative_format,
                                                }
                                            )
                                            failed_count += 1
                                            results.append(
                                                SyncCreativeResult(
                                                    creative_id=existing_creative.creative_id,
                                                    action="failed",
                                                    status=None,
                                                    platform_id=None,
                                                    errors=[error_msg],
                                                    review_feedback=None,
                                                    assigned_to=None,
                                                    assignment_errors=None,
                                                )
                                            )
                                            continue  # Skip this creative, move to next

                                except Exception as validation_error:
                                    # Creative agent validation failed for update (network error, agent down, etc.)
                                    # Do NOT update the creative - it needs validation before acceptance
                                    error_msg = (
                                        f"Creative agent unreachable or validation error: {str(validation_error)}. "
                                        f"Retry recommended - creative agent may be temporarily unavailable."
                                    )
                                    logger.error(
                                        f"[sync_creatives] {error_msg} for update of {existing_creative.creative_id}",
                                        exc_info=True,
                                    )
                                    failed_creatives.append(
                                        {
                                            "creative_id": existing_creative.creative_id,
                                            "error": error_msg,
                                            "format": creative_format,
                                        }
                                    )
                                    failed_count += 1
                                    results.append(
                                        SyncCreativeResult(
                                            creative_id=existing_creative.creative_id,
                                            action="failed",
                                            status=None,
                                            platform_id=None,
                                            errors=[error_msg],
                                            review_feedback=None,
                                            assigned_to=None,
                                            assignment_errors=None,
                                        )
                                    )
                                    continue  # Skip this creative update

                            # In full upsert, consider all fields as changed
                            changes.extend(["url", "click_url", "width", "height", "duration"])

                        existing_creative.data = data

                        # Mark JSONB field as modified for SQLAlchemy
                        from sqlalchemy.orm import attributes

                        attributes.flag_modified(existing_creative, "data")

                        # Track creatives needing approval for workflow creation
                        if needs_approval:
                            creative_info = {
                                "creative_id": existing_creative.creative_id,
                                "format": creative_format,
                                "name": creative.get("name"),
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

                        # Record result for updated creative
                        from typing import Literal

                        action: Literal["updated", "unchanged"] = "updated" if changes else "unchanged"
                        if action == "updated":
                            updated_count += 1
                        else:
                            unchanged_count += 1

                        results.append(
                            SyncCreativeResult(
                                creative_id=existing_creative.creative_id,
                                action=action,
                                status=existing_creative.status,
                                platform_id=None,
                                changes=changes,
                                review_feedback=None,
                                assigned_to=None,
                                assignment_errors=None,
                            )
                        )

                    else:
                        # Create new creative
                        from src.core.database.models import Creative as DBCreative

                        # Extract creative_id for error reporting (must be defined before any validation)
                        creative_id = creative.get("creative_id", "unknown")

                        # Prepare data field with all creative properties
                        # Extract URL from assets if not provided at top level
                        url = creative.get("url")
                        if not url and creative.get("assets"):
                            assets = creative["assets"]

                            # Priority 1: Try common asset_ids
                            for priority_key in ["main", "image", "video", "creative", "content"]:
                                if priority_key in assets and isinstance(assets[priority_key], dict):
                                    url = assets[priority_key].get("url")
                                    if url:
                                        logger.debug(
                                            f"[sync_creatives] Extracted URL from assets.{priority_key}.url for create"
                                        )
                                        break

                            # Priority 2: First available asset URL
                            if not url:
                                for asset_id, asset_data in assets.items():
                                    if isinstance(asset_data, dict) and asset_data.get("url"):
                                        url = asset_data["url"]
                                        logger.debug(
                                            f"[sync_creatives] Extracted URL from assets.{asset_id}.url for create (fallback)"
                                        )
                                        break

                        data = {
                            "url": url,
                            "click_url": creative.get("click_url"),
                            "width": creative.get("width"),
                            "height": creative.get("height"),
                            "duration": creative.get("duration"),
                        }

                        # Store user-provided assets for preservation check
                        user_provided_assets = creative.get("assets")
                        if user_provided_assets:
                            data["assets"] = user_provided_assets

                        # Add AdCP v1.3+ fields to data
                        if creative.get("snippet"):
                            data["snippet"] = creative.get("snippet")
                            data["snippet_type"] = creative.get("snippet_type")

                        if creative.get("template_variables"):
                            data["template_variables"] = creative.get("template_variables")

                        # ALWAYS validate creatives with the creative agent (validation + preview generation)
                        creative_format = creative.get("format_id") or creative.get("format")
                        if creative_format:
                            try:
                                # Use pre-fetched formats (fetched outside transaction at function start)
                                # This avoids async HTTP calls inside savepoint

                                # Find matching format
                                format_obj = None
                                for fmt in all_formats:
                                    if fmt.format_id == creative_format:
                                        format_obj = fmt
                                        break

                                if format_obj and format_obj.agent_url:
                                    # Check if format is generative (has output_format_ids)
                                    is_generative = bool(getattr(format_obj, "output_format_ids", None))

                                    if is_generative:
                                        # Generative creative - call build_creative
                                        logger.info(
                                            f"[sync_creatives] Detected generative format: {creative_format}, "
                                            f"checking for Gemini API key"
                                        )

                                        # Get Gemini API key from config
                                        from src.core.config import get_config

                                        config = get_config()
                                        gemini_api_key = config.gemini_api_key

                                        if not gemini_api_key:
                                            error_msg = (
                                                f"Cannot build generative creative {creative_format}: "
                                                f"GEMINI_API_KEY not configured"
                                            )
                                            logger.error(f"[sync_creatives] {error_msg}")
                                            raise ValueError(error_msg)

                                        # Extract message/brief from assets or inputs
                                        message = None
                                        if creative.get("assets"):
                                            assets = creative.get("assets", {})
                                            for role, asset in assets.items():
                                                if role in ["message", "brief", "prompt"] and isinstance(asset, dict):
                                                    message = asset.get("content") or asset.get("text")
                                                    break

                                        if not message and creative.get("inputs"):
                                            inputs = creative.get("inputs", [])
                                            if inputs and isinstance(inputs[0], dict):
                                                message = inputs[0].get("context_description")

                                        if not message:
                                            message = f"Create a creative for: {creative.get('name')}"
                                            logger.warning(
                                                "[sync_creatives] No message found in assets/inputs, "
                                                "using creative name as fallback"
                                            )

                                        # Extract promoted_offerings from assets if available
                                        promoted_offerings = None
                                        if creative.get("assets"):
                                            assets = creative.get("assets", {})
                                            for role, asset in assets.items():
                                                if role == "promoted_offerings" and isinstance(asset, dict):
                                                    promoted_offerings = asset
                                                    break

                                        # Call build_creative
                                        # Extract string ID from FormatId object if needed
                                        format_id_str = (
                                            creative_format.id
                                            if hasattr(creative_format, "id")
                                            else str(creative_format)
                                        )
                                        logger.info(
                                            f"[sync_creatives] Calling build_creative for generative format: "
                                            f"{format_id_str} from agent {format_obj.agent_url}, "
                                            f"message_length={len(message) if message else 0}"
                                        )

                                        build_result = run_async_in_sync_context(
                                            registry.build_creative(
                                                agent_url=format_obj.agent_url,
                                                format_id=format_id_str,
                                                message=message,
                                                gemini_api_key=gemini_api_key,
                                                promoted_offerings=promoted_offerings,
                                                context_id=creative.get("context_id"),
                                                finalize=creative.get("approved", False),
                                            )
                                        )

                                        # Store build result
                                        if build_result:
                                            data["generative_build_result"] = build_result
                                            data["generative_status"] = build_result.get("status", "draft")
                                            data["generative_context_id"] = build_result.get("context_id")

                                            # Extract creative output
                                            if build_result.get("creative_output"):
                                                creative_output = build_result["creative_output"]

                                                # Only use generative assets if user didn't provide their own
                                                if creative_output.get("assets") and not user_provided_assets:
                                                    data["assets"] = creative_output["assets"]
                                                    logger.info("[sync_creatives] Using assets from generative output")
                                                elif user_provided_assets:
                                                    logger.info(
                                                        "[sync_creatives] Preserving user-provided assets, "
                                                        "not overwriting with generative output"
                                                    )

                                                if creative_output.get("output_format"):
                                                    output_format = creative_output["output_format"]
                                                    data["output_format"] = output_format

                                                    # Only use generative URL if user didn't provide one
                                                    if isinstance(output_format, dict) and output_format.get("url"):
                                                        if not data.get("url"):
                                                            data["url"] = output_format["url"]
                                                            logger.info(
                                                                f"[sync_creatives] Got URL from generative output: "
                                                                f"{data['url']}"
                                                            )
                                                        else:
                                                            logger.info(
                                                                "[sync_creatives] Preserving user-provided URL, "
                                                                "not overwriting with generative output"
                                                            )

                                            logger.info(
                                                f"[sync_creatives] Generative creative built: "
                                                f"status={data.get('generative_status')}, "
                                                f"context_id={data.get('generative_context_id')}"
                                            )

                                        # Skip preview_creative call since we already have the output
                                        preview_result = None
                                    else:
                                        # Static creative - use preview_creative
                                        # Build creative manifest from available data
                                        # Extract string ID from FormatId object if needed
                                        format_id_str = (
                                            creative_format.id
                                            if hasattr(creative_format, "id")
                                            else str(creative_format)
                                        )
                                        creative_manifest = {
                                            "creative_id": creative.get("creative_id") or str(uuid.uuid4()),
                                            "name": creative.get("name"),
                                            "format_id": format_id_str,
                                        }

                                        # Add any provided asset data for validation
                                        # Validate assets are in dict format (AdCP v2.4+)
                                        if creative.get("assets"):
                                            validated_assets = _validate_creative_assets(creative.get("assets"))
                                            if validated_assets:
                                                creative_manifest["assets"] = validated_assets
                                        if data.get("url"):
                                            creative_manifest["url"] = data.get("url")

                                        # Call creative agent's preview_creative for validation + preview
                                        # Extract string ID from FormatId object if needed
                                        format_id_str = (
                                            creative_format.id
                                            if hasattr(creative_format, "id")
                                            else str(creative_format)
                                        )
                                        logger.info(
                                            f"[sync_creatives] Calling preview_creative for validation: {format_id_str} "
                                            f"from agent {format_obj.agent_url}, has_assets={bool(creative.get('assets'))}, "
                                            f"has_url={bool(data.get('url'))}"
                                        )

                                        preview_result = run_async_in_sync_context(
                                            registry.preview_creative(
                                                agent_url=format_obj.agent_url,
                                                format_id=format_id_str,
                                                creative_manifest=creative_manifest,
                                            )
                                        )

                                    # Extract preview data and store in data field
                                    if preview_result and preview_result.get("previews"):
                                        # Store full preview response for UI (per AdCP PR #119)
                                        # This preserves all variants and renders for UI display
                                        data["preview_response"] = preview_result

                                        # Also extract primary preview URL for backward compatibility
                                        first_preview = preview_result["previews"][0]
                                        renders = first_preview.get("renders", [])
                                        if renders:
                                            first_render = renders[0]

                                            # Only use preview URL if user didn't provide one
                                            if first_render.get("preview_url") and not data.get("url"):
                                                data["url"] = first_render["preview_url"]
                                                logger.info(
                                                    f"[sync_creatives] Got preview URL from creative agent: {data['url']}"
                                                )
                                            elif data.get("url"):
                                                logger.info(
                                                    "[sync_creatives] Preserving user-provided URL from assets, "
                                                    "not overwriting with preview URL"
                                                )

                                            # Only use preview dimensions if user didn't provide them
                                            dimensions = first_render.get("dimensions", {})
                                            if dimensions.get("width") and not data.get("width"):
                                                data["width"] = dimensions["width"]
                                            if dimensions.get("height") and not data.get("height"):
                                                data["height"] = dimensions["height"]
                                            if dimensions.get("duration") and not data.get("duration"):
                                                data["duration"] = dimensions["duration"]

                                        logger.info(
                                            f"[sync_creatives] Preview data populated: "
                                            f"url={bool(data.get('url'))}, "
                                            f"width={data.get('width')}, "
                                            f"height={data.get('height')}, "
                                            f"variants={len(preview_result.get('previews', []))}"
                                        )
                                    else:
                                        # Preview generation returned no previews
                                        # Only acceptable if creative has a media_url (direct URL to creative asset)
                                        has_media_url = bool(creative.get("url") or data.get("url"))

                                        if has_media_url:
                                            # Static creatives with media_url don't need previews
                                            warning_msg = f"Preview generation returned no previews for {creative_id} (static creative with media_url)"
                                            logger.warning(f"[sync_creatives] {warning_msg}")
                                            # Continue with creative creation - preview is optional for static creatives
                                        else:
                                            # Creative agent should have generated previews but didn't
                                            error_msg = f"Preview generation failed for {creative_id}: no previews returned and no media_url provided"
                                            logger.error(f"[sync_creatives] {error_msg}")
                                            failed_creatives.append(
                                                {
                                                    "creative_id": creative_id,
                                                    "error": error_msg,
                                                    "format": creative_format,
                                                }
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
                                            continue  # Skip this creative, move to next

                            except Exception as validation_error:
                                # Creative agent validation failed (network error, agent down, etc.)
                                # Do NOT store the creative - it needs validation before acceptance
                                error_msg = (
                                    f"Creative agent unreachable or validation error: {str(validation_error)}. "
                                    f"Retry recommended - creative agent may be temporarily unavailable."
                                )
                                logger.error(
                                    f"[sync_creatives] {error_msg} - rejecting creative {creative_id}",
                                    exc_info=True,
                                )
                                failed_creatives.append(
                                    {
                                        "creative_id": creative_id,
                                        "error": error_msg,
                                        "format": creative_format,
                                    }
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
                                continue  # Skip storing this creative

                        # Determine creative status based on approval mode

                        # Create initial creative with pending_review status (will be updated based on approval mode)
                        creative_status = CreativeStatusEnum.pending_review.value
                        needs_approval = False

                        # Extract agent_url and format ID from format_id field
                        # Use validated format_value (already auto-upgraded from string)
                        agent_url, format_id = _extract_format_namespace(format_value)

                        db_creative = DBCreative(
                            tenant_id=tenant["tenant_id"],
                            creative_id=creative.get("creative_id") or str(uuid.uuid4()),
                            name=creative.get("name"),
                            agent_url=agent_url,
                            format=format_id,
                            principal_id=principal_id,
                            status=creative_status,
                            created_at=datetime.now(UTC),
                            data=data,
                        )

                        session.add(db_creative)
                        session.flush()  # Get the ID

                        # Update creative_id if it was generated
                        if not creative.get("creative_id"):
                            creative["creative_id"] = db_creative.creative_id

                        # Now apply approval mode logic
                        if approval_mode == "auto-approve":
                            db_creative.status = CreativeStatusEnum.approved.value
                            needs_approval = False
                        elif approval_mode == "ai-powered":
                            # Submit to background AI review (async)

                            from src.admin.blueprints.creatives import (
                                _ai_review_executor,
                                _ai_review_lock,
                                _ai_review_tasks,
                            )

                            # Set status to pending_review for AI review
                            db_creative.status = CreativeStatusEnum.pending_review.value
                            needs_approval = True

                            # Submit background task
                            task_id = f"ai_review_{db_creative.creative_id}_{uuid.uuid4().hex[:8]}"

                            # Import the async function
                            from src.admin.blueprints.creatives import _ai_review_creative_async

                            future = _ai_review_executor.submit(
                                _ai_review_creative_async,
                                creative_id=db_creative.creative_id,
                                tenant_id=tenant["tenant_id"],
                                webhook_url=webhook_url,
                                slack_webhook_url=tenant.get("slack_webhook_url"),
                                principal_name=principal_id,
                            )

                            # Track the task
                            with _ai_review_lock:
                                _ai_review_tasks[task_id] = {
                                    "future": future,
                                    "creative_id": db_creative.creative_id,
                                    "created_at": time.time(),
                                }

                            logger.info(
                                f"[sync_creatives] Submitted AI review for new creative {db_creative.creative_id} (task: {task_id})"
                            )
                        else:  # require-human
                            db_creative.status = CreativeStatusEnum.pending_review.value
                            needs_approval = True

                        # Track creatives needing approval for workflow creation
                        if needs_approval:
                            creative_info = {
                                "creative_id": db_creative.creative_id,
                                "format": creative_format,
                                "name": creative.get("name"),
                                "status": db_creative.status,  # Include status for Slack notification
                            }
                            # AI review reason will be added asynchronously when review completes
                            # No ai_result available yet in async mode
                            creatives_needing_approval.append(creative_info)

                        # Record result for created creative
                        created_count += 1
                        results.append(
                            SyncCreativeResult(
                                creative_id=db_creative.creative_id,
                                action="created",
                                status=db_creative.status,
                                platform_id=None,
                                review_feedback=None,
                                assigned_to=None,
                                assignment_errors=None,
                            )
                        )

                    # If we reach here, creative processing succeeded
                    synced_creatives.append(creative)

            except Exception as e:
                # Savepoint automatically rolls back this creative only
                creative_id = creative.get("creative_id", "unknown")
                error_msg = str(e)
                failed_creatives.append({"creative_id": creative_id, "name": creative.get("name"), "error": error_msg})
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
    assignment_list = []
    # Track assignments per creative for response population
    assignments_by_creative: dict[str, list[str]] = {}  # creative_id -> [package_ids]
    assignment_errors_by_creative: dict[str, dict[str, str]] = {}  # creative_id -> {package_id: error}

    # Note: assignments should be a dict, but handle both dict and None
    if assignments and isinstance(assignments, dict):
        with get_db_session() as session:
            from src.core.database.models import CreativeAssignment as DBAssignment
            from src.core.database.models import MediaBuy, MediaPackage
            from src.core.schemas import CreativeAssignment

            for creative_id, package_ids in assignments.items():
                # Initialize tracking for this creative
                if creative_id not in assignments_by_creative:
                    assignments_by_creative[creative_id] = []
                if creative_id not in assignment_errors_by_creative:
                    assignment_errors_by_creative[creative_id] = {}

                for package_id in package_ids:
                    # Find which media buy this package belongs to by querying MediaPackage table
                    # Note: We need to join with MediaBuy to verify tenant_id
                    from sqlalchemy import join

                    package_stmt = (
                        select(MediaPackage, MediaBuy)
                        .select_from(join(MediaPackage, MediaBuy, MediaPackage.media_buy_id == MediaBuy.media_buy_id))
                        .where(MediaPackage.package_id == package_id)
                        .where(MediaBuy.tenant_id == tenant["tenant_id"])
                    )
                    result = session.execute(package_stmt).first()

                    media_buy_id = None
                    actual_package_id = None
                    if result:
                        db_package, db_media_buy = result
                        media_buy_id = db_package.media_buy_id
                        actual_package_id = db_package.package_id

                    if not media_buy_id:
                        # Package not found - record error
                        error_msg = f"Package not found: {package_id}"
                        assignment_errors_by_creative[creative_id][package_id] = error_msg

                        # Skip if in lenient mode, error if strict
                        if validation_mode == "strict":
                            raise ToolError(error_msg)
                        else:
                            logger.warning(f"Package not found during assignment: {package_id}, skipping")
                            continue

                    # Validate creative format against package product formats
                    # Get creative format
                    from src.core.database.models import Creative as DBCreative
                    from src.core.database.models import Product

                    creative_stmt = select(DBCreative).where(
                        DBCreative.tenant_id == tenant["tenant_id"], DBCreative.creative_id == creative_id
                    )
                    db_creative_result = session.scalars(creative_stmt).first()

                    # Get product_id from package_config
                    product_id = db_package.package_config.get("product_id") if db_package.package_config else None

                    if db_creative_result and product_id:
                        # Get product formats
                        product_stmt = select(Product).where(
                            Product.tenant_id == tenant["tenant_id"], Product.product_id == product_id
                        )
                        product = session.scalars(product_stmt).first()

                        if product and product.formats:
                            # Build set of supported formats (agent_url, format_id) tuples
                            supported_formats: set[tuple[str, str]] = set()
                            for fmt in product.formats:
                                if isinstance(fmt, dict):
                                    agent_url_val = fmt.get("agent_url")
                                    format_id_val = fmt.get("id") or fmt.get("format_id")
                                    if agent_url_val and format_id_val:
                                        supported_formats.add((str(agent_url_val), str(format_id_val)))

                            # Check creative format against supported formats
                            creative_agent_url = db_creative_result.agent_url
                            creative_format_id = db_creative_result.format

                            # Allow /mcp URL variant (creative agent may return format with /mcp suffix)
                            def normalize_url(url: str | None) -> str | None:
                                if not url:
                                    return None
                                return url.rstrip("/").removesuffix("/mcp")

                            normalized_creative_url = normalize_url(creative_agent_url)
                            is_supported = False

                            for supported_url, supported_format_id in supported_formats:
                                normalized_supported_url = normalize_url(supported_url)
                                if (
                                    normalized_creative_url == normalized_supported_url
                                    and creative_format_id == supported_format_id
                                ):
                                    is_supported = True
                                    break

                            if not supported_formats:
                                # Product has no format restrictions - allow all
                                is_supported = True

                            if not is_supported:
                                # Creative format not supported by product
                                creative_format_display = (
                                    f"{creative_agent_url}/{creative_format_id}"
                                    if creative_agent_url
                                    else creative_format_id
                                )
                                supported_formats_display = ", ".join(
                                    [f"{url}/{fmt_id}" if url else fmt_id for url, fmt_id in supported_formats]
                                )
                                error_msg = (
                                    f"Creative {creative_id} format '{creative_format_display}' "
                                    f"is not supported by product '{product.name}' (package {package_id}). "
                                    f"Supported formats: {supported_formats_display}"
                                )
                                assignment_errors_by_creative[creative_id][package_id] = error_msg

                                if validation_mode == "strict":
                                    raise ToolError(error_msg)
                                else:
                                    logger.warning(f"Creative format mismatch during assignment, skipping: {error_msg}")
                                    continue

                    # Check if assignment already exists (idempotent operation)
                    stmt_existing = select(DBAssignment).filter_by(
                        tenant_id=tenant["tenant_id"],
                        media_buy_id=media_buy_id,
                        package_id=actual_package_id,
                        creative_id=creative_id,
                    )
                    existing_assignment = session.scalars(stmt_existing).first()

                    if existing_assignment:
                        # Assignment already exists - update weight if needed
                        if existing_assignment.weight != 100:
                            existing_assignment.weight = 100
                            logger.info(
                                f"Updated existing assignment: creative={creative_id}, "
                                f"package={actual_package_id}, media_buy={media_buy_id}"
                            )
                        assignment = existing_assignment
                    else:
                        # Create new assignment in creative_assignments table
                        assignment = DBAssignment(
                            tenant_id=tenant["tenant_id"],
                            assignment_id=str(uuid.uuid4()),
                            media_buy_id=media_buy_id,
                            package_id=actual_package_id,  # Use resolved package_id
                            creative_id=creative_id,
                            weight=100,
                            created_at=datetime.now(UTC),
                        )
                        session.add(assignment)
                        logger.info(
                            f"Created new assignment: creative={creative_id}, "
                            f"package={actual_package_id}, media_buy={media_buy_id}"
                        )

                    assignment_list.append(
                        CreativeAssignment(
                            assignment_id=assignment.assignment_id,
                            media_buy_id=assignment.media_buy_id,
                            package_id=assignment.package_id,
                            creative_id=assignment.creative_id,
                            weight=assignment.weight,
                        )
                    )

                    # Track successful assignment
                    if actual_package_id is not None:
                        assignments_by_creative[creative_id].append(actual_package_id)

            session.commit()

    # Update creative results with assignment information (per AdCP spec)
    for sync_result in results:
        if sync_result.creative_id in assignments_by_creative:
            assigned_packages = assignments_by_creative[sync_result.creative_id]
            if assigned_packages:
                sync_result.assigned_to = assigned_packages

        if sync_result.creative_id in assignment_errors_by_creative:
            errors = assignment_errors_by_creative[sync_result.creative_id]
            if errors:
                sync_result.assignment_errors = errors

    # Create workflow steps for creatives requiring approval
    if creatives_needing_approval:
        from src.core.context_manager import get_context_manager
        from src.core.database.models import ObjectWorkflowMapping

        ctx_manager = get_context_manager()

        # Ensure principal_id is available (should always be set by this point)
        if principal_id is None:
            raise ToolError("Principal ID required for workflow creation")

        # Get or create persistent context for this operation
        # is_async=True because we're creating workflow steps that need tracking
        persistent_ctx = ctx_manager.get_or_create_context(
            principal_id=principal_id, tenant_id=tenant["tenant_id"], is_async=True
        )

        if persistent_ctx is None:
            raise ToolError("Failed to create workflow context")

        with get_db_session() as session:
            for creative_info in creatives_needing_approval:
                # Build appropriate comment based on status
                status = creative_info.get("status", CreativeStatusEnum.pending_review.value)
                if status == CreativeStatusEnum.rejected.value:
                    comment = f"Creative '{creative_info['name']}' (format: {creative_info['format']}) was rejected by AI review"
                elif status == CreativeStatusEnum.pending_review.value:
                    if approval_mode == "ai-powered":
                        comment = f"Creative '{creative_info['name']}' (format: {creative_info['format']}) requires human review per AI recommendation"
                    else:
                        comment = f"Creative '{creative_info['name']}' (format: {creative_info['format']}) requires manual approval"
                else:
                    comment = f"Creative '{creative_info['name']}' (format: {creative_info['format']}) requires review"

                # Create workflow step for creative approval
                request_data_for_workflow = {
                    "creative_id": creative_info["creative_id"],
                    "format": creative_info["format"],
                    "name": creative_info["name"],
                    "status": status,
                    "approval_mode": approval_mode,
                }
                # Store push_notification_config if provided for async notification
                if push_notification_config:
                    request_data_for_workflow["push_notification_config"] = push_notification_config

                step = ctx_manager.create_workflow_step(
                    context_id=persistent_ctx.context_id,
                    step_type="creative_approval",
                    owner="publisher",
                    status="requires_approval",
                    tool_name="sync_creatives",
                    request_data=request_data_for_workflow,
                    initial_comment=comment,
                )

                # Create ObjectWorkflowMapping to link creative to workflow step
                # This is CRITICAL for webhook delivery when creative is approved
                mapping = ObjectWorkflowMapping(
                    step_id=step.step_id,
                    object_type="creative",
                    object_id=creative_info["creative_id"],
                    action="approval_required",
                )
                session.add(mapping)

            session.commit()
            logger.info(f"📋 Created {len(creatives_needing_approval)} workflow steps for creative approval")

        # Send Slack notification for pending/rejected creative reviews
        # Note: For ai-powered mode, notifications are sent AFTER AI review completes (with AI reasoning)
        # Only send immediate notifications for require-human mode or existing creatives with AI review results
        logger.info(
            f"Checking Slack notification: creatives={len(creatives_needing_approval)}, webhook={tenant.get('slack_webhook_url')}, approval_mode={approval_mode}"
        )
        if creatives_needing_approval and tenant.get("slack_webhook_url") and approval_mode == "require-human":
            from src.services.slack_notifier import get_slack_notifier

            logger.info(
                f"Sending Slack notifications for {len(creatives_needing_approval)} creatives (require-human mode)"
            )
            tenant_config = {"features": {"slack_webhook_url": tenant["slack_webhook_url"]}}
            notifier = get_slack_notifier(tenant_config)

            for creative_info in creatives_needing_approval:
                status = creative_info.get("status", CreativeStatusEnum.pending_review.value)
                ai_review_reason = creative_info.get("ai_review_reason")

                # Ensure required fields are strings
                creative_id_str = str(creative_info.get("creative_id", "unknown"))
                format_str = str(creative_info.get("format", "unknown"))
                principal_name_str = str(principal_id) if principal_id else "unknown"

                if status == CreativeStatusEnum.rejected.value:
                    # For rejected creatives, send a different notification
                    # TODO: Add notify_creative_rejected method to SlackNotifier
                    notifier.notify_creative_pending(
                        creative_id=creative_id_str,
                        principal_name=principal_name_str,
                        format_type=format_str,
                        media_buy_id=None,
                        tenant_id=tenant["tenant_id"],
                        ai_review_reason=ai_review_reason,
                    )
                else:
                    # For pending creatives (human review required)
                    notifier.notify_creative_pending(
                        creative_id=creative_id_str,
                        principal_name=principal_name_str,
                        format_type=format_str,
                        media_buy_id=None,
                        tenant_id=tenant["tenant_id"],
                        ai_review_reason=ai_review_reason,
                    )

    # Audit logging
    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])

    # Build error message from failed creatives
    error_message = None
    if failed_creatives:
        error_lines = []
        for fc in failed_creatives[:5]:  # Limit to first 5 errors to avoid huge messages
            creative_id = fc.get("creative_id", "unknown")
            error_text = fc.get("error", "Unknown error")
            error_lines.append(f"{creative_id}: {error_text}")
        error_message = "; ".join(error_lines)
        if len(failed_creatives) > 5:
            error_message += f" (and {len(failed_creatives) - 5} more)"

    # Ensure principal_id is string for audit logging
    principal_id_str = str(principal_id) if principal_id else "unknown"

    audit_logger.log_operation(
        operation="sync_creatives",
        principal_name=principal_id_str,
        principal_id=principal_id_str,
        adapter_id="N/A",
        success=len(failed_creatives) == 0,
        error=error_message,
        details={
            "synced_count": len(synced_creatives),
            "failed_count": len(failed_creatives),
            "assignment_count": len(assignment_list),
            "patch_mode": patch,
            "dry_run": dry_run,
        },
    )

    # Log activity
    # Activity logging imported at module level
    if context is not None:
        log_tool_activity(context, "sync_creatives", start_time)

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

    # Log audit trail for sync_creatives operation
    try:
        with get_db_session() as audit_session:
            from src.core.database.models import Principal as DBPrincipal

            # Get principal info for audit log
            principal_stmt = select(DBPrincipal).filter_by(tenant_id=tenant["tenant_id"], principal_id=principal_id)
            principal = audit_session.scalars(principal_stmt).first()

            if principal:
                # Create audit logger and log the operation
                audit_logger = get_audit_logger("sync_creatives", tenant["tenant_id"])
                principal_id_str = str(principal_id) if principal_id else "unknown"
                audit_logger.log_operation(
                    operation="sync_creatives",
                    principal_name=principal.name,
                    principal_id=principal_id_str,
                    adapter_id=principal_id_str,  # Use principal_id as adapter_id for consistency
                    success=(failed_count == 0),
                    details={
                        "created_count": created_count,
                        "updated_count": updated_count,
                        "unchanged_count": unchanged_count,
                        "failed_count": failed_count,
                        "assignment_count": len(assignment_list) if assignment_list else 0,
                        "approval_required_count": len(creatives_needing_approval),
                        "dry_run": dry_run,
                        "patch_mode": patch,
                    },
                    tenant_id=tenant["tenant_id"],
                )
    except Exception as e:
        # Don't fail the operation if audit logging fails
        logger.warning(f"Failed to write audit log for sync_creatives: {e}")

    # Build AdCP-compliant response (per official spec)
    return SyncCreativesResponse(
        creatives=results,
        dry_run=dry_run,
    )


async def sync_creatives(
    creatives: list[dict],
    patch: bool = False,
    assignments: dict | None = None,
    delete_missing: bool = False,
    dry_run: bool = False,
    validation_mode: str = "strict",
    push_notification_config: dict | None = None,
    context: Context | ToolContext | None = None,
):
    """Sync creative assets to centralized library (AdCP v2.4 spec compliant endpoint).

    MCP tool wrapper that delegates to the shared implementation.

    Args:
        creatives: List of creative objects to sync
        patch: When true, only update provided fields (partial update). When false, full upsert.
        assignments: Bulk assignment map of creative_id to package_ids (spec-compliant)
        delete_missing: Delete creatives not in sync payload (use with caution)
        dry_run: Preview changes without applying them
        validation_mode: Validation strictness (strict or lenient)
        push_notification_config: Push notification config for async notifications (AdCP spec, optional)
        context: FastMCP context (automatically provided)

    Returns:
        ToolResult with SyncCreativesResponse data
    """
    response = _sync_creatives_impl(
        creatives=creatives,
        patch=patch,
        assignments=assignments,
        delete_missing=delete_missing,
        dry_run=dry_run,
        validation_mode=validation_mode,
        push_notification_config=push_notification_config,
        context=context,
    )
    return ToolResult(content=str(response), structured_content=response.model_dump())


def _list_creatives_impl(
    media_buy_id: str | None = None,
    buyer_ref: str | None = None,
    status: str | None = None,
    format: str | None = None,
    tags: list[str] | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    search: str | None = None,
    filters: dict | None = None,
    sort: dict | None = None,
    pagination: dict | None = None,
    fields: list[str] | None = None,
    include_performance: bool = False,
    include_assignments: bool = False,
    include_sub_assets: bool = False,
    page: int = 1,
    limit: int = 50,
    sort_by: str = "created_date",
    sort_order: str = "desc",
    context: Context | ToolContext | None = None,
) -> ListCreativesResponse:
    """List and search creative library (AdCP spec endpoint).

    Advanced filtering and search endpoint for the centralized creative library.
    Supports pagination, sorting, and multiple filter criteria.

    Args:
        media_buy_id: Filter by media buy ID (optional)
        buyer_ref: Filter by buyer reference (optional)
        status: Filter by creative status (pending, approved, rejected) (optional)
        format: Filter by creative format (optional)
        tags: Filter by tags (optional)
        created_after: Filter by creation date (ISO string) (optional)
        created_before: Filter by creation date (ISO string) (optional)
        search: Search in creative names and descriptions (optional)
        filters: Advanced filtering options (nested object, optional)
        sort: Sort configuration (nested object, optional)
        pagination: Pagination parameters (nested object, optional)
        fields: Specific fields to return (optional)
        include_performance: Include performance metrics (optional)
        include_assignments: Include package assignments (optional)
        include_sub_assets: Include sub-assets (optional)
        page: Page number for pagination (default: 1)
        limit: Number of results per page (default: 50, max: 1000)
        sort_by: Sort field (created_date, name, status) (default: created_date)
        sort_order: Sort order (asc, desc) (default: desc)
        context: FastMCP context (automatically provided)

    Returns:
        ListCreativesResponse with filtered creative assets and pagination info
    """
    from src.core.schemas import ListCreativesRequest

    # Parse datetime strings if provided
    created_after_dt = None
    created_before_dt = None
    if created_after:
        try:
            created_after_dt = datetime.fromisoformat(created_after.replace("Z", "+00:00"))
        except ValueError:
            raise ToolError(f"Invalid created_after date format: {created_after}")
    if created_before:
        try:
            created_before_dt = datetime.fromisoformat(created_before.replace("Z", "+00:00"))
        except ValueError:
            raise ToolError(f"Invalid created_before date format: {created_before}")

    # Create request object from individual parameters (MCP-compliant)
    # Validate sort_order is valid Literal
    from typing import Literal, cast

    valid_sort_order: Literal["asc", "desc"] = cast(
        Literal["asc", "desc"], sort_order if sort_order in ["asc", "desc"] else "desc"
    )

    try:
        req = ListCreativesRequest(
            media_buy_id=media_buy_id,
            buyer_ref=buyer_ref,
            status=status,
            format=format,
            tags=tags or [],
            created_after=created_after_dt,
            created_before=created_before_dt,
            search=search,
            filters=filters,
            sort=sort,
            pagination=pagination,
            fields=fields,
            include_performance=include_performance,
            include_assignments=include_assignments,
            include_sub_assets=include_sub_assets,
            page=page,
            limit=min(limit, 1000),  # Enforce max limit
            sort_by=sort_by,
            sort_order=valid_sort_order,
        )
    except ValidationError as e:
        raise ToolError(format_validation_error(e, context="list_creatives request")) from e

    start_time = time.time()

    # Authentication - REQUIRED (creatives contain sensitive data)
    # Unlike discovery endpoints (list_creative_formats), this returns actual creative assets
    # which are principal-specific and must be access-controlled
    principal_id = get_principal_id_from_context(context)
    if not principal_id:
        raise ToolError("Missing x-adcp-auth header")

    # Get tenant information
    tenant = get_current_tenant()
    if not tenant:
        raise ToolError("No tenant context available")

    creatives = []
    total_count = 0

    with get_db_session() as session:
        from src.core.database.models import Creative as DBCreative
        from src.core.database.models import CreativeAssignment as DBAssignment
        from src.core.database.models import MediaBuy

        # Build query - filter by tenant AND principal for security
        stmt = select(DBCreative).filter_by(tenant_id=tenant["tenant_id"], principal_id=principal_id)

        # Apply filters
        if req.media_buy_id:
            # Filter by media buy assignments
            stmt = stmt.join(DBAssignment, DBCreative.creative_id == DBAssignment.creative_id).where(
                DBAssignment.media_buy_id == req.media_buy_id
            )

        if req.buyer_ref:
            # Filter by buyer_ref through media buy
            stmt = (
                stmt.join(DBAssignment, DBCreative.creative_id == DBAssignment.creative_id)
                .join(MediaBuy, DBAssignment.media_buy_id == MediaBuy.media_buy_id)
                .where(MediaBuy.buyer_ref == req.buyer_ref)
            )

        if req.status:
            stmt = stmt.where(DBCreative.status == req.status)

        if req.format:
            stmt = stmt.where(DBCreative.format == req.format)

        if req.tags:
            # Simple tag filtering - in production, might use JSON operators
            for tag in req.tags:
                stmt = stmt.where(DBCreative.name.contains(tag))  # Simplified

        if req.created_after:
            stmt = stmt.where(DBCreative.created_at >= req.created_after)

        if req.created_before:
            stmt = stmt.where(DBCreative.created_at <= req.created_before)

        if req.search:
            # Search in name and description
            search_term = f"%{req.search}%"
            stmt = stmt.where(DBCreative.name.ilike(search_term))

        # Get total count before pagination
        from sqlalchemy import func
        from sqlalchemy.orm import InstrumentedAttribute

        total_count_result = session.scalar(select(func.count()).select_from(stmt.subquery()))
        total_count = int(total_count_result) if total_count_result is not None else 0

        # Apply sorting
        sort_column: InstrumentedAttribute
        if req.sort_by == "name":
            sort_column = DBCreative.name  # type: ignore[assignment]
        elif req.sort_by == "status":
            sort_column = DBCreative.status  # type: ignore[assignment]
        else:  # Default to created_date
            sort_column = DBCreative.created_at  # type: ignore[assignment]

        if req.sort_order == "asc":
            stmt = stmt.order_by(sort_column.asc())
        else:
            stmt = stmt.order_by(sort_column.desc())

        # Apply pagination
        offset = (req.page - 1) * req.limit
        db_creatives = session.scalars(stmt.offset(offset).limit(req.limit)).all()

        # Convert to schema objects
        for db_creative in db_creatives:
            # Handle content_uri - required field even for snippet creatives
            # For snippet creatives, provide an HTML-looking URL to pass validation
            snippet = db_creative.data.get("snippet") if db_creative.data else None
            if snippet:
                content_uri = (
                    db_creative.data.get("url") or "<script>/* Snippet-based creative */</script>"
                    if db_creative.data
                    else "<script>/* Snippet-based creative */</script>"
                )
            else:
                content_uri = (
                    db_creative.data.get("url") or "https://placeholder.example.com/missing.jpg"
                    if db_creative.data
                    else "https://placeholder.example.com/missing.jpg"
                )

            # Build Creative directly with explicit types to satisfy mypy
            from src.core.schemas import FormatId

            format_obj = FormatId(agent_url=db_creative.agent_url or "", id=db_creative.format or "")

            # Ensure datetime fields are datetime (not SQLAlchemy DateTime)
            created_at_dt: datetime = (
                db_creative.created_at if isinstance(db_creative.created_at, datetime) else datetime.now(UTC)
            )
            updated_at_dt: datetime = (
                db_creative.updated_at if isinstance(db_creative.updated_at, datetime) else datetime.now(UTC)
            )

            # AdCP v1 spec compliant - only spec fields
            # Reconstruct assets dict from database data
            assets_dict = db_creative.data.get("assets", {}) if db_creative.data else {}
            if not assets_dict:
                # Legacy data: reconstruct assets dict from old fields
                assets_dict = {}
                if content_uri:
                    # Detect asset type and build appropriate asset structure
                    main_asset: dict[str, Any] = {"url": content_uri}
                    if db_creative.data:
                        if "width" in db_creative.data and db_creative.data["width"] is not None:
                            main_asset["width"] = db_creative.data["width"]
                        if "height" in db_creative.data and db_creative.data["height"] is not None:
                            main_asset["height"] = db_creative.data["height"]
                        if "duration" in db_creative.data and db_creative.data["duration"] is not None:
                            main_asset["duration_ms"] = int(db_creative.data["duration"] * 1000)
                    assets_dict["main"] = main_asset

                # Add click URL if present
                if db_creative.data and "click_url" in db_creative.data:
                    assets_dict["click_url"] = {
                        "url": db_creative.data["click_url"],
                        "url_type": "clickthrough",
                    }

            creative = Creative(
                creative_id=db_creative.creative_id,
                name=db_creative.name,
                format_id=format_obj,
                assets=assets_dict,
                inputs=db_creative.data.get("inputs") if db_creative.data else None,
                tags=db_creative.data.get("tags") if db_creative.data else None,
                approved=db_creative.data.get("approved") if db_creative.data else None,
                # Internal fields
                status=db_creative.status,
                principal_id=db_creative.principal_id,
                created_at=created_at_dt,
                updated_at=updated_at_dt,
            )
            creatives.append(creative)

    # Calculate pagination info
    has_more = (req.page * req.limit) < total_count

    # Audit logging
    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
    audit_logger.log_operation(
        operation="list_creatives",
        principal_name=principal_id,
        principal_id=principal_id,
        adapter_id="N/A",
        success=True,
        details={
            "result_count": len(creatives),
            "total_count": total_count,
            "page": req.page,
            "filters_applied": {
                "media_buy_id": req.media_buy_id,
                "status": req.status,
                "format": req.format,
                "search": req.search,
            },
        },
    )

    # Log activity
    # Activity logging imported at module level
    if context is not None:
        log_tool_activity(context, "list_creatives", start_time)

    message = f"Found {len(creatives)} creatives"
    if total_count > len(creatives):
        message += f" (page {req.page} of {total_count} total)"

    # Build filters_applied list
    filters_applied = []
    if req.media_buy_id:
        filters_applied.append(f"media_buy_id={req.media_buy_id}")
    if req.buyer_ref:
        filters_applied.append(f"buyer_ref={req.buyer_ref}")
    if req.status:
        filters_applied.append(f"status={req.status}")
    if req.format:
        filters_applied.append(f"format={req.format}")
    if req.tags:
        filters_applied.append(f"tags={','.join(req.tags)}")
    if req.created_after:
        filters_applied.append(f"created_after={req.created_after.isoformat()}")
    if req.created_before:
        filters_applied.append(f"created_before={req.created_before.isoformat()}")
    if req.search:
        filters_applied.append(f"search={req.search}")

    # Build sort_applied dict
    sort_applied = {"field": req.sort_by, "direction": req.sort_order} if req.sort_by else None

    # Calculate offset and total_pages
    offset = (req.page - 1) * req.limit
    total_pages = (total_count + req.limit - 1) // req.limit if req.limit > 0 else 0

    # Import required schema classes
    from src.core.schemas import Pagination, QuerySummary

    return ListCreativesResponse(
        query_summary=QuerySummary(
            total_matching=total_count,
            returned=len(creatives),
            filters_applied=filters_applied,
            sort_applied=sort_applied,
        ),
        pagination=Pagination(
            limit=req.limit, offset=offset, has_more=has_more, total_pages=total_pages, current_page=req.page
        ),
        creatives=creatives,
    )


async def list_creatives(
    media_buy_id: str = None,
    buyer_ref: str = None,
    status: str = None,
    format: str = None,
    tags: list[str] = None,
    created_after: str = None,
    created_before: str = None,
    search: str = None,
    filters: dict = None,
    sort: dict = None,
    pagination: dict = None,
    fields: list[str] = None,
    include_performance: bool = False,
    include_assignments: bool = False,
    include_sub_assets: bool = False,
    page: int = 1,
    limit: int = 50,
    sort_by: str = "created_date",
    sort_order: str = "desc",
    webhook_url: str | None = None,
    context: Context | ToolContext | None = None,
):
    """List and filter creative assets from the centralized library.

    MCP tool wrapper that delegates to the shared implementation.
    Supports both flat parameters (status, format, etc.) and nested objects (filters, sort, pagination)
    for maximum flexibility.

    Returns:
        ToolResult with ListCreativesResponse data
    """
    response = _list_creatives_impl(
        media_buy_id,
        buyer_ref,
        status,
        format,
        tags,
        created_after,
        created_before,
        search,
        filters,
        sort,
        pagination,
        fields,
        include_performance,
        include_assignments,
        include_sub_assets,
        page,
        limit,
        sort_by,
        sort_order,
        context,
    )
    return ToolResult(content=str(response), structured_content=response.model_dump())


def sync_creatives_raw(
    creatives: list[dict],
    patch: bool = False,
    assignments: dict = None,
    delete_missing: bool = False,
    dry_run: bool = False,
    validation_mode: str = "strict",
    push_notification_config: dict = None,
    context: Context | ToolContext | None = None,
):
    """Sync creative assets to the centralized creative library (raw function for A2A server use).

    Delegates to the shared implementation.

    Args:
        creatives: List of creative asset objects
        patch: When true, only update provided fields (partial update). When false, full upsert.
        assignments: Bulk assignment map of creative_id to package_ids (spec-compliant)
        delete_missing: Delete creatives not in sync payload (use with caution)
        dry_run: Preview changes without applying them
        validation_mode: Validation strictness (strict or lenient)
        push_notification_config: Push notification config for status updates
        context: FastMCP context (automatically provided)

    Returns:
        SyncCreativesResponse with synced creatives and assignments
    """
    return _sync_creatives_impl(
        creatives=creatives,
        patch=patch,
        assignments=assignments,
        delete_missing=delete_missing,
        dry_run=dry_run,
        validation_mode=validation_mode,
        push_notification_config=push_notification_config,
        context=context,
    )


def list_creatives_raw(
    media_buy_id: str = None,
    buyer_ref: str = None,
    status: str = None,
    format: str = None,
    tags: list[str] = None,
    created_after: str = None,
    created_before: str = None,
    search: str = None,
    page: int = 1,
    limit: int = 50,
    sort_by: str = "created_date",
    sort_order: str = "desc",
    context: Context | ToolContext | None = None,
):
    """List creative assets with filtering and pagination (raw function for A2A server use).

    Delegates to the shared implementation.

    Args:
        media_buy_id: Filter by media buy ID (optional)
        buyer_ref: Filter by buyer reference (optional)
        status: Filter by status (optional)
        format: Filter by creative format (optional)
        tags: Filter by creative group tags (optional)
        created_after: Filter creatives created after this date (ISO format) (optional)
        created_before: Filter creatives created before this date (ISO format) (optional)
        search: Search in creative name or description (optional)
        page: Page number for pagination (default: 1)
        limit: Number of results per page (default: 50, max: 1000)
        sort_by: Sort field (default: created_date)
        sort_order: Sort order (default: desc)
        context: FastMCP context (automatically provided)

    Returns:
        ListCreativesResponse with filtered creative assets and pagination info
    """
    return _list_creatives_impl(
        media_buy_id=media_buy_id,
        buyer_ref=buyer_ref,
        status=status,
        format=format,
        tags=tags,
        created_after=created_after,
        created_before=created_before,
        search=search,
        page=page,
        limit=limit,
        sort_by=sort_by,
        sort_order=sort_order,
        context=context,
    )
