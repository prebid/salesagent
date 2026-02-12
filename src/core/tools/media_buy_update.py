"""Update Media Buy tool implementation.

Handles media buy updates including:
- Campaign-level budget and date changes
- Package-level budget adjustments
- Creative assignments per package
- Activation/pause controls
- Currency limit validation
"""

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from adcp import PushNotificationConfig
from adcp.types import Error
from adcp.types.generated_poc.core.context import ContextObject
from adcp.types.generated_poc.core.targeting import TargetingOverlay
from adcp.types.generated_poc.media_buy.package_update import PackageUpdate as UpdatePackage
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import ValidationError
from sqlalchemy import select

from src.core.tool_context import ToolContext

logger = logging.getLogger(__name__)

from src.core.audit_logger import get_audit_logger
from src.core.auth import (
    get_principal_object,
)
from src.core.config_loader import get_current_tenant
from src.core.context_manager import get_context_manager
from src.core.database.database_session import get_db_session
from src.core.helpers import get_principal_id_from_context
from src.core.helpers.adapter_helpers import get_adapter
from src.core.schema_helpers import to_context_object
from src.core.schemas import (
    AffectedPackage,
    UpdateMediaBuyError,
    UpdateMediaBuyRequest,
    UpdateMediaBuySuccess,
)
from src.core.testing_hooks import get_testing_context
from src.core.validation_helpers import format_validation_error


def _verify_principal(media_buy_id: str, context: Context | ToolContext):
    """Verify that the principal from context owns the media buy.

    Checks database for media buy ownership, not in-memory dictionary.

    Args:
        media_buy_id: Media buy ID to verify
        context: FastMCP Context or ToolContext with principal info

    Raises:
        ValueError: Media buy not found
        PermissionError: Principal doesn't own media buy
    """

    from src.core.database.models import MediaBuy as MediaBuyModel

    # Get principal_id from context
    if isinstance(context, ToolContext):
        principal_id: str | None = context.principal_id
    else:
        principal_id = get_principal_id_from_context(context)

    # CRITICAL: principal_id is required for media buy updates
    if not principal_id:
        raise ToolError(
            "Authentication required: Missing or invalid x-adcp-auth header. Media buy updates require authentication."
        )

    tenant = get_current_tenant()

    # Query database for media buy (try media_buy_id first, then buyer_ref)
    with get_db_session() as session:
        stmt = select(MediaBuyModel).where(
            MediaBuyModel.media_buy_id == media_buy_id, MediaBuyModel.tenant_id == tenant["tenant_id"]
        )
        media_buy = session.scalars(stmt).first()

        # If not found by media_buy_id, try buyer_ref (for backwards compatibility)
        if not media_buy:
            stmt = select(MediaBuyModel).where(
                MediaBuyModel.buyer_ref == media_buy_id, MediaBuyModel.tenant_id == tenant["tenant_id"]
            )
            media_buy = session.scalars(stmt).first()

        if not media_buy:
            raise ValueError(f"Media buy '{media_buy_id}' not found.")

        if media_buy.principal_id != principal_id:
            # CRITICAL: Verify principal_id is set (security check, not assertion)
            # Using explicit check instead of assert because asserts are removed with python -O
            if not principal_id:
                raise ToolError("Authentication required: principal_id not found in context")

            # Log security violation
            security_logger = get_audit_logger("AdCP", tenant["tenant_id"])
            security_logger.log_security_violation(
                operation="access_media_buy",
                principal_id=principal_id,
                resource_id=media_buy_id,
                reason=f"Principal does not own media buy (owner: {media_buy.principal_id})",
            )
            raise PermissionError(f"Principal '{principal_id}' does not own media buy '{media_buy_id}'.")


