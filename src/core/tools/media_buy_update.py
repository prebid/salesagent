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

from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import ValidationError
from sqlalchemy import select

from src.core.tool_context import ToolContext

logger = logging.getLogger(__name__)

from adcp.types.generated_poc.error import Error

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
            "Authentication required: Missing or invalid x-adcp-auth header. "
            "Media buy updates require authentication."
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
    media_buy_id: str,
    buyer_ref: str | None = None,
    active: bool | None = None,
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
        if pacing in ("even", "asap", "daily_budget"):
            pacing_val = pacing  # type: ignore[assignment]
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
    request_params = {
        "media_buy_id": media_buy_id,
        "buyer_ref": buyer_ref,
        "active": active,
        "start_time": start_time,
        "end_time": end_time,
        "budget": budget_obj,
        "packages": packages,
        "push_notification_config": push_notification_config,
        "context": context,
    }
    # Remove None values to avoid validation errors in strict mode
    request_params = {k: v for k, v in request_params.items() if v is not None}

    try:
        req = UpdateMediaBuyRequest(**request_params)  # type: ignore[arg-type]
    except ValidationError as e:
        raise ToolError(format_validation_error(e, context="update_media_buy request")) from e

    # Initialize tracking for affected packages (internal tracking, not part of schema)
    affected_packages_list: list[AffectedPackage] = []

    if ctx is None:
        raise ValueError("Context is required for update_media_buy")

    # Resolve media_buy_id from buyer_ref if needed (AdCP oneOf constraint)
    media_buy_id_to_use = req.media_buy_id
    if not media_buy_id_to_use and req.buyer_ref:
        # Look up media_buy_id by buyer_ref
        from src.core.database.database_session import get_db_session
        from src.core.database.models import MediaBuy as MediaBuyModel

        tenant = get_current_tenant()
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

    _verify_principal(media_buy_id_to_use, ctx)
    principal_id = get_principal_id_from_context(ctx)  # Already verified by _verify_principal

    # Verify principal_id is not None (get_principal_id_from_context should raise if None)
    if principal_id is None:
        raise ValueError("principal_id is required but was None")

    tenant = get_current_tenant()

    # Create or get persistent context
    ctx_manager = get_context_manager()
    ctx_id = ctx.headers.get("x-context-id") if hasattr(ctx, "headers") else None
    persistent_ctx = ctx_manager.get_or_create_context(
        tenant_id=tenant["tenant_id"],
        principal_id=principal_id,  # Now guaranteed to be str
        context_id=ctx_id,
        is_async=True,
    )

    # Verify persistent_ctx is not None
    if persistent_ctx is None:
        raise ValueError("Failed to create or get persistent context")

    # Create workflow step for this tool call
    step = ctx_manager.create_workflow_step(
        context_id=persistent_ctx.context_id,  # Now safe to access
        step_type="tool_call",
        owner="principal",
        status="in_progress",
        tool_name="update_media_buy",
        request_data=req.model_dump(mode="json"),  # Convert dates to strings
    )

    principal = get_principal_object(principal_id)  # Now guaranteed to be str
    if not principal:
        error_msg = f"Principal {principal_id} not found"
        response_data = UpdateMediaBuyError(
            errors=[Error(code="principal_not_found", message=error_msg)],  # type: ignore[list-item]
            context=req.context,
        )
        ctx_manager.update_workflow_step(
            step.step_id,
            status="failed",
            response_data=response_data.model_dump(),
            error_message=error_msg,
        )
        return response_data  # type: ignore[return-value]

    # Extract testing context for dry_run and testing_context parameters
    testing_ctx = get_testing_context(ctx)

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
        # Build response first, then persist on workflow step, then return
        # UpdateMediaBuySuccess extends adcp v1.2.1 with internal fields (workflow_step_id, affected_packages)
        approval_response = UpdateMediaBuySuccess(
            media_buy_id=req.media_buy_id or "",
            buyer_ref=req.buyer_ref or "",
            affected_packages=[],  # Internal field for tracking changes
            context=req.context,
        )
        ctx_manager.update_workflow_step(
            step.step_id,
            status="requires_approval",
            response_data=approval_response.model_dump(),
            add_comment={"user": "system", "comment": "Publisher requires manual approval for all media buy updates"},
        )
        return approval_response  # type: ignore[return-value]

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
                        errors=[Error(code="currency_not_supported", message=error_msg)],  # type: ignore[list-item]
                        context=req.context,
                    )
                    ctx_manager.update_workflow_step(
                        step.step_id, status="failed", response_data=response_data.model_dump(), error_message=error_msg
                    )
                    return response_data  # type: ignore[return-value]

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
                                    errors=[Error(code="budget_limit_exceeded", message=error_msg)],  # type: ignore[list-item]
                                    context=req.context,
                                )
                                ctx_manager.update_workflow_step(
                                    step.step_id,
                                    status="failed",
                                    response_data=response_data.model_dump(),
                                    error_message=error_msg,
                                )
                                return response_data  # type: ignore[return-value]

    # Handle campaign-level updates
    if req.active is not None:
        action = "resume_media_buy" if req.active else "pause_media_buy"
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
            return UpdateMediaBuyError(errors=result.errors)  # type: ignore[return-value]
        else:
            # UpdateMediaBuySuccess extends adcp v1.2.1 with internal fields
            # Use getattr to safely access discriminated union fields
            media_buy_id = getattr(result, "media_buy_id", req.media_buy_id or "")
            buyer_ref_val = getattr(result, "buyer_ref", req.buyer_ref or "")
            affected_pkgs = getattr(result, "affected_packages", [])

            success_response = UpdateMediaBuySuccess(
                media_buy_id=media_buy_id,
                buyer_ref=buyer_ref_val,
                affected_packages=affected_pkgs,  # type: ignore[arg-type]
            )
            return success_response  # type: ignore[return-value]

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
                        response_data=response_data.model_dump(),
                        error_message=error_message,
                    )
                    return response_data  # type: ignore[return-value]

            # Handle budget updates
            if pkg_update.budget is not None:
                # Validate package_id is provided (required for budget updates)
                if not pkg_update.package_id:
                    error_msg = "package_id is required when updating package budget"
                    response_data = UpdateMediaBuyError(
                        errors=[Error(code="missing_package_id", message=error_msg)],  # type: ignore[list-item]
                        context=req.context,
                    )
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        response_data=response_data.model_dump(),
                        error_message=error_msg,
                    )
                    return response_data  # type: ignore[return-value]

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
                        response_data=response_data.model_dump(),
                        error_message=error_message,
                    )
                    return response_data  # type: ignore[return-value]

                # Track budget update in affected_packages
                # At this point, pkg_update.package_id is guaranteed to be str (checked above)
                affected_packages_list.append(
                    AffectedPackage(
                        buyer_ref=req.buyer_ref or "",  # Required by AdCP
                        package_id=pkg_update.package_id,  # Required by AdCP (guaranteed str)
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
                        errors=[Error(code="missing_package_id", message=error_msg)],  # type: ignore[list-item]
                        context=req.context,
                    )
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        response_data=response_data.model_dump(),
                        error_message=error_msg,
                    )
                    return response_data  # type: ignore[return-value]

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
                            errors=[Error(code="media_buy_not_found", message=error_msg)],  # type: ignore[list-item]
                            context=req.context,
                        )
                        ctx_manager.update_workflow_step(
                            step.step_id,
                            status="failed",
                            response_data=response_data.model_dump(),
                            error_message=error_msg,
                        )
                        return response_data  # type: ignore[return-value]

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
                            errors=[Error(code="creatives_not_found", message=error_msg)],  # type: ignore[list-item]
                            context=req.context,
                        )
                        ctx_manager.update_workflow_step(
                            step.step_id,
                            status="failed",
                            response_data=response_data.model_dump(),
                            error_message=error_msg,
                        )
                        return response_data  # type: ignore[return-value]

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

                    session.commit()

                    # Store results for affected_packages response
                    affected_packages_list.append(
                        AffectedPackage(
                            buyer_ref=req.buyer_ref or "",  # Required by AdCP
                            package_id=pkg_update.package_id,  # Required by AdCP
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
                        response_data=response_data.model_dump(),
                        error_message=error_msg,
                    )
                    return response_data  # type: ignore[return-value]

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
                            response_data=response_data.model_dump(),
                            error_message=error_msg,
                        )
                        return response_data  # type: ignore[return-value]

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
                errors=[Error(code="invalid_budget", message=error_msg)],  # type: ignore[list-item]
                context=req.context,
            )
            ctx_manager.update_workflow_step(
                step.step_id,
                status="failed",
                response_data=response_data.model_dump(),
                error_message=error_msg,
            )
            return response_data  # type: ignore[return-value]

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
                        response_data=response_data.model_dump(),
                        error_message=error_msg,
                    )
                    return response_data  # type: ignore[return-value]

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
                        response_data=response_data.model_dump(),
                        error_message=error_msg,
                    )
                    return response_data  # type: ignore[return-value]

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
        context=req.context,
    )

    # Persist success with response data, then return
    ctx_manager.update_workflow_step(
        step.step_id,
        status="completed",
        response_data=final_response.model_dump(),
    )

    return final_response  # type: ignore[return-value]


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
    context: dict | None = None,  # payload-level context
    ctx: Context | ToolContext | None = None,
):
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
        ToolResult with UpdateMediaBuyResponse data
    """
    response = _update_media_buy_impl(
        media_buy_id=media_buy_id,
        buyer_ref=buyer_ref,
        active=active,
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
    return ToolResult(content=str(response), structured_content=response.model_dump())


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
    context: dict | None = None,  # payload-level context
    ctx: Context | ToolContext | None = None,
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
        context: Application level context per adcp spec
        ctx: Context for authentication

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
