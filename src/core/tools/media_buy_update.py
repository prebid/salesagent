"""Update Media Buy tool implementation.

Handles media buy updates including:
- Campaign-level budget and date changes
- Package-level budget adjustments
- Creative assignments per package
- Activation/pause controls
- Currency limit validation
"""

import logging
from datetime import UTC, date, datetime

from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from pydantic import ValidationError
from sqlalchemy import select

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
from src.core.schema_adapters import UpdateMediaBuyResponse
from src.core.schemas import UpdateMediaBuyRequest
from src.core.testing_hooks import get_testing_context
from src.core.validation_helpers import format_validation_error


def _verify_principal(media_buy_id: str, context: Context):
    """Verify that the principal from context owns the media buy.

    Checks database for media buy ownership, not in-memory dictionary.

    Args:
        media_buy_id: Media buy ID to verify
        context: FastMCP context with principal info

    Raises:
        ValueError: Media buy not found
        PermissionError: Principal doesn't own media buy
    """

    from src.core.database.models import MediaBuy as MediaBuyModel

    principal_id = get_principal_id_from_context(context)
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
            # Log security violation
            # Note: principal_id guaranteed to be str here (checked by get_principal_id_from_context)
            assert principal_id is not None, "principal_id should be set at this point"
            security_logger = get_audit_logger("AdCP", tenant["tenant_id"])
            security_logger.log_security_violation(
                operation="access_media_buy",
                principal_id=principal_id,
                resource_id=media_buy_id,
                reason=f"Principal does not own media buy (owner: {media_buy.principal_id})",
            )
            raise PermissionError(f"Principal '{principal_id}' does not own media buy '{media_buy_id}'.")