def _update_media_buy_impl(
    media_buy_id: str | None = None,
    buyer_ref: str | None = None,
    paused: bool | None = None,
    flight_start_date: str | None = None,
    flight_end_date: str | None = None,
    budget: float | None = None,
    currency_param: str | None = None,  # Renamed to avoid redefinition
    targeting_overlay: dict | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    pacing: str | None = None,
    daily_budget: float | None = None,
    packages: list | None = None,
    creatives: list | None = None,
    push_notification_config: dict | None = None,
    context: dict | None = None,
    ctx: Context | ToolContext | None = None,
) -> UpdateMediaBuySuccess | UpdateMediaBuyError:
    """Shared implementation for update_media_buy (used by both MCP and A2A).

    Update a media buy with campaign-level and/or package-level changes.

    Args:
        media_buy_id: Media buy ID to update (oneOf with buyer_ref - exactly one required)
        buyer_ref: Buyer reference to identify media buy (oneOf with media_buy_id - exactly one required)
        paused: True to pause campaign, False to resume (adcp 2.12.0+)
        flight_start_date: Change start date (if not started)
        flight_end_date: Extend or shorten campaign
        budget: Update total budget
        currency: Update currency (ISO 4217)
        targeting_overlay: Update global targeting
        start_time: Update start datetime
        end_time: Update end datetime
        pacing: Pacing strategy (even, asap, daily_budget)
        daily_budget: Daily spend cap across all packages
        packages: Package-specific updates
        creatives: Add new creatives
        push_notification_config: Push notification config for status updates (AdCP spec, optional)
        context: Application level context per adcp spec
        ctx: FastMCP context (automatically provided)

    Returns:
        UpdateMediaBuyResponse with updated media buy details
    """
    # Create request object from individual parameters (MCP-compliant)
    # Handle deprecated field names (backward compatibility)
    if flight_start_date and not start_time:
        start_time = flight_start_date
    if flight_end_date and not end_time:
        end_time = flight_end_date

    # Convert flat budget/currency/pacing to Budget object if budget provided
    budget_obj = None
    if budget is not None:
        from typing import Literal

        from src.core.schemas import Budget

        pacing_val: Literal["even", "asap", "daily_budget"] = "even"
        if pacing == "even":
            pacing_val = "even"
        elif pacing == "asap":
            pacing_val = "asap"
        elif pacing == "daily_budget":
            pacing_val = "daily_budget"
        budget_obj = Budget(
            total=budget,
            currency=currency_param or "USD",  # Use renamed parameter
            pacing=pacing_val,  # Default pacing
            daily_cap=daily_budget,  # Map daily_budget to daily_cap
            auto_pause_on_budget_exhaustion=None,
        )

    # Build request with only valid AdCP fields
    # Note: flight_start_date, flight_end_date are mapped to start_time/end_time above
    # creatives and targeting_overlay are deprecated - use packages for updates
    # Filter out None values to avoid passing them to the request (strict validation in dev mode)
    request_params: dict[str, Any] = {}
    if media_buy_id is not None:
        request_params["media_buy_id"] = media_buy_id
    if buyer_ref is not None:
        request_params["buyer_ref"] = buyer_ref
    if paused is not None:
        request_params["paused"] = paused
    if start_time is not None:
        request_params["start_time"] = start_time
    if end_time is not None:
        request_params["end_time"] = end_time
    if budget_obj is not None:
        request_params["budget"] = budget_obj
    if packages is not None:
        request_params["packages"] = packages
    if push_notification_config is not None:
        request_params["push_notification_config"] = push_notification_config
    if context is not None:
        request_params["context"] = context

    try:
        req = UpdateMediaBuyRequest(**request_params)
    except ValidationError as e:
        raise ToolError(format_validation_error(e, context="update_media_buy request")) from e

    # Initialize tracking for affected packages (internal tracking, not part of schema)
    affected_packages_list: list[AffectedPackage] = []

    if ctx is None:
        raise ValueError("Context is required for update_media_buy")

    # CRITICAL: Establish tenant context FIRST by extracting principal from auth token
    # This must happen before any database queries that need tenant_id
    principal_id = get_principal_id_from_context(ctx)
    if principal_id is None:
        raise ValueError("principal_id is required but was None - authentication required")

    # Now tenant context is set, we can safely call get_current_tenant()
    tenant = get_current_tenant()

    # Resolve media_buy_id from buyer_ref if needed (AdCP oneOf constraint)
    media_buy_id_to_use = req.media_buy_id
    if not media_buy_id_to_use and req.buyer_ref:
        # Look up media_buy_id by buyer_ref (tenant context already set above)
        from src.core.database.database_session import get_db_session
        from src.core.database.models import MediaBuy as MediaBuyModel

        with get_db_session() as session:
            stmt = select(MediaBuyModel).where(
                MediaBuyModel.buyer_ref == req.buyer_ref, MediaBuyModel.tenant_id == tenant["tenant_id"]
            )
            media_buy = session.scalars(stmt).first()
            if not media_buy:
                raise ValueError(f"Media buy with buyer_ref '{req.buyer_ref}' not found")
            media_buy_id_to_use = media_buy.media_buy_id
            logger.info(
                f"[update_media_buy] Resolved buyer_ref '{req.buyer_ref}' to media_buy_id '{media_buy_id_to_use}'"
            )

    if not media_buy_id_to_use:
        raise ValueError("Either media_buy_id or buyer_ref is required")

    # Update req.media_buy_id for downstream processing
    req.media_buy_id = media_buy_id_to_use

    # Verify principal owns this media buy
    _verify_principal(media_buy_id_to_use, ctx)

    # Extract testing context early (needed for dry_run check)
    testing_ctx = get_testing_context(ctx)

    # Create or get persistent context and workflow step
    # Skip for dry_run mode (no side effects, no database writes)
    ctx_manager = get_context_manager()
    ctx_id = ctx.headers.get("x-context-id") if hasattr(ctx, "headers") else None
    persistent_ctx = None
    step = None

    if not testing_ctx.dry_run:
        persistent_ctx = ctx_manager.get_or_create_context(
            tenant_id=tenant["tenant_id"],
            principal_id=principal_id,  # Now guaranteed to be str
            context_id=ctx_id,
            is_async=True,
        )

        # Verify persistent_ctx is not None
        if persistent_ctx is None:
            raise ValueError("Failed to create or get persistent context")

        # Prepare request data with protocol detection
        request_data_for_workflow = req.model_dump(mode="json")  # Convert dates to strings

        # Store protocol type for webhook payload creation
        # ToolContext = A2A, Context (FastMCP) = MCP
        request_data_for_workflow["protocol"] = "a2a" if isinstance(ctx, ToolContext) else "mcp"

        # Create workflow step for this tool call
        step = ctx_manager.create_workflow_step(
            context_id=persistent_ctx.context_id,  # Now safe to access
            step_type="tool_call",
            owner="principal",
            status="in_progress",
            tool_name="update_media_buy",
            request_data=request_data_for_workflow,
        )

    principal = get_principal_object(principal_id)  # Now guaranteed to be str
    if not principal:
        error_msg = f"Principal {principal_id} not found"
        response_data = UpdateMediaBuyError(
            errors=[Error(code="principal_not_found", message=error_msg)],
            context=to_context_object(req.context),
        )
        if step:
            ctx_manager.update_workflow_step(
                step.step_id,
                status="failed",
                response_data=response_data.model_dump(mode="json"),
                error_message=error_msg,
            )
        return response_data

    adapter = get_adapter(principal, dry_run=testing_ctx.dry_run, testing_context=testing_ctx)
    today = req.today or date.today()

    # Dry-run mode: Return simulated response without any database writes
    # Validation has passed (principal verified, media buy exists), so we return what WOULD be updated
    if testing_ctx.dry_run:
        logger.info(f"[DRY_RUN] Returning simulated update response for media_buy_id={req.media_buy_id}")

        # Build simulated affected packages from request
        simulated_affected: list[AffectedPackage] = []
        if req.packages:
            for pkg_update in req.packages:
                simulated_affected.append(
                    AffectedPackage(
                        buyer_ref=req.buyer_ref or "",
                        package_id=pkg_update.package_id or "",
                        paused=pkg_update.paused if pkg_update.paused is not None else False,
                        buyer_package_ref=pkg_update.package_id,
                        changes_applied={"dry_run": True, "would_update": pkg_update.model_dump(exclude_none=True)},
                    )
                )

        # Build simulated response
        dry_run_response = UpdateMediaBuySuccess(
            media_buy_id=req.media_buy_id or "",
            buyer_ref=req.buyer_ref or "",
            affected_packages=simulated_affected,
            context=to_context_object(req.context),
        )

        return dry_run_response

    # Type narrowing: after dry_run early return, step and persistent_ctx are guaranteed to exist
    assert step is not None, "step should be created when not in dry_run mode"
    assert persistent_ctx is not None, "persistent_ctx should be created when not in dry_run mode"

    # Check if manual approval is required
    manual_approval_required = (
        adapter.manual_approval_required if hasattr(adapter, "manual_approval_required") else False
    )
    manual_approval_operations = (
        adapter.manual_approval_operations if hasattr(adapter, "manual_approval_operations") else []
    )

    if manual_approval_required and "update_media_buy" in manual_approval_operations:
        # Build response first, then persist on workflow step, then return
        # UpdateMediaBuySuccess extends adcp v1.2.1 with internal fields (workflow_step_id, affected_packages)
        approval_response = UpdateMediaBuySuccess(
            media_buy_id=req.media_buy_id or "",
            buyer_ref=req.buyer_ref or "",
            affected_packages=[],  # Internal field for tracking changes
            context=to_context_object(req.context),
        )
        ctx_manager.update_workflow_step(
            step.step_id,
            status="requires_approval",
            response_data=approval_response.model_dump(mode="json"),
            add_comment={"user": "system", "comment": "Publisher requires manual approval for all media buy updates"},
        )
        return approval_response

    # Validate currency limits if flight dates or budget changes
    # This prevents workarounds where buyers extend flight to bypass daily max
    if req.start_time or req.end_time or req.budget or (req.packages and any(pkg.budget for pkg in req.packages)):
        from decimal import Decimal

        from src.core.database.database_session import get_db_session
        from src.core.database.models import CurrencyLimit
        from src.core.database.models import MediaBuy as MediaBuyModel

        # Get media buy from database to check currency and current dates
        with get_db_session() as session:
            stmt = select(MediaBuyModel).where(MediaBuyModel.media_buy_id == req.media_buy_id)
            media_buy = session.scalars(stmt).first()

            if media_buy:
                # Determine currency (use updated or existing)
                # Extract currency from Budget object if present (and if it's an object, not plain number)
                request_currency: str
                if req.budget:
                    # Check if it's a Budget object with currency attribute, otherwise use existing
                    if hasattr(req.budget, "currency"):
                        request_currency = str(req.budget.currency)
                    else:
                        # Float budget - use existing media buy currency
                        request_currency = str(media_buy.currency) if media_buy.currency else "USD"
                else:
                    request_currency = str(media_buy.currency) if media_buy.currency else "USD"

                # Get currency limit
                currency_stmt = select(CurrencyLimit).where(
                    CurrencyLimit.tenant_id == tenant["tenant_id"], CurrencyLimit.currency_code == request_currency
                )
                currency_limit = session.scalars(currency_stmt).first()

                if not currency_limit:
                    error_msg = f"Currency {request_currency} is not supported by this publisher."
                    response_data = UpdateMediaBuyError(
                        errors=[Error(code="currency_not_supported", message=error_msg)],
                        context=to_context_object(req.context),
                    )
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        response_data=response_data.model_dump(mode="json"),
                        error_message=error_msg,
                    )
                    return response_data

                # Calculate new flight duration
                start = req.start_time if req.start_time else media_buy.start_time
                end = req.end_time if req.end_time else media_buy.end_time

                # Parse datetime strings if needed, handle 'asap' (AdCP v1.7.0)
                from datetime import datetime as dt

                # Convert to datetime objects
                start_dt: datetime
                end_dt: datetime

                if isinstance(start, str):
                    if start == "asap":
                        start_dt = dt.now(UTC)
                    else:
                        start_dt = dt.fromisoformat(start.replace("Z", "+00:00"))
                elif isinstance(start, datetime):
                    start_dt = start
                else:
                    # Handle None or other types
                    start_dt = dt.now(UTC)

                if isinstance(end, str):
                    end_dt = dt.fromisoformat(end.replace("Z", "+00:00"))
                elif isinstance(end, datetime):
                    end_dt = end
                else:
                    # Handle None - default to start + 1 day
                    end_dt = start_dt + timedelta(days=1)

                flight_days = (end_dt - start_dt).days
                if flight_days <= 0:
                    flight_days = 1

                # Validate max daily spend for packages
                if currency_limit.max_daily_package_spend and req.packages:
                    for pkg_update in req.packages:
                        if pkg_update.budget:
                            # Extract budget amount - handle both float and Budget object
                            pkg_budget_amount: float
                            if isinstance(pkg_update.budget, int | float):
                                pkg_budget_amount = float(pkg_update.budget)
                            else:
                                # Budget object with .total attribute
                                pkg_budget_amount = float(pkg_update.budget.total)

                            package_budget = Decimal(str(pkg_budget_amount))
                            package_daily = package_budget / Decimal(str(flight_days))

                            if package_daily > currency_limit.max_daily_package_spend:
                                error_msg = (
                                    f"Updated package daily budget ({package_daily} {request_currency}) "
                                    f"exceeds maximum ({currency_limit.max_daily_package_spend} {request_currency}). "
                                    f"Flight date changes that reduce daily budget are not allowed to bypass limits."
                                )
                                response_data = UpdateMediaBuyError(
                                    errors=[Error(code="budget_limit_exceeded", message=error_msg)],
                                    context=to_context_object(req.context),
                                )
                                ctx_manager.update_workflow_step(
                                    step.step_id,
                                    status="failed",
                                    response_data=response_data.model_dump(mode="json"),
                                    error_message=error_msg,
                                )
                                return response_data

    # Handle campaign-level updates
    if req.paused is not None:
        # adcp 2.12.0+: paused=True means pause, paused=False means resume
        action = "pause_media_buy" if req.paused else "resume_media_buy"
        result = adapter.update_media_buy(
            media_buy_id=req.media_buy_id,
            buyer_ref=req.buyer_ref or "",
            action=action,
            package_id=None,
            budget=None,
            today=datetime.combine(today, datetime.min.time(), tzinfo=UTC),
        )
        # Manual approval case - convert adapter result to appropriate Success/Error
        # adcp v1.2.1 oneOf pattern: Check if result is Error variant (has errors field)
        if hasattr(result, "errors") and result.errors:
            return UpdateMediaBuyError(errors=result.errors)
        else:
            # UpdateMediaBuySuccess extends adcp v1.2.1 with internal fields
            # Use getattr to safely access discriminated union fields
            media_buy_id = getattr(result, "media_buy_id", req.media_buy_id or "")
            buyer_ref_val = getattr(result, "buyer_ref", req.buyer_ref or "")
            affected_pkgs = getattr(result, "affected_packages", [])

            success_response = UpdateMediaBuySuccess(
                media_buy_id=media_buy_id,
                buyer_ref=buyer_ref_val,
                affected_packages=affected_pkgs,
            )
            # Log successful update_media_buy (pause/resume)
            audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
            audit_logger.log_operation(
                operation="update_media_buy",
                principal_name=principal_id or "anonymous",
                principal_id=principal_id or "anonymous",
                adapter_id="mcp_server",
                success=True,
                details={
                    "media_buy_id": req.media_buy_id,
                    "buyer_ref": req.buyer_ref,
                    "action": action,
                    "affected_packages_count": len(affected_pkgs),
                },
            )
            return success_response

    # Handle package-level updates
    if req.packages:
        for pkg_update in req.packages:
            # Handle paused state
            if pkg_update.paused is not None:
                # adcp 2.12.0+: paused=True means pause, paused=False means resume
                action = "pause_package" if pkg_update.paused else "resume_package"
                result = adapter.update_media_buy(
                    media_buy_id=req.media_buy_id,
                    buyer_ref=req.buyer_ref or "",
                    action=action,
                    package_id=pkg_update.package_id,
                    budget=None,
                    today=datetime.combine(today, datetime.min.time(), tzinfo=UTC),
                )
                # adcp v1.2.1 oneOf pattern: Check if result is Error variant
                if hasattr(result, "errors") and result.errors:
                    error_message = (
                        result.errors[0].message if (result.errors and len(result.errors) > 0) else "Update failed"
                    )
                    response_data = UpdateMediaBuyError(errors=result.errors)
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        response_data=response_data.model_dump(mode="json"),
                        error_message=error_message,
                    )
                    return response_data

            # Handle budget updates
            if pkg_update.budget is not None:
                # Validate package_id is provided (required for budget updates)
                if not pkg_update.package_id:
                    error_msg = "package_id is required when updating package budget"
                    response_data = UpdateMediaBuyError(
                        errors=[Error(code="missing_package_id", message=error_msg)],
                        context=to_context_object(req.context),
                    )
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        response_data=response_data.model_dump(mode="json"),
                        error_message=error_msg,
                    )
                    return response_data

                # Extract budget amount - handle both float and Budget object
                budget_amount: float
                currency: str
                if isinstance(pkg_update.budget, int | float):
                    budget_amount = float(pkg_update.budget)
                    currency = "USD"  # Default currency for float budgets
                else:
                    # Budget object with .total and .currency attributes
                    budget_amount = float(pkg_update.budget.total)
                    currency = pkg_update.budget.currency if hasattr(pkg_update.budget, "currency") else "USD"

                result = adapter.update_media_buy(
                    media_buy_id=req.media_buy_id,
                    buyer_ref=req.buyer_ref or "",
                    action="update_package_budget",
                    package_id=pkg_update.package_id,
                    budget=int(budget_amount),
                    today=datetime.combine(today, datetime.min.time(), tzinfo=UTC),
                )
                # adcp v1.2.1 oneOf pattern: Check if result is Error variant
                if hasattr(result, "errors") and result.errors:
                    error_message = (
                        result.errors[0].message if (result.errors and len(result.errors) > 0) else "Update failed"
                    )
                    response_data = UpdateMediaBuyError(errors=result.errors)
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        response_data=response_data.model_dump(mode="json"),
                        error_message=error_message,
                    )
                    return response_data

                # Track budget update in affected_packages
                # At this point, pkg_update.package_id is guaranteed to be str (checked above)
                affected_packages_list.append(
                    AffectedPackage(
                        buyer_ref=req.buyer_ref or "",  # Required by AdCP
                        package_id=pkg_update.package_id,  # Required by AdCP (guaranteed str)
                        paused=False,  # Package not paused (active)
                        buyer_package_ref=pkg_update.package_id,  # Internal field (for backward compat)
                        changes_applied={"budget": {"updated": budget_amount, "currency": currency}},  # Internal field
                    )
                )

            # Handle creative_ids updates (AdCP v2.2.0+)
            if pkg_update.creative_ids is not None:
                # Validate package_id is provided
                if not pkg_update.package_id:
                    error_msg = "package_id is required when updating creative_ids"
                    response_data = UpdateMediaBuyError(
                        errors=[Error(code="missing_package_id", message=error_msg)],
                        context=to_context_object(req.context),
                    )
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        response_data=response_data.model_dump(mode="json"),
                        error_message=error_msg,
                    )
                    return response_data

                from src.core.database.database_session import get_db_session
                from src.core.database.models import Creative as DBCreative
                from src.core.database.models import CreativeAssignment as DBAssignment
                from src.core.database.models import MediaBuy as MediaBuyModel

                with get_db_session() as session:
                    # Resolve media_buy_id (might be buyer_ref)
                    mb_stmt = select(MediaBuyModel).where(
                        MediaBuyModel.media_buy_id == req.media_buy_id, MediaBuyModel.tenant_id == tenant["tenant_id"]
                    )
                    media_buy_obj = session.scalars(mb_stmt).first()

                    # Try buyer_ref if not found
                    if not media_buy_obj:
                        mb_stmt = select(MediaBuyModel).where(
                            MediaBuyModel.buyer_ref == req.media_buy_id, MediaBuyModel.tenant_id == tenant["tenant_id"]
                        )
                        media_buy_obj = session.scalars(mb_stmt).first()

                    if not media_buy_obj:
                        error_msg = f"Media buy '{req.media_buy_id}' not found"
                        response_data = UpdateMediaBuyError(
                            errors=[Error(code="media_buy_not_found", message=error_msg)],
                            context=to_context_object(req.context),
                        )
                        ctx_manager.update_workflow_step(
                            step.step_id,
                            status="failed",
                            response_data=response_data.model_dump(mode="json"),
                            error_message=error_msg,
                        )
                        return response_data

                    # Use the actual internal media_buy_id
                    actual_media_buy_id = media_buy_obj.media_buy_id

                    # Validate all creative IDs exist
                    creative_stmt = select(DBCreative).where(
                        DBCreative.tenant_id == tenant["tenant_id"],
                        DBCreative.creative_id.in_(pkg_update.creative_ids),
                    )
                    creatives_list = session.scalars(creative_stmt).all()
                    found_creative_ids = {c.creative_id for c in creatives_list}
                    missing_ids = set(pkg_update.creative_ids) - found_creative_ids

                    if missing_ids:
                        error_msg = f"Creative IDs not found: {', '.join(missing_ids)}"
                        response_data = UpdateMediaBuyError(
                            errors=[Error(code="creatives_not_found", message=error_msg)],
                            context=to_context_object(req.context),
                        )
                        ctx_manager.update_workflow_step(
                            step.step_id,
                            status="failed",
                            response_data=response_data.model_dump(mode="json"),
                            error_message=error_msg,
                        )
                        return response_data

                    # Validate creatives are in usable state before updating
                    # Note: We validate existence (already done above) and status, not structure
                    # Structure validation happens during sync_creatives - here we just assign
                    validation_errors = []
                    for creative in creatives_list:
                        # Check if creative is in a valid state for assignment
                        # Creatives in "error" or "rejected" state should not be assignable
                        if creative.status in ["error", "rejected"]:
                            validation_errors.append(
                                f"Creative {creative.creative_id} cannot be assigned (status={creative.status})"
                            )

                    # Validate creative formats against package product formats
                    # Get package and product to check supported formats
                    from src.core.database.models import MediaPackage as MediaPackageModel
                    from src.core.database.models import Product

                    package_stmt = select(MediaPackageModel).where(
                        MediaPackageModel.package_id == pkg_update.package_id,
                        MediaPackageModel.media_buy_id == actual_media_buy_id,
                    )
                    db_package = session.scalars(package_stmt).first()

                    # Get product_id from package_config
                    product_id = (
                        db_package.package_config.get("product_id")
                        if db_package and db_package.package_config
                        else None
                    )

                    if product_id:
                        # Get product to check supported formats
                        product_stmt = select(Product).where(
                            Product.tenant_id == tenant["tenant_id"], Product.product_id == product_id
                        )
                        product = session.scalars(product_stmt).first()

                        if product and product.format_ids:
                            # Build set of supported formats (agent_url, format_id) tuples
                            supported_formats = set()
                            for fmt in product.format_ids:
                                if isinstance(fmt, dict):
                                    agent_url = fmt.get("agent_url")
                                    format_id = fmt.get("id") or fmt.get("format_id")
                                    if agent_url and format_id:
                                        supported_formats.add((agent_url, format_id))

                            # Check each creative's format
                            for creative in creatives_list:
                                creative_agent_url = creative.agent_url
                                creative_format_id = creative.format

                                # Allow /mcp URL variant
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
                                    creative_format_display = (
                                        f"{creative_agent_url}/{creative_format_id}"
                                        if creative_agent_url
                                        else creative_format_id
                                    )
                                    supported_formats_display = ", ".join(
                                        [f"{url}/{fmt_id}" if url else fmt_id for url, fmt_id in supported_formats]
                                    )
                                    validation_errors.append(
                                        f"Creative {creative.creative_id} format '{creative_format_display}' "
                                        f"is not supported by product '{product.name}'. "
                                        f"Supported formats: {supported_formats_display}"
                                    )

                    if validation_errors:
                        error_msg = (
                            "Cannot update media buy with invalid creatives. "
                            "The following creatives cannot be assigned:\n"
                            + "\n".join(f"  • {err}" for err in validation_errors)
                        )
                        logger.error(f"[UPDATE] {error_msg}")
                        raise ToolError("INVALID_CREATIVES", error_msg, {"creative_errors": validation_errors})

                    # Get existing assignments for this package
                    assignment_stmt = select(DBAssignment).where(
                        DBAssignment.tenant_id == tenant["tenant_id"],
                        DBAssignment.media_buy_id == actual_media_buy_id,
                        DBAssignment.package_id == pkg_update.package_id,
                    )
                    existing_assignments = session.scalars(assignment_stmt).all()
                    existing_creative_ids = {a.creative_id for a in existing_assignments}

                    # Determine added and removed creative IDs
                    requested_ids = set(pkg_update.creative_ids)
                    added_ids = requested_ids - existing_creative_ids
                    removed_ids = existing_creative_ids - requested_ids

                    # Remove old assignments
                    for assignment in existing_assignments:
                        if assignment.creative_id in removed_ids:
                            session.delete(assignment)

                    # Add new assignments
                    import uuid

                    for creative_id in added_ids:
                        assignment_id = f"assign_{uuid.uuid4().hex[:12]}"
                        assignment = DBAssignment(
                            assignment_id=assignment_id,
                            tenant_id=tenant["tenant_id"],
                            media_buy_id=actual_media_buy_id,
                            package_id=pkg_update.package_id,
                            creative_id=creative_id,
                        )
                        session.add(assignment)

                    # If media buy was approved (approved_at set) but is in draft status
                    # (meaning it was approved without creatives), transition to pending_creatives
                    # Check whenever creative_ids are being set (not just when new ones added)
                    if (
                        pkg_update.creative_ids
                        and media_buy_obj.status == "draft"
                        and media_buy_obj.approved_at is not None
                    ):
                        media_buy_obj.status = "pending_creatives"
                        logger.info(
                            f"[UPDATE] Media buy {actual_media_buy_id} transitioned from draft to pending_creatives "
                            f"(creative_ids: {pkg_update.creative_ids})"
                        )

                    session.commit()

                    # Store results for affected_packages response
                    affected_packages_list.append(
                        AffectedPackage(
                            buyer_ref=req.buyer_ref or "",  # Required by AdCP
                            package_id=pkg_update.package_id,  # Required by AdCP
                            paused=False,  # Package not paused (active)
                            buyer_package_ref=pkg_update.package_id,  # Internal field (for backward compat)
                            changes_applied={  # Internal field
                                "creative_ids": {
                                    "added": list(added_ids),
                                    "removed": list(removed_ids),
                                    "current": pkg_update.creative_ids,
                                }
                            },
                        )
                    )

            # Handle creatives (inline upload) - AdCP 2.5
            if hasattr(pkg_update, "creatives") and pkg_update.creatives:
                # Validate package_id is provided
                if not pkg_update.package_id:
                    error_msg = "package_id is required when uploading creatives"
                    response_data = UpdateMediaBuyError(
                        errors=[Error(code="missing_package_id", message=error_msg)],
                        context=to_context_object(req.context),
                    )
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        response_data=response_data.model_dump(mode="json"),
                        error_message=error_msg,
                    )
                    return response_data

                from src.core.tools.creatives import _sync_creatives_impl

                # Sync creatives (upload/update)
                creative_dicts: list[dict[str, Any]] = []
                for c in pkg_update.creatives:
                    if hasattr(c, "model_dump"):
                        creative_dicts.append(c.model_dump(mode="json"))
                    else:
                        creative_dicts.append(cast(dict[str, Any], c))
                sync_response = _sync_creatives_impl(
                    creatives=creative_dicts,
                    assignments={
                        (c.get("creative_id") if isinstance(c, dict) else c.creative_id): [pkg_update.package_id]
                        for c in pkg_update.creatives
                        if (c.get("creative_id") if isinstance(c, dict) else getattr(c, "creative_id", None))
                    },
                    ctx=ctx,
                )

                # Check for sync errors
                failed_creatives = [r for r in sync_response.creatives if r.action == "failed"]
                if failed_creatives:
                    error_msgs = [f"{r.creative_id}: {', '.join(r.errors or [])}" for r in failed_creatives]
                    error_msg = f"Failed to sync creatives: {'; '.join(error_msgs)}"
                    response_data = UpdateMediaBuyError(
                        errors=[Error(code="creative_sync_failed", message=error_msg)],
                        context=to_context_object(req.context),
                    )
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        response_data=response_data.model_dump(mode="json"),
                        error_message=error_msg,
                    )
                    return response_data

                # Track in affected_packages
                synced_ids = [r.creative_id for r in sync_response.creatives if r.action in ["created", "updated"]]
                affected_packages_list.append(
                    AffectedPackage(
                        buyer_ref=req.buyer_ref or "",
                        package_id=pkg_update.package_id,
                        paused=False,
                        buyer_package_ref=pkg_update.package_id,
                        changes_applied={"creatives_uploaded": synced_ids},
                    )
                )

            # Handle creative_assignments (weight/placement updates) - adcp#208
            if pkg_update.creative_assignments:
                # Validate package_id is provided
                if not pkg_update.package_id:
                    error_msg = "package_id is required when updating creative_assignments"
                    response_data = UpdateMediaBuyError(
                        errors=[Error(code="missing_package_id", message=error_msg)],
                        context=to_context_object(req.context),
                    )
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        response_data=response_data.model_dump(mode="json"),
                        error_message=error_msg,
                    )
                    return response_data

                from src.core.database.database_session import get_db_session
                from src.core.database.models import CreativeAssignment as DBAssignment
                from src.core.database.models import MediaBuy as MediaBuyModel
                from src.core.database.models import MediaPackage as MediaPackageModel
                from src.core.database.models import Product as ProductModel

                with get_db_session() as session:
                    # Resolve media_buy_id
                    mb_stmt = select(MediaBuyModel).where(
                        MediaBuyModel.media_buy_id == req.media_buy_id, MediaBuyModel.tenant_id == tenant["tenant_id"]
                    )
                    media_buy_obj = session.scalars(mb_stmt).first()
                    if not media_buy_obj:
                        mb_stmt = select(MediaBuyModel).where(
                            MediaBuyModel.buyer_ref == req.media_buy_id, MediaBuyModel.tenant_id == tenant["tenant_id"]
                        )
                        media_buy_obj = session.scalars(mb_stmt).first()

                    if not media_buy_obj:
                        error_msg = f"Media buy '{req.media_buy_id}' not found"
                        response_data = UpdateMediaBuyError(
                            errors=[Error(code="media_buy_not_found", message=error_msg)],
                            context=to_context_object(req.context),
                        )
                        return response_data

                    actual_media_buy_id = media_buy_obj.media_buy_id

                    # Validate placement_ids against product's available placements (adcp#208)
                    # Build set of placement_ids from all creative_assignments
                    all_requested_placement_ids: set[str] = set()
                    for ca in pkg_update.creative_assignments:
                        if ca.placement_ids:
                            all_requested_placement_ids.update(ca.placement_ids)

                    if all_requested_placement_ids:
                        # Get package to find product_id
                        pkg_stmt = select(MediaPackageModel).where(
                            MediaPackageModel.media_buy_id == actual_media_buy_id,
                            MediaPackageModel.package_id == pkg_update.package_id,
                        )
                        pkg_record = session.scalars(pkg_stmt).first()

                        if not pkg_record:
                            error_msg = (
                                f"Package '{pkg_update.package_id}' not found for media buy '{actual_media_buy_id}'"
                            )
                            response_data = UpdateMediaBuyError(
                                errors=[Error(code="package_not_found", message=error_msg)],
                                context=to_context_object(req.context),
                            )
                            return response_data

                        product_id = pkg_record.package_config.get("product_id") if pkg_record.package_config else None

                        if product_id:
                            # Get product's placements
                            prod_stmt = select(ProductModel).where(
                                ProductModel.tenant_id == tenant["tenant_id"],
                                ProductModel.product_id == product_id,
                            )
                            product_obj = session.scalars(prod_stmt).first()

                            if product_obj and product_obj.placements:
                                available_placement_ids: set[str] = {
                                    str(p.get("placement_id")) for p in product_obj.placements if p.get("placement_id")
                                }
                                invalid_ids = all_requested_placement_ids - available_placement_ids
                                if invalid_ids:
                                    error_msg = f"Invalid placement_ids: {sorted(invalid_ids)}. Available: {sorted(available_placement_ids)}"
                                    response_data = UpdateMediaBuyError(
                                        errors=[Error(code="invalid_placement_ids", message=error_msg)],
                                        context=to_context_object(req.context),
                                    )
                                    return response_data
                            elif product_obj and not product_obj.placements:
                                # Product doesn't define placements, so placement targeting not supported
                                error_msg = f"Product '{product_id}' does not support placement targeting (no placements defined)"
                                response_data = UpdateMediaBuyError(
                                    errors=[Error(code="placement_targeting_not_supported", message=error_msg)],
                                    context=to_context_object(req.context),
                                )
                                return response_data

                    updated_assignments = []
                    new_assignments_created = []

                    for ca in pkg_update.creative_assignments:
                        # Schema validates and coerces dict inputs to LibraryCreativeAssignment
                        creative_id = ca.creative_id
                        weight = ca.weight
                        placement_ids = ca.placement_ids

                        # Find or create assignment record
                        assign_stmt = select(DBAssignment).where(
                            DBAssignment.tenant_id == tenant["tenant_id"],
                            DBAssignment.media_buy_id == actual_media_buy_id,
                            DBAssignment.package_id == pkg_update.package_id,
                            DBAssignment.creative_id == creative_id,
                        )
                        db_assignment = session.scalars(assign_stmt).first()

                        if db_assignment:
                            # Update existing assignment
                            if weight is not None:
                                db_assignment.weight = int(weight)
                            # adcp#208: persist placement_ids for placement-specific targeting
                            if placement_ids is not None:
                                db_assignment.placement_ids = placement_ids
                            updated_assignments.append(creative_id)
                        else:
                            # Create new assignment with weight and placement_ids
                            import uuid as uuid_module

                            assignment_id = f"assign_{uuid_module.uuid4().hex[:12]}"
                            new_assignment = DBAssignment(
                                assignment_id=assignment_id,
                                tenant_id=tenant["tenant_id"],
                                media_buy_id=actual_media_buy_id,
                                package_id=pkg_update.package_id,
                                creative_id=creative_id,
                                weight=int(weight) if weight is not None else 100,
                                # adcp#208: placement-specific targeting
                                placement_ids=placement_ids,
                            )
                            session.add(new_assignment)
                            updated_assignments.append(creative_id)
                            new_assignments_created.append(creative_id)

                    # If media buy was approved (approved_at set) but is in draft status
                    # (meaning it was approved without creatives), transition to pending_creatives
                    # Check whenever creative_assignments are being set (not just when new ones created)
                    if (
                        pkg_update.creative_assignments
                        and media_buy_obj.status == "draft"
                        and media_buy_obj.approved_at is not None
                    ):
                        media_buy_obj.status = "pending_creatives"
                        logger.info(
                            f"[UPDATE] Media buy {actual_media_buy_id} transitioned from draft to pending_creatives "
                            f"(creative_assignments processed: {updated_assignments})"
                        )

                    session.commit()

                    # Track in affected_packages
                    affected_packages_list.append(
                        AffectedPackage(
                            buyer_ref=req.buyer_ref or "",
                            package_id=pkg_update.package_id,
                            paused=False,
                            buyer_package_ref=pkg_update.package_id,
                            changes_applied={"creative_assignments_updated": updated_assignments},
                        )
                    )

            # Handle targeting_overlay updates
            if pkg_update.targeting_overlay is not None:
                # Validate package_id is provided
                if not pkg_update.package_id:
                    error_msg = "package_id is required when updating targeting_overlay"
                    response_data = UpdateMediaBuyError(
                        errors=[Error(code="missing_package_id", message=error_msg)],
                    )
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        response_data=response_data.model_dump(mode="json"),
                        error_message=error_msg,
                    )
                    return response_data

                from sqlalchemy.orm import attributes

                from src.core.database.database_session import get_db_session
                from src.core.database.models import MediaPackage as MediaPackageModel

                with get_db_session() as session:
                    # Get the package
                    package_stmt = select(MediaPackageModel).where(
                        MediaPackageModel.package_id == pkg_update.package_id,
                        MediaPackageModel.media_buy_id == req.media_buy_id,
                    )
                    media_package = session.scalars(package_stmt).first()

                    if not media_package:
                        error_msg = f"Package {pkg_update.package_id} not found for media buy {req.media_buy_id}"
                        response_data = UpdateMediaBuyError(
                            errors=[Error(code="package_not_found", message=error_msg)],
                        )
                        ctx_manager.update_workflow_step(
                            step.step_id,
                            status="failed",
                            response_data=response_data.model_dump(mode="json"),
                            error_message=error_msg,
                        )
                        return response_data

                    # Update targeting in package_config JSON
                    # Convert Targeting Pydantic model to dict
                    targeting_dict = (
                        pkg_update.targeting_overlay.model_dump(exclude_none=True)
                        if hasattr(pkg_update.targeting_overlay, "model_dump")
                        else pkg_update.targeting_overlay
                    )

                    media_package.package_config["targeting"] = targeting_dict
                    # Flag the JSON field as modified so SQLAlchemy persists it
                    attributes.flag_modified(media_package, "package_config")
                    session.commit()
                    logger.info(
                        f"[update_media_buy] Updated package {pkg_update.package_id} targeting: {targeting_dict}"
                    )

                    # Track targeting update in affected_packages
                    affected_packages_list.append(
                        AffectedPackage(
                            buyer_ref=pkg_update.package_id,
                            package_id=pkg_update.package_id,
                            paused=False,  # Package not paused (active)
                            changes_applied={"targeting": targeting_dict},
                            buyer_package_ref=pkg_update.package_id,  # Legacy compatibility
                        )
                    )

    # Handle budget updates (handle both float and Budget object)
    if req.budget is not None:
        # Extract budget amount - handle both float and Budget object
        total_budget: float
        budget_currency: str  # Renamed to avoid redefinition
        if isinstance(req.budget, int | float):
            total_budget = float(req.budget)
            budget_currency = "USD"  # Default currency for float budgets
        else:
            # Budget object with .total and .currency attributes
            total_budget = float(req.budget.total)
            budget_currency = req.budget.currency if hasattr(req.budget, "currency") else "USD"

        if total_budget <= 0:
            error_msg = f"Invalid budget: {total_budget}. Budget must be positive."
            response_data = UpdateMediaBuyError(
                errors=[Error(code="invalid_budget", message=error_msg)],
                context=to_context_object(req.context),
            )
            ctx_manager.update_workflow_step(
                step.step_id,
                status="failed",
                response_data=response_data.model_dump(mode="json"),
                error_message=error_msg,
            )
            return response_data

        # TODO: Sync budget change to GAM order
        # Currently only updates database - does NOT sync to GAM API
        # This creates data inconsistency between our database and GAM
        # Need to implement: adapter.orders_manager.update_order_budget(order_id, total_budget)

        # Persist top-level budget update to database
        # Note: In-memory media_buys dict removed after refactor
        # Media buys are persisted in database, not in-memory state
        if req.budget:
            from sqlalchemy import update as sqlalchemy_update

            from src.core.database.models import MediaBuy

            with get_db_session() as db_session:
                update_stmt = (
                    sqlalchemy_update(MediaBuy)
                    .where(MediaBuy.media_buy_id == req.media_buy_id)
                    .values(budget=total_budget, currency=budget_currency)
                )
                db_session.execute(update_stmt)
                db_session.commit()
                logger.warning(
                    f"⚠️  Updated MediaBuy {req.media_buy_id} budget to {total_budget} {budget_currency} in database ONLY"
                )
                logger.warning("⚠️  GAM sync NOT implemented - GAM still has old budget")

            # Track top-level budget update in affected_packages
            # When top-level budget changes, all packages are affected
            # Get all packages for this media buy from database to report them as affected
            from src.core.database.models import MediaPackage as MediaPackageModel

            with get_db_session() as db_session:
                stmt_packages = select(MediaPackageModel).filter_by(media_buy_id=req.media_buy_id)
                packages_result = list(db_session.scalars(stmt_packages).all())

                for pkg in packages_result:
                    # MediaPackage uses package_id as primary identifier
                    package_ref = pkg.package_id if pkg.package_id else None
                    if package_ref:
                        # Type narrowing: package_ref is guaranteed to be str at this point
                        package_ref_str: str = package_ref
                        affected_packages_list.append(
                            AffectedPackage(
                                buyer_ref=package_ref_str,  # Required: buyer's package reference
                                package_id=package_ref_str,  # Required: package identifier
                                paused=False,  # Package not paused (active)
                                buyer_package_ref=None,  # Internal field (not applicable for top-level budget updates)
                                changes_applied={
                                    "budget": {"updated": total_budget, "currency": budget_currency}
                                },  # Internal tracking field
                            )
                        )

    # Handle start_time/end_time updates
    if req.start_time is not None or req.end_time is not None:
        # TODO: Sync date changes to GAM order
        # Currently only updates database - does NOT sync to GAM API
        # This creates data inconsistency between our database and GAM
        # Need to implement: adapter.orders_manager.update_order_dates(order_id, start_time, end_time)

        from sqlalchemy import update as sqlalchemy_update

        from src.core.database.models import MediaBuy

        update_values = {}
        if req.start_time is not None:
            # Parse start_time (handle 'asap' and datetime strings)
            if isinstance(req.start_time, str):
                if req.start_time == "asap":
                    update_values["start_time"] = datetime.now(UTC)
                else:
                    update_values["start_time"] = datetime.fromisoformat(req.start_time.replace("Z", "+00:00"))
            elif isinstance(req.start_time, datetime):
                update_values["start_time"] = req.start_time

        if req.end_time is not None:
            # Parse end_time (datetime string or datetime object)
            if isinstance(req.end_time, str):
                update_values["end_time"] = datetime.fromisoformat(req.end_time.replace("Z", "+00:00"))
            elif isinstance(req.end_time, datetime):
                update_values["end_time"] = req.end_time

        if update_values:
            with get_db_session() as db_session:
                # Get existing media buy to check date range consistency
                from sqlalchemy import select as sqlalchemy_select

                existing_mb_stmt = sqlalchemy_select(MediaBuy).where(MediaBuy.media_buy_id == req.media_buy_id)
                existing_mb = db_session.scalars(existing_mb_stmt).first()

                if not existing_mb:
                    error_msg = f"Media buy {req.media_buy_id} not found"
                    response_data = UpdateMediaBuyError(
                        errors=[Error(code="media_buy_not_found", message=error_msg)],
                    )
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        response_data=response_data.model_dump(mode="json"),
                        error_message=error_msg,
                    )
                    return response_data

                # Validate date range: end_time must be after start_time
                # Type guard: Ensure we're working with datetime objects (not SQLAlchemy DateTime)
                start_val = update_values.get("start_time", existing_mb.start_time)
                end_val = update_values.get("end_time", existing_mb.end_time)

                # Convert to Python datetime if needed (handle SQLAlchemy DateTime)
                final_start_time: datetime | None = None
                final_end_time: datetime | None = None

                if start_val is not None:
                    final_start_time = (
                        start_val if isinstance(start_val, datetime) else datetime.fromisoformat(str(start_val))
                    )
                if end_val is not None:
                    final_end_time = end_val if isinstance(end_val, datetime) else datetime.fromisoformat(str(end_val))

                if final_start_time and final_end_time and final_end_time <= final_start_time:
                    error_msg = (
                        f"Invalid date range: end_time ({final_end_time.isoformat()}) "
                        f"must be after start_time ({final_start_time.isoformat()})"
                    )
                    response_data = UpdateMediaBuyError(
                        errors=[Error(code="invalid_date_range", message=error_msg)],
                    )
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        response_data=response_data.model_dump(mode="json"),
                        error_message=error_msg,
                    )
                    return response_data

                update_stmt = (
                    sqlalchemy_update(MediaBuy).where(MediaBuy.media_buy_id == req.media_buy_id).values(**update_values)
                )
                db_session.execute(update_stmt)
                db_session.commit()
                logger.warning(
                    f"⚠️  Updated MediaBuy {req.media_buy_id} dates in database ONLY: "
                    f"start_time={update_values.get('start_time')}, end_time={update_values.get('end_time')}"
                )
                logger.warning("⚠️  GAM sync NOT implemented - GAM still has old dates")

    # Note: Budget validation already done above (lines 286-396)
    # Package-level updates already handled above (lines 422-709)
    # Targeting updates are handled via packages (AdCP spec v2.4)

    # Create ObjectWorkflowMapping to link media buy update to workflow step
    # This enables webhook delivery when the update completes
    from src.core.database.database_session import get_db_session
    from src.core.database.models import ObjectWorkflowMapping

    with get_db_session() as session:
        mapping = ObjectWorkflowMapping(
            step_id=step.step_id,
            object_type="media_buy",
            object_id=req.media_buy_id,
            action="update",
        )
        session.add(mapping)
        session.commit()

    # Build final response first
    logger.info(f"[update_media_buy] Final affected_packages before return: {affected_packages_list}")

    # UpdateMediaBuySuccess extends adcp v1.2.1 with internal fields (workflow_step_id, affected_packages)
    # affected_packages_list contains AffectedPackage objects with both:
    # - AdCP-required fields (buyer_ref, package_id) for spec compliance
    # - Internal tracking fields (buyer_package_ref, changes_applied) excluded via exclude=True

    final_response = UpdateMediaBuySuccess(
        media_buy_id=req.media_buy_id or "",
        buyer_ref=req.buyer_ref or "",
        affected_packages=affected_packages_list,
        context=to_context_object(req.context),
    )

    # Log successful update_media_buy call
    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
    audit_logger.log_operation(
        operation="update_media_buy",
        principal_name=principal_id or "anonymous",
        principal_id=principal_id or "anonymous",
        adapter_id="mcp_server",
        success=True,
        details={
            "media_buy_id": req.media_buy_id,
            "buyer_ref": req.buyer_ref,
            "affected_packages_count": len(affected_packages_list),
            "has_budget_update": req.budget is not None,
            "has_pause_update": req.paused is not None,
            "has_packages_update": req.packages is not None and len(req.packages) > 0,
        },
    )

    # Persist success with response data, then return
    # Use mode="json" to ensure enums are serialized as strings for JSONB storage
    ctx_manager.update_workflow_step(
        step.step_id,
        status="completed",
        response_data=final_response.model_dump(mode="json"),
    )

    return final_response


def update_media_buy(
    media_buy_id: str | None = None,
    buyer_ref: str | None = None,
    paused: bool = None,
    flight_start_date: str = None,
    flight_end_date: str = None,
    budget: float = None,
    currency: str = None,
    targeting_overlay: TargetingOverlay | None = None,
    start_time: str = None,
    end_time: str = None,
    pacing: str = None,
    daily_budget: float = None,
    packages: list[UpdatePackage] | None = None,
    creatives: list = None,
    push_notification_config: PushNotificationConfig | None = None,
    context: ContextObject | None = None,  # payload-level context
    ctx: Context | ToolContext | None = None,
):
    """Update a media buy with campaign-level and/or package-level changes.

    MCP tool wrapper that delegates to the shared implementation.
    FastMCP automatically validates and coerces JSON inputs to Pydantic models.

    Args:
        media_buy_id: Media buy ID to update (oneOf with buyer_ref - exactly one required)
        buyer_ref: Buyer reference to identify media buy (oneOf with media_buy_id - exactly one required)
        paused: True to pause campaign, False to resume (adcp 2.12.0+)
        flight_start_date: Change start date (if not started)
        flight_end_date: Extend or shorten campaign
        budget: Update total budget
        currency: Update currency (ISO 4217)
        targeting_overlay: Update global targeting
        start_time: Update start datetime
        end_time: Update end datetime
        pacing: Pacing strategy (even, asap, daily_budget)
        daily_budget: Daily spend cap across all packages
        packages: Package-specific updates
        creatives: Add new creatives
        push_notification_config: Push notification config for async notifications (AdCP spec, optional)
        context: FastMCP context (automatically provided)

    Returns:
        ToolResult with UpdateMediaBuyResponse data
    """
    # Convert typed Pydantic models to dicts for the impl
    # FastMCP already coerced JSON inputs to these types
    targeting_overlay_dict = targeting_overlay.model_dump(mode="json") if targeting_overlay else None
    packages_dicts = [p.model_dump(mode="json") for p in packages] if packages else None
    push_config_dict = push_notification_config.model_dump(mode="json") if push_notification_config else None
    context_dict = context.model_dump(mode="json") if context else None

    response = _update_media_buy_impl(
        media_buy_id=media_buy_id,
        buyer_ref=buyer_ref,
        paused=paused,
        flight_start_date=flight_start_date,
        flight_end_date=flight_end_date,
        budget=budget,
        currency_param=currency,  # Pass as currency_param
        targeting_overlay=targeting_overlay_dict,
        start_time=start_time,
        end_time=end_time,
        pacing=pacing,
        daily_budget=daily_budget,
        packages=packages_dicts,
        creatives=creatives,
        push_notification_config=push_config_dict,
        context=context_dict,
        ctx=ctx,
    )
    return ToolResult(content=str(response), structured_content=response)