def _update_media_buy_impl(
    media_buy_id: str,
    buyer_ref: str | None = None,
    active: bool | None = None,
    flight_start_date: str | None = None,
    flight_end_date: str | None = None,
    budget: float | None = None,
    currency: str | None = None,
    targeting_overlay: dict | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    pacing: str | None = None,
    daily_budget: float | None = None,
    packages: list | None = None,
    creatives: list | None = None,
    push_notification_config: dict | None = None,
    context: Context | None = None,
) -> UpdateMediaBuyResponse:
    """Shared implementation for update_media_buy (used by both MCP and A2A).

    Update a media buy with campaign-level and/or package-level changes.

    Args:
        media_buy_id: Media buy ID to update (required)
        buyer_ref: Update buyer reference
        active: True to activate, False to pause entire campaign
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
        context: FastMCP context (automatically provided)

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
        if pacing in ("even", "asap", "daily_budget"):
            pacing_val = pacing  # type: ignore[assignment]
        budget_obj = Budget(
            total=budget,
            currency=currency or "USD",  # Default to USD if not specified
            pacing=pacing_val,  # Default pacing
            daily_cap=daily_budget,  # Map daily_budget to daily_cap
            auto_pause_on_budget_exhaustion=None,
        )

    # Build request with only valid AdCP fields
    # Note: flight_start_date, flight_end_date are mapped to start_time/end_time above
    # creatives and targeting_overlay are deprecated - use packages for updates
    # Filter out None values to avoid passing them to the request (strict validation in dev mode)
    request_params = {
        "media_buy_id": media_buy_id,
        "buyer_ref": buyer_ref,
        "active": active,
        "start_time": start_time,
        "end_time": end_time,
        "budget": budget_obj,
        "packages": packages,
        "push_notification_config": push_notification_config,
    }
    # Remove None values to avoid validation errors in strict mode
    request_params = {k: v for k, v in request_params.items() if v is not None}

    try:
        req = UpdateMediaBuyRequest(**request_params)  # type: ignore[arg-type]
    except ValidationError as e:
        raise ToolError(format_validation_error(e, context="update_media_buy request")) from e

    if context is None:
        raise ValueError("Context is required for update_media_buy")

    if not req.media_buy_id:
        # TODO: Handle buyer_ref case - for now just raise error
        raise ValueError("media_buy_id is required (buyer_ref lookup not yet implemented)")

    _verify_principal(req.media_buy_id, context)
    principal_id = get_principal_id_from_context(context)  # Already verified by _verify_principal
    tenant = get_current_tenant()

    # Create or get persistent context
    ctx_manager = get_context_manager()
    ctx_id = context.headers.get("x-context-id") if hasattr(context, "headers") else None
    persistent_ctx = ctx_manager.get_or_create_context(
        tenant_id=tenant["tenant_id"],
        principal_id=principal_id,
        context_id=ctx_id,
        is_async=True,
    )

    # Create workflow step for this tool call
    step = ctx_manager.create_workflow_step(
        context_id=persistent_ctx.context_id,
        step_type="tool_call",
        owner="principal",
        status="in_progress",
        tool_name="update_media_buy",
        request_data=req.model_dump(mode="json"),  # Convert dates to strings
    )

    principal = get_principal_object(principal_id)
    if not principal:
        error_msg = f"Principal {principal_id} not found"
        ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
        return UpdateMediaBuyResponse(
            media_buy_id=req.media_buy_id or "",
            buyer_ref=req.buyer_ref or "",
            implementation_date=None,
            errors=[{"code": "principal_not_found", "message": error_msg}],
        )

    # Extract testing context for dry_run and testing_context parameters
    testing_ctx = get_testing_context(context)

    adapter = get_adapter(principal, dry_run=testing_ctx.dry_run, testing_context=testing_ctx)
    today = req.today or date.today()

    # Check if manual approval is required
    manual_approval_required = (
        adapter.manual_approval_required if hasattr(adapter, "manual_approval_required") else False
    )
    manual_approval_operations = (
        adapter.manual_approval_operations if hasattr(adapter, "manual_approval_operations") else []
    )

    if manual_approval_required and "update_media_buy" in manual_approval_operations:
        # Workflow step already created above - update its status
        ctx_manager.update_workflow_step(
            step.step_id,
            status="requires_approval",
            add_comment={"user": "system", "comment": "Publisher requires manual approval for all media buy updates"},
        )

        return UpdateMediaBuyResponse(
            media_buy_id=req.media_buy_id or "",
            buyer_ref=req.buyer_ref or "",
            implementation_date=None,
        )

    # Validate currency limits if flight dates or budget changes
    # This prevents workarounds where buyers extend flight to bypass daily max
    if req.start_time or req.end_time or req.budget or (req.packages and any(pkg.budget for pkg in req.packages)):
        from decimal import Decimal

        from sqlalchemy import select

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
                if req.budget and hasattr(req.budget, "currency"):
                    request_currency = str(req.budget.currency)
                else:
                    request_currency = str(media_buy.currency) if media_buy.currency else "USD"

                # Get currency limit
                currency_stmt = select(CurrencyLimit).where(
                    CurrencyLimit.tenant_id == tenant["tenant_id"], CurrencyLimit.currency_code == request_currency
                )
                currency_limit = session.scalars(currency_stmt).first()

                if not currency_limit:
                    error_msg = f"Currency {request_currency} is not supported by this publisher."
                    ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
                    return UpdateMediaBuyResponse(
                        media_buy_id=req.media_buy_id or "",
                        buyer_ref=req.buyer_ref or "",
                        implementation_date=None,
                        errors=[{"code": "currency_not_supported", "message": error_msg}],
                    )

                # Calculate new flight duration
                start = req.start_time if req.start_time else media_buy.start_time
                end = req.end_time if req.end_time else media_buy.end_time

                # Parse datetime strings if needed, handle 'asap' (AdCP v1.7.0)
                from datetime import datetime as dt

                if isinstance(start, str):
                    if start == "asap":
                        start = dt.now(UTC)
                    else:
                        start = dt.fromisoformat(start.replace("Z", "+00:00"))
                if isinstance(end, str):
                    end = dt.fromisoformat(end.replace("Z", "+00:00"))

                flight_days = (end - start).days
                if flight_days <= 0:
                    flight_days = 1

                # Validate max daily spend for packages
                if currency_limit.max_daily_package_spend and req.packages:
                    for pkg_update in req.packages:
                        if pkg_update.budget:
                            # Extract budget amount - handle both Budget object and legacy float
                            from src.core.schemas import Budget, extract_budget_amount

                            if isinstance(pkg_update.budget, Budget):
                                pkg_budget_amount, _ = extract_budget_amount(pkg_update.budget, request_currency)
                            else:
                                pkg_budget_amount = float(pkg_update.budget)

                            package_budget = Decimal(str(pkg_budget_amount))
                            package_daily = package_budget / Decimal(str(flight_days))

                            if package_daily > currency_limit.max_daily_package_spend:
                                error_msg = (
                                    f"Updated package daily budget ({package_daily} {request_currency}) "
                                    f"exceeds maximum ({currency_limit.max_daily_package_spend} {request_currency}). "
                                    f"Flight date changes that reduce daily budget are not allowed to bypass limits."
                                )
                                ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
                                return UpdateMediaBuyResponse(
                                    media_buy_id=req.media_buy_id or "",
                                    buyer_ref=req.buyer_ref or "",
                                    implementation_date=None,
                                    errors=[{"code": "budget_limit_exceeded", "message": error_msg}],
                                )

    # Handle campaign-level updates
    if req.active is not None:
        action = "resume_media_buy" if req.active else "pause_media_buy"
        result = adapter.update_media_buy(
            media_buy_id=req.media_buy_id,
            buyer_ref=req.buyer_ref or "",
            action=action,
            package_id=None,
            budget=None,
            today=datetime.combine(today, datetime.min.time()),
        )
        if result.errors:
            return result

    # Handle package-level updates
    if req.packages:
        for pkg_update in req.packages:
            # Handle active/pause state
            if pkg_update.active is not None:
                action = "resume_package" if pkg_update.active else "pause_package"
                result = adapter.update_media_buy(
                    media_buy_id=req.media_buy_id,
                    buyer_ref=req.buyer_ref or "",
                    action=action,
                    package_id=pkg_update.package_id,
                    budget=None,
                    today=datetime.combine(today, datetime.min.time()),
                )
                if result.errors:
                    error_message = (
                        result.errors[0].get("message", "Update failed") if result.errors else "Update failed"
                    )
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        error_message=error_message,
                    )
                    return result

            # Handle budget updates
            if pkg_update.budget is not None:
                # Extract budget amount - handle both Budget object and legacy float
                from src.core.schemas import Budget, extract_budget_amount

                if isinstance(pkg_update.budget, Budget):
                    budget_amount, currency = extract_budget_amount(pkg_update.budget, "USD")
                else:
                    # Legacy float format
                    budget_amount = float(pkg_update.budget)
                    currency = "USD"

                result = adapter.update_media_buy(
                    media_buy_id=req.media_buy_id,
                    buyer_ref=req.buyer_ref or "",
                    action="update_package_budget",
                    package_id=pkg_update.package_id,
                    budget=int(budget_amount),
                    today=datetime.combine(today, datetime.min.time()),
                )
                if result.errors:
                    error_message = (
                        result.errors[0].get("message", "Update failed") if result.errors else "Update failed"
                    )
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        error_message=error_message,
                    )
                    return result

                # Track budget update in affected_packages
                if not hasattr(req, "_affected_packages"):
                    req._affected_packages = []
                req._affected_packages.append(
                    {
                        "buyer_package_ref": pkg_update.package_id,
                        "changes_applied": {"budget": {"updated": budget_amount, "currency": currency}},
                    }
                )

            # Handle creative_ids updates (AdCP v2.2.0+)
            if pkg_update.creative_ids is not None:
                # Validate package_id is provided
                if not pkg_update.package_id:
                    error_msg = "package_id is required when updating creative_ids"
                    ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
                    return UpdateMediaBuyResponse(
                        media_buy_id=req.media_buy_id or "",
                        buyer_ref=req.buyer_ref or "",
                        implementation_date=None,
                        errors=[{"code": "missing_package_id", "message": error_msg}],
                    )

                from sqlalchemy import select

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
                        ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
                        return UpdateMediaBuyResponse(
                            media_buy_id=req.media_buy_id or "",
                            buyer_ref=req.buyer_ref or "",
                            implementation_date=None,
                            errors=[{"code": "media_buy_not_found", "message": error_msg}],
                        )

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
                        ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
                        return UpdateMediaBuyResponse(
                            media_buy_id=req.media_buy_id or "",
                            buyer_ref=req.buyer_ref or "",
                            implementation_date=None,
                            errors=[{"code": "creatives_not_found", "message": error_msg}],
                        )

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

                    session.commit()

                    # Store results for affected_packages response
                    if not hasattr(req, "_affected_packages"):
                        req._affected_packages = []
                    req._affected_packages.append(
                        {
                            "buyer_package_ref": pkg_update.package_id,
                            "changes_applied": {
                                "creative_ids": {
                                    "added": list(added_ids),
                                    "removed": list(removed_ids),
                                    "current": pkg_update.creative_ids,
                                }
                            },
                        }
                    )

    # Handle budget updates (Budget object from AdCP spec - v1.8.0 compatible)
    if req.budget is not None:
        from src.core.schemas import extract_budget_amount

        # For UpdateMediaBuyRequest, budget is always a Budget object (not a float)
        # The currency comes from the Budget object itself or defaults to USD
        total_budget, currency = extract_budget_amount(req.budget, "USD")

        if total_budget <= 0:
            error_msg = f"Invalid budget: {total_budget}. Budget must be positive."
            ctx_manager.update_workflow_step(step.step_id, status="failed", error_message=error_msg)
            return UpdateMediaBuyResponse(
                media_buy_id=req.media_buy_id or "",
                buyer_ref=req.buyer_ref or "",
                implementation_date=None,
                errors=[{"code": "invalid_budget", "message": error_msg}],
            )

        # Persist top-level budget update to database
        # Note: In-memory media_buys dict removed after refactor
        # Media buys are persisted in database, not in-memory state
        if req.budget:
            from sqlalchemy import update

            from src.core.database.models import MediaBuy

            with get_db_session() as db_session:
                stmt = (
                    update(MediaBuy)
                    .where(MediaBuy.media_buy_id == req.media_buy_id)
                    .values(budget=total_budget, currency=currency)
                )
                db_session.execute(stmt)
                db_session.commit()
                logger.info(
                    f"[update_media_buy] Updated MediaBuy {req.media_buy_id} budget to {total_budget} {currency}"
                )

            # Track top-level budget update in affected_packages
            # When top-level budget changes, all packages are affected
            if not hasattr(req, "_affected_packages"):
                req._affected_packages = []

            # Get all packages for this media buy from database to report them as affected
            from src.core.database.models import MediaPackage as MediaPackageModel

            with get_db_session() as db_session:
                stmt_packages = select(MediaPackageModel).filter_by(media_buy_id=req.media_buy_id)
                packages = db_session.scalars(stmt_packages).all()

                for pkg in packages:
                    package_ref = pkg.package_id if pkg.package_id else pkg.buyer_ref
                    if package_ref:
                        req._affected_packages.append(
                            {
                                "buyer_package_ref": package_ref,
                                "changes_applied": {"budget": {"updated": total_budget, "currency": currency}},
                            }
                        )

    # Note: Budget validation already done above (lines 4318-4336)
    # Package-level updates already handled above (lines 4266-4316)
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

    # Update workflow step with success
    ctx_manager.update_workflow_step(
        step.step_id,
        status="completed",
        response_data={
            "status": "accepted",
            "updates_applied": {
                "campaign_level": req.active is not None,
                "package_count": len(req.packages) if req.packages else 0,
                "budget": req.budget is not None,
                "flight_dates": req.start_time is not None or req.end_time is not None,
            },
        },
    )

    # Build affected_packages from stored results
    affected_packages = getattr(req, "_affected_packages", [])
    logger.info(f"[update_media_buy] Final affected_packages before return: {affected_packages}")

    return UpdateMediaBuyResponse(
        media_buy_id=req.media_buy_id or "",
        buyer_ref=req.buyer_ref or "",
        implementation_date=None,
        affected_packages=affected_packages if affected_packages else None,
    )


def update_media_buy(
    media_buy_id: str,
    buyer_ref: str = None,
    active: bool = None,
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
    push_notification_config: dict | None = None,
    context: Context = None,
) -> UpdateMediaBuyResponse:
    """Update a media buy with campaign-level and/or package-level changes.

    MCP tool wrapper that delegates to the shared implementation.

    Args:
        media_buy_id: Media buy ID to update (required)
        buyer_ref: Update buyer reference
        active: True to activate, False to pause entire campaign
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
        UpdateMediaBuyResponse with updated media buy details
    """
    return _update_media_buy_impl(
        media_buy_id=media_buy_id,
        buyer_ref=buyer_ref,
        active=active,
        flight_start_date=flight_start_date,
        flight_end_date=flight_end_date,
        budget=budget,
        currency=currency,
        targeting_overlay=targeting_overlay,
        start_time=start_time,
        end_time=end_time,
        pacing=pacing,
        daily_budget=daily_budget,
        packages=packages,
        creatives=creatives,
        push_notification_config=push_notification_config,
        context=context,
    )


def update_media_buy_raw(
    media_buy_id: str,
    buyer_ref: str = None,
    active: bool = None,
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
    context: Context = None,
):
    """Update an existing media buy (raw function for A2A server use).

    Delegates to the shared implementation.

    Args:
        media_buy_id: The ID of the media buy to update
        buyer_ref: Update buyer reference
        active: True to activate, False to pause
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
        context: Context for authentication

    Returns:
        UpdateMediaBuyResponse
    """
    return _update_media_buy_impl(
        media_buy_id=media_buy_id,
        buyer_ref=buyer_ref,
        active=active,
        flight_start_date=flight_start_date,
        flight_end_date=flight_end_date,
        budget=budget,
        currency=currency,
        targeting_overlay=targeting_overlay,
        start_time=start_time,
        end_time=end_time,
        pacing=pacing,
        daily_budget=daily_budget,
        packages=packages,
        creatives=creatives,
        push_notification_config=push_notification_config,
        context=context,
    )