def update_media_buy_raw(
    media_buy_id: str | None = None,
    buyer_ref: str | None = None,
    paused: bool = None,
    flight_start_date: str = None,
    flight_end_date: str = None,
    budget: float = None,
    currency: str = None,
    targeting_overlay: dict = None,
    start_time: str = None,
    end_time: str = None,
    pacing: str = None,
    daily_budget: float = None,
    packages: list = None,
    creatives: list = None,
    push_notification_config: dict = None,
    context: dict | None = None,  # payload-level context
    ctx: Context | ToolContext | None = None,
):
    """Update an existing media buy (raw function for A2A server use).

    Delegates to the shared implementation.

    Args:
        media_buy_id: The ID of the media buy to update (oneOf with buyer_ref - exactly one required)
        buyer_ref: Buyer reference to identify media buy (oneOf with media_buy_id - exactly one required)
        paused: True to pause campaign, False to resume (adcp 2.12.0+)
        flight_start_date: Change start date
        flight_end_date: Change end date
        budget: Update total budget
        currency: Update currency
        targeting_overlay: Update targeting
        start_time: Update start datetime
        end_time: Update end datetime
        pacing: Pacing strategy
        daily_budget: Daily budget cap
        packages: Package updates
        creatives: Creative updates
        push_notification_config: Push notification config for status updates
        context: Application level context per adcp spec
        ctx: Context for authentication

    Returns:
        UpdateMediaBuyResponse
    """
    return _update_media_buy_impl(
        media_buy_id=media_buy_id,
        buyer_ref=buyer_ref,
        paused=paused,
        flight_start_date=flight_start_date,
        flight_end_date=flight_end_date,
        budget=budget,
        currency_param=currency,  # Pass as currency_param
        targeting_overlay=targeting_overlay,
        start_time=start_time,
        end_time=end_time,
        pacing=pacing,
        daily_budget=daily_budget,
        packages=packages,
        creatives=creatives,
        push_notification_config=push_notification_config,
        context=context,
        ctx=ctx,
    )
