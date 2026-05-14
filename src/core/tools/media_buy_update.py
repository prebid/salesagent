"""Update Media Buy tool implementation.

Handles media buy updates including:
- Campaign-level budget and date changes
- Package-level budget adjustments
- Creative assignments per package
- Activation/pause controls
- Currency limit validation
"""

import logging
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from adcp import PushNotificationConfig
from adcp.server.helpers import MEDIA_BUY_STATE_MACHINE, is_terminal_status, valid_actions_for_status
from pydantic import Field

# ---------------------------------------------------------------------------
# Financial policy constants (F-05)
# ---------------------------------------------------------------------------

#: Absolute upper bound for any campaign-level budget update.
#: Configurable via MAX_CAMPAIGN_BUDGET_USD env var; default 10,000,000.
MAX_CAMPAIGN_BUDGET: Decimal = Decimal(os.environ.get("MAX_CAMPAIGN_BUDGET_USD", "10000000"))

from adcp.types import Error
from adcp.types.generated_poc.core.context import ContextObject
from adcp.types.generated_poc.core.reporting_webhook import ReportingWebhook
from adcp.types.generated_poc.core.targeting import TargetingOverlay
from adcp.types.generated_poc.enums.creative_action import CreativeAction
from adcp.types.generated_poc.media_buy.package_update import PackageUpdate as UpdatePackage
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import ValidationError
from sqlalchemy import select

from src.core.exceptions import (
    AdCPAuthenticationError,
    AdCPAuthorizationError,
    AdCPValidationError,
)
from src.core.tool_context import ToolContext

logger = logging.getLogger(__name__)

from src.core.audit_logger import get_audit_logger
from src.core.auth import (
    get_principal_object,
)
from src.core.context_manager import get_context_manager
from src.core.database.repositories import MediaBuyRepository, MediaBuyUoW
from src.core.helpers.adapter_helpers import get_adapter
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    AffectedPackage,
    Budget,
    UpdateMediaBuyError,
    UpdateMediaBuyRequest,
    UpdateMediaBuySuccess,
)
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.financial_validation import (
    validate_max_campaign_budget,
    validate_max_daily_package_spend,
    validate_min_package_budget,
)
from src.core.validation_helpers import format_validation_error


def _requested_actions(req: UpdateMediaBuyRequest) -> list[str]:
    """Derive the AdCP buyer-action names implied by an update request.

    Returned names align with ``MEDIA_BUY_STATE_MACHINE`` keys so they can
    be intersected against ``valid_actions_for_status(current_status)``.
    """
    actions: list[str] = []
    if req.paused is True:
        actions.append("pause")
    if req.paused is False:
        actions.append("resume")
    if req.budget is not None:
        actions.append("update_budget")
    if req.start_time is not None or req.end_time is not None:
        actions.append("update_dates")
    if req.packages:
        actions.append("update_packages")
    return actions


def _verify_principal(media_buy_id: str, context: "ResolvedIdentity", repo: MediaBuyRepository) -> None:
    """Verify that the principal from context owns the media buy.

    Uses the provided repository for database access (no own session).

    Args:
        media_buy_id: Media buy ID to verify
        context: ResolvedIdentity with principal info
        repo: Tenant-scoped MediaBuyRepository for DB lookups

    Raises:
        AdCPAuthenticationError: Missing principal
        ValueError: Media buy not found
        PermissionError: Principal doesn't own media buy
    """
    principal_id: str | None = context.principal_id

    # CRITICAL: principal_id is required for media buy updates
    if not principal_id:
        raise AdCPAuthenticationError(
            "Authentication required: Missing or invalid x-adcp-auth header. Media buy updates require authentication."
        )

    # Tenant is resolved at the transport boundary (resolve_identity_from_context)
    tenant = context.tenant
    if not tenant:
        raise AdCPAuthenticationError("No tenant context available")

    # Query database for media buy by ID
    media_buy = repo.get_by_id(media_buy_id)

    if not media_buy:
        raise ValueError(f"Media buy '{media_buy_id}' not found.")

    if media_buy.principal_id != principal_id:
        # CRITICAL: Verify principal_id is set (security check, not assertion)
        # Using explicit check instead of assert because asserts are removed with python -O
        if not principal_id:
            raise AdCPAuthenticationError("Authentication required: principal_id not found in context")

        # Log security violation
        security_logger = get_audit_logger("AdCP", tenant["tenant_id"])
        security_logger.log_security_violation(
            operation="access_media_buy",
            principal_id=principal_id,
            resource_id=media_buy_id,
            reason=f"Principal does not own media buy (owner: {media_buy.principal_id})",
        )
        raise AdCPAuthorizationError(f"Principal '{principal_id}' does not own media buy '{media_buy_id}'.")


# Fields that are NOT considered "ignored extras" when cancel is also set.
# media_buy_id is the identifier; canceled and cancellation_reason are the
# cancel intent itself; context is the buyer correlation envelope.
_CANCEL_NEUTRAL_FIELDS: frozenset[str] = frozenset({"media_buy_id", "canceled", "cancellation_reason", "context"})


def _execute_cancel(
    *,
    req: UpdateMediaBuyRequest,
    media_buy: Any,
    identity: ResolvedIdentity,
    tenant: dict,
    principal_id: str,
    uow: "MediaBuyUoW",
    adapter: Any,
    today: date,
    ctx_manager: Any,
    step: Any,
    testing_ctx: AdCPTestContext,
) -> UpdateMediaBuySuccess | UpdateMediaBuyError:
    """Execute buyer-initiated media buy cancellation.

    Per AdCP spec, cancellation is irreversible and exclusive of every other
    update. Spec §292: when other fields accompany ``canceled=true``, ignore
    them and surface a warning in the response context. The terminal-status
    guard in ``_update_media_buy_impl`` already filtered out re-cancel of an
    already-canceled buy and updates of completed/rejected buys.

    Side effects on success: MediaBuy.status -> "canceled", cancellation
    metadata persisted; CreativeAssignment.released_at set; MediaPackage.is_paused
    cleared (cancel supersedes pause); adapter ad-server cancel invoked;
    delivery simulator stopped; workflow step completed.

    All DB writes ride the outer UoW transaction so an adapter rejection or
    later failure rolls back the buyer-visible state, matching adapter-side
    truth.

    NOTE: post-commit webhook delivery via ``uow.enqueue_webhook`` is plumbed
    on ``BaseUoW`` but not yet wired here. Buyers subscribed to
    ``push_notification_config`` on the cancel request do NOT currently
    receive a status-transition webhook on cancel — tracked as follow-up.
    """
    from adcp.types.generated_poc.core.media_buy import Cancellation
    from adcp.types.generated_poc.enums.canceled_by import CanceledBy
    from adcp.types.generated_poc.enums.media_buy_status import MediaBuyStatus

    assert uow.media_buys is not None
    assert uow.assignments is not None

    media_buy_id = req.media_buy_id or ""
    now = datetime.now(UTC)

    # Spec §292: collect any other set fields and surface as a context warning.
    # ContextObject is `extra='allow'` so we round-trip via model_dump to splice
    # a warnings list into metadata while preserving the buyer-supplied envelope.
    ignored_fields = sorted(req.model_fields_set - _CANCEL_NEUTRAL_FIELDS)
    response_context: ContextObject | None = req.context
    if ignored_fields:
        ignored_warning = {
            "code": "fields_ignored",
            "ignored_fields": ignored_fields,
            "message": "Cancellation supersedes concurrent updates; non-cancellation fields ignored.",
        }
        ctx_dict = response_context.model_dump(mode="json") if response_context is not None else {}
        metadata = dict(ctx_dict.get("metadata") or {})
        warnings_list = list(metadata.get("warnings", []))
        warnings_list.append(ignored_warning)
        metadata["warnings"] = warnings_list
        ctx_dict["metadata"] = metadata
        response_context = ContextObject(**ctx_dict)

    # Dry-run: synthesize a spec-shaped response without writes or adapter call.
    if testing_ctx.dry_run:
        logger.info(f"[DRY_RUN] Synthesizing cancel response for media_buy_id={media_buy_id}")
        packages = uow.media_buys.get_packages(media_buy_id)
        simulated_cancellation = Cancellation(
            canceled_at=now,
            canceled_by=CanceledBy.buyer,
            reason=req.cancellation_reason,
        )
        simulated_packages = [
            AffectedPackage(
                package_id=pkg.package_id,
                paused=False,
                canceled=True,
                cancellation=simulated_cancellation,
                buyer_package_ref=pkg.package_id,
                changes_applied={"dry_run": True, "canceled": True},
            )
            for pkg in sorted(packages, key=lambda p: p.package_id)
        ]
        return UpdateMediaBuySuccess(
            media_buy_id=media_buy_id,
            affected_packages=simulated_packages,
            status=MediaBuyStatus.canceled,
            valid_actions=[],
            context=response_context,
        )

    # Live cancel.
    adapter_today = datetime.combine(today, datetime.min.time(), tzinfo=UTC)
    result = adapter.update_media_buy(
        media_buy_id=media_buy_id,
        action="cancel_media_buy",
        package_id=None,
        budget=None,
        today=adapter_today,
        cancellation_reason=req.cancellation_reason,
    )

    if isinstance(result, UpdateMediaBuyError) and result.errors:
        adapter_code = (result.errors[0].code or "").upper()
        adapter_msg = result.errors[0].message or "Adapter rejected cancellation"
        if step is not None:
            ctx_manager.update_workflow_step(
                step.step_id,
                status="failed",
                response_data=result.model_dump(mode="json"),
                error_message=adapter_msg,
            )
        # Adapter-side cancel rejection. UNSUPPORTED_FEATURE keeps its semantic;
        # everything else maps to NOT_CANCELLABLE per spec. Return (don't raise)
        # so the response envelope echoes context.correlation_id back to the
        # buyer per the spec's UpdateMediaBuyResponse2 shape.
        if adapter_code == "UNSUPPORTED_FEATURE":
            return UpdateMediaBuyError(errors=result.errors, context=response_context)
        return UpdateMediaBuyError(
            errors=[
                Error(
                    code="NOT_CANCELLABLE",
                    message=adapter_msg,
                    recovery="correctable",
                    details={"adapter_code": adapter_code},
                )
            ],
            context=response_context,
        )

    # Persist cancellation metadata on MediaBuy and release assignments.
    uow.media_buys.cancel(
        media_buy_id,
        when=now,
        canceled_by=CanceledBy.buyer.value,
        reason=req.cancellation_reason,
    )
    released_count = uow.assignments.release_all_for_media_buy(media_buy_id, when=now)
    logger.info(f"Released {released_count} creative assignment(s) on cancel of {media_buy_id}")

    # Cancel supersedes pause: clear is_paused on every package.
    packages = uow.media_buys.get_packages(media_buy_id)
    for pkg in packages:
        if pkg.is_paused:
            uow.media_buys.update_package_fields(media_buy_id, pkg.package_id, is_paused=False)

    # Stop any in-flight delivery simulation. Idempotent — no-op if not running.
    try:
        from src.services.delivery_simulator import delivery_simulator

        delivery_simulator.stop_simulation(media_buy_id)
    except Exception as exc:  # noqa: BLE001 — simulator is best-effort
        logger.warning(f"Failed to stop delivery simulator for {media_buy_id}: {exc}")

    # Build response.
    cancellation_block = Cancellation(
        canceled_at=now,
        canceled_by=CanceledBy.buyer,
        reason=req.cancellation_reason,
    )
    affected_packages = [
        AffectedPackage(
            package_id=pkg.package_id,
            paused=False,
            canceled=True,
            cancellation=cancellation_block,
            buyer_package_ref=pkg.package_id,
            changes_applied={"canceled": True, "reason": req.cancellation_reason},
        )
        for pkg in sorted(packages, key=lambda p: p.package_id)
    ]
    response = UpdateMediaBuySuccess(
        media_buy_id=media_buy_id,
        affected_packages=affected_packages,
        status=MediaBuyStatus.canceled,
        valid_actions=valid_actions_for_status("canceled"),
        context=response_context,
    )

    # Audit (separate session — pre-existing pattern, not refactored here).
    audit_logger = get_audit_logger("AdCP", tenant["tenant_id"])
    audit_logger.log_operation(
        operation="update_media_buy",
        principal_name=principal_id,
        principal_id=principal_id,
        adapter_id="mcp_server",
        success=True,
        details={
            "media_buy_id": media_buy_id,
            "action": "cancel_media_buy",
            "reason": req.cancellation_reason,
            "ignored_fields": ignored_fields or None,
        },
    )

    if step is not None:
        ctx_manager.update_workflow_step(
            step.step_id,
            status="completed",
            response_data=response.model_dump(mode="json"),
        )

    return response


def _update_media_buy_impl(
    req: UpdateMediaBuyRequest,
    identity: ResolvedIdentity | None = None,
    context_id: str | None = None,
) -> UpdateMediaBuySuccess | UpdateMediaBuyError:
    """Shared implementation for update_media_buy (used by both MCP and A2A).

    Callers construct the validated UpdateMediaBuyRequest at their boundary
    (MCP wrapper from typed FastMCP params, A2A raw from dict params).

    Uses a single MediaBuyUoW for the entire operation — one session, one transaction.

    Args:
        req: Validated UpdateMediaBuyRequest with all protocol fields
        identity: ResolvedIdentity with principal/tenant info (transport-agnostic)
        context_id: Optional workflow context ID

    Returns:
        UpdateMediaBuyResponse with updated media buy details
    """
    # Initialize tracking for affected packages (internal tracking, not part of schema)
    affected_packages_list: list[AffectedPackage] = []

    if identity is None:
        raise ValueError("Identity is required for update_media_buy")

    # CRITICAL: Extract principal from identity
    principal_id = identity.principal_id
    if principal_id is None:
        raise ValueError("principal_id is required but was None - authentication required")

    # Tenant is resolved at the transport boundary (resolve_identity_from_context)
    tenant = identity.tenant
    if not tenant:
        raise AdCPAuthenticationError("No tenant context available")

    # Single UoW for entire update operation — one session, one transaction
    with MediaBuyUoW(tenant["tenant_id"]) as uow:
        assert uow.media_buys is not None
        # FIXME(salesagent-9f2): raw session usages below should migrate to repository methods
        assert uow.session is not None
        session = uow.session

        # media_buy_id is required by library base class
        media_buy_id_to_use = req.media_buy_id

        if not media_buy_id_to_use:
            raise ValueError("media_buy_id is required")

        # Verify principal owns this media buy
        _verify_principal(media_buy_id_to_use, identity, uow.media_buys)

        # Fetch the media buy with a row-level lock for the duration of the
        # transaction. This serializes concurrent updates (especially cancels)
        # under PostgreSQL READ COMMITTED so two simultaneous cancels can't
        # both succeed silently.
        media_buy_locked = uow.media_buys.get_by_id_for_update(media_buy_id_to_use)
        if media_buy_locked is None:
            # _verify_principal already validated existence; reaching here means
            # a TOCTOU race deleted the row between calls. Treat as not-found.
            raise AdCPValidationError(f"Media buy '{media_buy_id_to_use}' was not found at lock time")

        # State-machine precondition. Terminal states reject all mutations
        # per the SDK state machine; the cancel branch gets NOT_CANCELLABLE
        # precedence over INVALID_STATE per AdCP spec §128/§129 ("re-cancel
        # of a canceled buy MUST return NOT_CANCELLABLE; idempotent acceptance
        # is NOT conformant"). Other actions are validated against the
        # ``adcp.server.helpers.MEDIA_BUY_STATE_MACHINE`` matrix for the
        # current status.
        #
        # We RETURN UpdateMediaBuyError (not raise AdCPError) so the response
        # envelope matches the spec's UpdateMediaBuyResponse2 discriminated
        # union variant and the buyer's context.correlation_id is echoed.
        # Storyboard probes ``invalid_transitions/double_cancel.second_cancel``,
        # ``terminal_enforcement/{pause,resume,recancel}_buy`` all assert
        # ``field_value path: context.correlation_id`` on the error response.
        _current_status = media_buy_locked.status
        buyer_attempted_cancel = req.canceled_explicitly_set()
        if is_terminal_status(_current_status):
            if buyer_attempted_cancel and _current_status == "canceled":
                err = Error(
                    code="NOT_CANCELLABLE",
                    message=f"Media buy '{media_buy_id_to_use}' is already canceled",
                    recovery="correctable",
                    details={"current_status": _current_status},
                )
            else:
                err = Error(
                    code="INVALID_STATE",
                    message=f"Cannot update media buy in terminal state '{_current_status}'",
                    recovery="correctable",
                    details={"current_status": _current_status},
                )
            return UpdateMediaBuyError(errors=[err], context=req.context)

        # Non-terminal: validate requested actions against the per-status
        # allowed set from the SDK state machine. Cancel is always allowed
        # in non-terminal statuses (the matrix includes it) so this naturally
        # admits the cancel path; other actions (pause, resume, update_*) are
        # checked here before the buyer-action dispatch below.
        _requested = _requested_actions(req)
        _allowed = set(valid_actions_for_status(_current_status))
        if _allowed or _current_status in MEDIA_BUY_STATE_MACHINE:
            _disallowed = [a for a in _requested if a not in _allowed]
            if _requested and _disallowed:
                err = Error(
                    code="INVALID_STATE",
                    message=f"Action(s) {_disallowed} not allowed in status '{_current_status}'",
                    recovery="correctable",
                    details={
                        "current_status": _current_status,
                        "disallowed_actions": _disallowed,
                        "valid_actions": sorted(_allowed),
                    },
                )
                return UpdateMediaBuyError(errors=[err], context=req.context)

        # Extract testing context early (needed for dry_run check)
        testing_ctx = identity.testing_context if identity.testing_context else AdCPTestContext()

        # Create or get persistent context and workflow step
        # Skip for dry_run mode (no side effects, no database writes)
        ctx_manager = get_context_manager()
        ctx_id = context_id  # Extracted at transport boundary, passed in
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

            # Create workflow step for this tool call
            step = ctx_manager.create_workflow_step(
                context_id=persistent_ctx.context_id,  # Now safe to access
                step_type="tool_call",
                owner="principal",
                status="in_progress",
                tool_name="update_media_buy",
                request_data=req,
                request_metadata={"protocol": identity.protocol},
            )

        principal = get_principal_object(principal_id, tenant_id=identity.tenant_id)  # Now guaranteed to be str
        if not principal:
            error_msg = f"Principal {principal_id} not found"
            response_data = UpdateMediaBuyError(
                errors=[Error(code="AUTH_REQUIRED", message=error_msg)],
                context=req.context,
            )
            if step:
                ctx_manager.update_workflow_step(
                    step.step_id,
                    status="failed",
                    response_data=response_data.model_dump(mode="json"),
                    error_message=error_msg,
                )
            return response_data

        adapter = get_adapter(principal, dry_run=testing_ctx.dry_run, testing_context=testing_ctx, tenant=tenant)
        today = req.today or date.today()

        # Cancellation branch — exclusive with every other update path per
        # AdCP spec §292 (cancel + other fields => apply cancel, ignore others
        # except cancellation_reason, warn in response context).
        if buyer_attempted_cancel:
            return _execute_cancel(
                req=req,
                media_buy=media_buy_locked,
                identity=identity,
                tenant=tenant,
                principal_id=principal_id,
                uow=uow,
                adapter=adapter,
                today=today,
                ctx_manager=ctx_manager,
                step=step,
                testing_ctx=testing_ctx,
            )

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
                            package_id=pkg_update.package_id or "",
                            paused=pkg_update.paused if pkg_update.paused is not None else False,
                            buyer_package_ref=pkg_update.package_id,
                            changes_applied={"dry_run": True, "would_update": pkg_update},
                        )
                    )

            # Look up current status for valid_actions
            _dry_run_mb = uow.media_buys.get_by_id(req.media_buy_id)
            _dry_run_status = _dry_run_mb.status if _dry_run_mb else ""

            # Build simulated response
            dry_run_response = UpdateMediaBuySuccess(
                media_buy_id=req.media_buy_id or "",
                affected_packages=simulated_affected,
                valid_actions=valid_actions_for_status(_dry_run_status),
                context=req.context,
            )

            return dry_run_response

        # Type narrowing: after dry_run early return, step and persistent_ctx are guaranteed to exist
        assert step is not None, "step should be created when not in dry_run mode"
        assert persistent_ctx is not None, "persistent_ctx should be created when not in dry_run mode"

        # Check if manual approval is required
        manual_approval_required = adapter.manual_approval_required
        manual_approval_operations = adapter.manual_approval_operations

        if manual_approval_required and "update_media_buy" in manual_approval_operations:
            # Store the original request alongside the response so the approval
            # execution path can re-execute the update after human approval.
            # This mirrors create_media_buy's raw_request pattern.
            _approval_mb = uow.media_buys.get_by_id(req.media_buy_id)
            _approval_status = _approval_mb.status if _approval_mb else ""
            approval_response = UpdateMediaBuySuccess(
                media_buy_id=req.media_buy_id or "",
                affected_packages=[],  # Not yet applied — pending approval
                valid_actions=valid_actions_for_status(_approval_status),
                context=req.context,
            )
            approval_data = approval_response.model_dump(mode="json")
            approval_data["request_data"] = req.model_dump(mode="json")
            ctx_manager.update_workflow_step(
                step.step_id,
                status="requires_approval",
                response_data=approval_data,
                add_comment={
                    "user": "system",
                    "comment": "Publisher requires manual approval for all media buy updates",
                },
            )

            # Create ObjectWorkflowMapping so the admin approval flow can find
            # this update and execute it after human approval (#1041).
            from src.core.database.models import ObjectWorkflowMapping

            mapping = ObjectWorkflowMapping(
                step_id=step.step_id,
                object_type="media_buy",
                object_id=req.media_buy_id,
                action="update",
            )
            session.add(mapping)

            return approval_response

        # Validate currency limits if flight dates or budget changes
        # This prevents workarounds where buyers extend flight to bypass daily max
        if req.start_time or req.end_time or req.budget or (req.packages and any(pkg.budget for pkg in req.packages)):
            media_buy = uow.media_buys.get_by_id(req.media_buy_id)

            if media_buy:
                request_currency: str
                if req.budget:
                    if isinstance(req.budget, int | float):
                        request_currency = str(media_buy.currency) if media_buy.currency else "USD"
                    elif req.budget.currency:
                        request_currency = str(req.budget.currency)
                    else:
                        request_currency = str(media_buy.currency) if media_buy.currency else "USD"
                else:
                    request_currency = str(media_buy.currency) if media_buy.currency else "USD"

                assert uow.currency_limits is not None
                currency_limit = uow.currency_limits.get_for_currency(request_currency)

                if not currency_limit:
                    error_msg = f"Currency {request_currency} is not supported by this publisher."
                    response_data = UpdateMediaBuyError(
                        errors=[Error(code="UNSUPPORTED_FEATURE", message=error_msg)],
                        context=req.context,
                    )
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        response_data=response_data.model_dump(mode="json"),
                        error_message=error_msg,
                    )
                    return response_data

                start = req.start_time if req.start_time else media_buy.start_time
                end = req.end_time if req.end_time else media_buy.end_time

                from datetime import datetime as dt

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
                    start_dt = dt.now(UTC)

                if isinstance(end, str):
                    end_dt = dt.fromisoformat(end.replace("Z", "+00:00"))
                elif isinstance(end, datetime):
                    end_dt = end
                else:
                    end_dt = start_dt + timedelta(days=1)

                flight_days = (end_dt - start_dt).days
                if flight_days <= 0:
                    flight_days = 1

                if currency_limit.max_daily_package_spend and req.packages:
                    for pkg_update in req.packages:
                        if pkg_update.budget:
                            pkg_budget_amount: float
                            if isinstance(pkg_update.budget, int | float):
                                pkg_budget_amount = float(pkg_update.budget)
                            else:
                                pkg_budget_amount = float(pkg_update.budget.total)

                            package_daily_spend_error: str | None = validate_max_daily_package_spend(
                                package_budget=Decimal(str(pkg_budget_amount)),
                                flight_days=flight_days,
                                max_daily_spend=currency_limit.max_daily_package_spend,
                                currency=request_currency,
                            )
                            if package_daily_spend_error:
                                response_data = UpdateMediaBuyError(
                                    errors=[Error(code="BUDGET_EXCEEDED", message=package_daily_spend_error)],
                                    context=req.context,
                                )
                                ctx_manager.update_workflow_step(
                                    step.step_id,
                                    status="failed",
                                    response_data=response_data.model_dump(mode="json"),
                                    error_message=package_daily_spend_error,
                                )
                                return response_data

        # Handle campaign-level updates
        if req.paused is not None:
            # adcp 2.12.0+: paused=True means pause, paused=False means resume
            action = "pause_media_buy" if req.paused else "resume_media_buy"
            result = adapter.update_media_buy(
                media_buy_id=req.media_buy_id,
                action=action,
                package_id=None,
                budget=None,
                today=datetime.combine(today, datetime.min.time(), tzinfo=UTC),
            )
            # Manual approval case - convert adapter result to appropriate Success/Error
            # adcp v1.2.1 oneOf pattern: Check if result is Error variant (has errors field)
            if isinstance(result, UpdateMediaBuyError) and result.errors:
                error_response = UpdateMediaBuyError(errors=result.errors)
                ctx_manager.update_workflow_step(
                    step.step_id,
                    status="failed",
                    response_data=error_response.model_dump(mode="json"),
                    error_message=result.errors[0].message if result.errors else "Pause/resume failed",
                )
                return error_response
            else:
                # UpdateMediaBuySuccess extends adcp v1.2.1 with internal fields
                # Use getattr to safely access discriminated union fields
                media_buy_id = getattr(result, "media_buy_id", req.media_buy_id or "")
                affected_pkgs = getattr(result, "affected_packages", [])

                # Persist campaign-level pause state to DB so reads (delivery,
                # list, admin readiness) reflect it without re-querying the
                # ad server. Pre-existing decoupling: is_paused was never
                # written prior to this work — we persist before reading the
                # post-action status below so it reflects the new state.
                uow.media_buys.update_fields(req.media_buy_id, is_paused=bool(req.paused))

                # Derive post-action status from the DB rather than hardcoding,
                # so valid_actions reflects what the buyer can actually do next.
                # Fall back to the current state-machine target only if the DB
                # row is missing (e.g., adapter deleted it under us).
                _post_action_mb = uow.media_buys.get_by_id(req.media_buy_id)
                _post_action_status = (
                    _post_action_mb.status
                    if _post_action_mb and _post_action_mb.status
                    else ("paused" if req.paused else "active")
                )
                success_response = UpdateMediaBuySuccess(
                    media_buy_id=media_buy_id,
                    affected_packages=affected_pkgs,
                    valid_actions=valid_actions_for_status(_post_action_status),
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
                        "action": action,
                        "affected_packages_count": len(affected_pkgs),
                    },
                )
                ctx_manager.update_workflow_step(
                    step.step_id,
                    status="completed",
                    response_data=success_response.model_dump(mode="json"),
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
                        action=action,
                        package_id=pkg_update.package_id,
                        budget=None,
                        today=datetime.combine(today, datetime.min.time(), tzinfo=UTC),
                    )
                    # adcp v1.2.1 oneOf pattern: Check if result is Error variant
                    if isinstance(result, UpdateMediaBuyError) and result.errors:
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

                    # Persist per-package pause state to DB. Closes the same
                    # decoupling as the campaign-level fix above.
                    if pkg_update.package_id:
                        uow.media_buys.update_package_fields(
                            req.media_buy_id, pkg_update.package_id, is_paused=bool(pkg_update.paused)
                        )

                # Handle budget updates
                if pkg_update.budget is not None:
                    # Validate package_id is provided (required for budget updates)
                    if not pkg_update.package_id:
                        error_msg = "package_id is required when updating package budget"
                        response_data = UpdateMediaBuyError(
                            errors=[Error(code="VALIDATION_ERROR", message=error_msg)],
                            context=req.context,
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
                        # F-07: preserve existing DB currency rather than defaulting to USD
                        _existing_mb = uow.media_buys.get_by_id(req.media_buy_id)
                        currency = str(_existing_mb.currency) if _existing_mb and _existing_mb.currency else "USD"
                    else:
                        # Budget object with .total and .currency attributes
                        budget_amount = float(pkg_update.budget.total)
                        currency = str(pkg_update.budget.currency) if pkg_update.budget.currency else "USD"

                    assert uow.currency_limits is not None
                    _cl = uow.currency_limits.get_for_currency(currency)
                    if _cl and _cl.min_package_budget:
                        package_min_budget_error: str | None = validate_min_package_budget(
                            package_budget=Decimal(str(budget_amount)),
                            min_package_budget=Decimal(str(_cl.min_package_budget)),
                            currency=currency,
                        )
                        if package_min_budget_error:
                            response_data = UpdateMediaBuyError(
                                errors=[Error(code="BUDGET_TOO_LOW", message=package_min_budget_error)],
                                context=req.context,
                            )
                            ctx_manager.update_workflow_step(
                                step.step_id,
                                status="failed",
                                error_message=package_min_budget_error,
                            )
                            return response_data

                    result = adapter.update_media_buy(
                        media_buy_id=req.media_buy_id,
                        action="update_package_budget",
                        package_id=pkg_update.package_id,
                        budget=int(budget_amount),
                        today=datetime.combine(today, datetime.min.time(), tzinfo=UTC),
                    )
                    # adcp v1.2.1 oneOf pattern: Check if result is Error variant
                    if isinstance(result, UpdateMediaBuyError) and result.errors:
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
                            package_id=pkg_update.package_id,  # Required by AdCP (guaranteed str)
                            paused=False,  # Package not paused (active)
                            buyer_package_ref=pkg_update.package_id,  # Internal field (for backward compat)
                            changes_applied={
                                "budget": {"updated": budget_amount, "currency": currency}
                            },  # Internal field
                        )
                    )

                # Handle creative_ids updates (AdCP v2.2.0+)
                if pkg_update.creative_ids is not None:
                    # Validate package_id is provided
                    if not pkg_update.package_id:
                        error_msg = "package_id is required when updating creative_ids"
                        response_data = UpdateMediaBuyError(
                            errors=[Error(code="VALIDATION_ERROR", message=error_msg)],
                            context=req.context,
                        )
                        ctx_manager.update_workflow_step(
                            step.step_id,
                            status="failed",
                            response_data=response_data.model_dump(mode="json"),
                            error_message=error_msg,
                        )
                        return response_data

                    from src.core.database.models import Creative as DBCreative
                    from src.core.database.models import CreativeAssignment as DBAssignment

                    # Resolve media_buy_id
                    media_buy_obj = uow.media_buys.get_by_id(req.media_buy_id)

                    if not media_buy_obj:
                        error_msg = f"Media buy '{req.media_buy_id}' not found"
                        response_data = UpdateMediaBuyError(
                            errors=[Error(code="MEDIA_BUY_NOT_FOUND", message=error_msg)],
                            context=req.context,
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
                            errors=[Error(code="CREATIVE_REJECTED", message=error_msg)],
                            context=req.context,
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
                    from src.core.database.models import Product

                    db_package = uow.media_buys.get_package(actual_media_buy_id, pkg_update.package_id)

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
                        raise AdCPValidationError(
                            error_msg, details={"error_code": "INVALID_CREATIVES", "creative_errors": validation_errors}
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
                            principal_id=principal_id,
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

                    # Flush to persist assignment changes within the session
                    session.flush()

                    # Store results for affected_packages response
                    affected_packages_list.append(
                        AffectedPackage(
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
                if pkg_update.creatives:
                    # Validate package_id is provided
                    if not pkg_update.package_id:
                        error_msg = "package_id is required when uploading creatives"
                        response_data = UpdateMediaBuyError(
                            errors=[Error(code="VALIDATION_ERROR", message=error_msg)],
                            context=req.context,
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
                    sync_response = _sync_creatives_impl(
                        creatives=pkg_update.creatives,
                        assignments={
                            c.creative_id: [pkg_update.package_id] for c in pkg_update.creatives if c.creative_id
                        },
                        identity=identity,
                    )

                    # Check for sync errors
                    failed_creatives = [r for r in sync_response.creatives if r.action == CreativeAction.failed]
                    if failed_creatives:
                        error_msgs = [
                            f"{r.creative_id}: {', '.join(e.message for e in (r.errors or []))}"
                            for r in failed_creatives
                        ]
                        error_msg = f"Failed to sync creatives: {'; '.join(error_msgs)}"
                        response_data = UpdateMediaBuyError(
                            errors=[Error(code="SERVICE_UNAVAILABLE", message=error_msg)],
                            context=req.context,
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
                            errors=[Error(code="VALIDATION_ERROR", message=error_msg)],
                            context=req.context,
                        )
                        ctx_manager.update_workflow_step(
                            step.step_id,
                            status="failed",
                            response_data=response_data.model_dump(mode="json"),
                            error_message=error_msg,
                        )
                        return response_data

                    from src.core.database.models import CreativeAssignment as DBAssignment
                    from src.core.database.models import Product as ProductModel

                    # Resolve media_buy_id
                    media_buy_obj = uow.media_buys.get_by_id(req.media_buy_id)

                    if not media_buy_obj:
                        error_msg = f"Media buy '{req.media_buy_id}' not found"
                        response_data = UpdateMediaBuyError(
                            errors=[Error(code="MEDIA_BUY_NOT_FOUND", message=error_msg)],
                            context=req.context,
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
                        pkg_record = uow.media_buys.get_package(actual_media_buy_id, pkg_update.package_id)

                        if not pkg_record:
                            error_msg = (
                                f"Package '{pkg_update.package_id}' not found for media buy '{actual_media_buy_id}'"
                            )
                            response_data = UpdateMediaBuyError(
                                errors=[Error(code="PACKAGE_NOT_FOUND", message=error_msg)],
                                context=req.context,
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
                                        errors=[Error(code="VALIDATION_ERROR", message=error_msg)],
                                        context=req.context,
                                    )
                                    return response_data
                            elif product_obj and not product_obj.placements:
                                # Product doesn't define placements, so placement targeting not supported
                                error_msg = f"Product '{product_id}' does not support placement targeting (no placements defined)"
                                response_data = UpdateMediaBuyError(
                                    errors=[Error(code="UNSUPPORTED_FEATURE", message=error_msg)],
                                    context=req.context,
                                )
                                return response_data

                    updated_assignments = []
                    new_assignments_created = []

                    # BR-RULE-024 INV-2: creative_assignments replaces ALL existing
                    # assignments for this package. Delete existing assignments not
                    # in the new list, matching the creative_ids handler pattern.
                    requested_creative_ids = {ca.creative_id for ca in pkg_update.creative_assignments}
                    existing_stmt = select(DBAssignment).where(
                        DBAssignment.tenant_id == tenant["tenant_id"],
                        DBAssignment.media_buy_id == actual_media_buy_id,
                        DBAssignment.package_id == pkg_update.package_id,
                    )
                    existing_assignments = session.scalars(existing_stmt).all()
                    for existing in existing_assignments:
                        if existing.creative_id not in requested_creative_ids:
                            session.delete(existing)

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
                                principal_id=principal_id,
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

                    # Flush to persist assignment changes within the session
                    session.flush()

                    # Track in affected_packages
                    affected_packages_list.append(
                        AffectedPackage(
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
                            errors=[Error(code="VALIDATION_ERROR", message=error_msg)],
                        )
                        ctx_manager.update_workflow_step(
                            step.step_id,
                            status="failed",
                            response_data=response_data.model_dump(mode="json"),
                            error_message=error_msg,
                        )
                        return response_data

                    from sqlalchemy.orm import attributes

                    # Get the package via repository
                    media_package = uow.media_buys.get_package(req.media_buy_id, pkg_update.package_id)

                    if not media_package:
                        error_msg = f"Package {pkg_update.package_id} not found for media buy {req.media_buy_id}"
                        response_data = UpdateMediaBuyError(
                            errors=[Error(code="PACKAGE_NOT_FOUND", message=error_msg)],
                        )
                        ctx_manager.update_workflow_step(
                            step.step_id,
                            status="failed",
                            response_data=response_data.model_dump(mode="json"),
                            error_message=error_msg,
                        )
                        return response_data

                    # Store Targeting model directly — engine's pydantic_core.to_json serializer handles it
                    media_package.package_config["targeting_overlay"] = pkg_update.targeting_overlay
                    # Flag the JSON field as modified so SQLAlchemy persists it
                    attributes.flag_modified(media_package, "package_config")
                    session.flush()
                    logger.info(
                        f"[update_media_buy] Updated package {pkg_update.package_id} targeting: {pkg_update.targeting_overlay}"
                    )

                    # Track targeting update in affected_packages
                    affected_packages_list.append(
                        AffectedPackage(
                            package_id=pkg_update.package_id,
                            paused=False,  # Package not paused (active)
                            changes_applied={"targeting": pkg_update.targeting_overlay},
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
                # F-07: preserve existing DB currency rather than defaulting to USD
                _mb_for_currency = uow.media_buys.get_by_id(req.media_buy_id)
                budget_currency = (
                    str(_mb_for_currency.currency) if _mb_for_currency and _mb_for_currency.currency else "USD"
                )
            else:
                # Budget object with .total and .currency attributes
                total_budget = float(req.budget.total)
                budget_currency = str(req.budget.currency) if req.budget.currency else "USD"

            if total_budget <= 0:
                error_msg = f"Invalid budget: {total_budget}. Budget must be positive."
                response_data = UpdateMediaBuyError(
                    errors=[Error(code="VALIDATION_ERROR", message=error_msg)],
                    context=req.context,
                )
                ctx_manager.update_workflow_step(
                    step.step_id,
                    status="failed",
                    response_data=response_data.model_dump(mode="json"),
                    error_message=error_msg,
                )
                return response_data

            budget_error = validate_max_campaign_budget(
                campaign_budget=Decimal(str(total_budget)),
                max_campaign_budget=MAX_CAMPAIGN_BUDGET,
                currency=budget_currency,
            )
            if budget_error:
                response_data = UpdateMediaBuyError(
                    errors=[Error(code="BUDGET_EXCEEDED", message=budget_error)],
                    context=req.context,
                )
                ctx_manager.update_workflow_step(
                    step.step_id,
                    status="failed",
                    error_message=budget_error,
                )
                return response_data

            # TODO: Sync budget change to GAM order
            # Currently only updates database - does NOT sync to GAM API
            # This creates data inconsistency between our database and GAM
            # Need to implement: adapter.orders_manager.update_order_budget(order_id, total_budget)

            # Persist top-level budget update to database via repository
            if req.budget:
                uow.media_buys.update_fields(req.media_buy_id, budget=total_budget, currency=budget_currency)
                logger.warning(
                    f"Updated MediaBuy {req.media_buy_id} budget to {total_budget} {budget_currency} in database ONLY"
                )
                logger.warning("GAM sync NOT implemented - GAM still has old budget")

                # Track top-level budget update in affected_packages
                # When top-level budget changes, all packages are affected
                packages_result = uow.media_buys.get_packages(req.media_buy_id)

                for pkg in packages_result:
                    # MediaPackage uses package_id as primary identifier
                    package_ref = pkg.package_id if pkg.package_id else None
                    if package_ref:
                        # Type narrowing: package_ref is guaranteed to be str at this point
                        package_ref_str: str = package_ref
                        affected_packages_list.append(
                            AffectedPackage(
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

            update_values: dict[str, Any] = {}
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
                # Get existing media buy to check date range consistency
                existing_mb = uow.media_buys.get_by_id(req.media_buy_id)

                if not existing_mb:
                    error_msg = f"Media buy {req.media_buy_id} not found"
                    response_data = UpdateMediaBuyError(
                        errors=[Error(code="MEDIA_BUY_NOT_FOUND", message=error_msg)],
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
                        errors=[Error(code="VALIDATION_ERROR", message=error_msg)],
                    )
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        response_data=response_data.model_dump(mode="json"),
                        error_message=error_msg,
                    )
                    return response_data

                uow.media_buys.update_fields(req.media_buy_id, **update_values)
                logger.warning(
                    f"Updated MediaBuy {req.media_buy_id} dates in database ONLY: "
                    f"start_time={update_values.get('start_time')}, end_time={update_values.get('end_time')}"
                )
                logger.warning("GAM sync NOT implemented - GAM still has old dates")

        # Create ObjectWorkflowMapping to link media buy update to workflow step
        # This enables webhook delivery when the update completes
        from src.core.database.models import ObjectWorkflowMapping

        mapping = ObjectWorkflowMapping(
            step_id=step.step_id,
            object_type="media_buy",
            object_id=req.media_buy_id,
            action="update",
        )
        session.add(mapping)

        # Build final response first
        logger.info(f"[update_media_buy] Final affected_packages before return: {affected_packages_list}")

        # UpdateMediaBuySuccess extends adcp v1.2.1 with internal fields (workflow_step_id, affected_packages)
        # affected_packages_list contains AffectedPackage objects with both:
        # - AdCP-required fields (package_id) for spec compliance
        # - Internal tracking fields (buyer_package_ref, changes_applied) excluded via exclude=True

        _final_mb = uow.media_buys.get_by_id(req.media_buy_id)
        _final_status = _final_mb.status if _final_mb else ""
        final_response = UpdateMediaBuySuccess(
            media_buy_id=req.media_buy_id or "",
            affected_packages=affected_packages_list,
            valid_actions=valid_actions_for_status(_final_status),
            context=req.context,
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


def _build_update_request(
    media_buy_id: str | None = None,
    paused: bool | None = None,
    flight_start_date: str | None = None,
    flight_end_date: str | None = None,
    budget: float | None = None,
    currency: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    pacing: str | None = None,
    daily_budget: float | None = None,
    packages: list | None = None,
    push_notification_config: Any = None,
    context: Any = None,
    reporting_webhook: Any = None,
    ext: Any = None,
    canceled: bool | None = None,
    cancellation_reason: str | None = None,
) -> UpdateMediaBuyRequest:
    """Build UpdateMediaBuyRequest from flat parameters.

    Handles deprecated field mapping and budget object construction.
    Used by both MCP wrapper and A2A raw function.

    `canceled` is forwarded to the request only when explicitly True so that
    `model_fields_set` records buyer intent. Library default is True after
    construction, so passing False or None would leave intent ambiguous;
    we reject False at the boundary.
    """
    if canceled is False:
        raise AdCPValidationError("canceled must be true or omitted (cancellation is irreversible)")
    # Handle deprecated field names
    effective_start = start_time or flight_start_date
    effective_end = end_time or flight_end_date

    # Preserve bare float budgets when no extra budget metadata is provided.
    # This lets _impl reuse the existing media buy currency instead of forcing USD
    # at the transport boundary.
    budget_obj: Budget | float | None = None
    if budget is not None:
        if currency is None and pacing is None and daily_budget is None:
            budget_obj = float(budget)
        else:
            pacing_val: Literal["even", "asap", "daily_budget"] = "even"
            if pacing == "asap":
                pacing_val = "asap"
            elif pacing == "daily_budget":
                pacing_val = "daily_budget"
            budget_obj = Budget(
                total=budget,
                currency=currency or "USD",
                pacing=pacing_val,
                daily_cap=daily_budget,
                auto_pause_on_budget_exhaustion=None,
            )

    # Build request with only non-None values (strict validation in dev mode)
    request_params: dict[str, Any] = {}
    if media_buy_id is not None:
        request_params["media_buy_id"] = media_buy_id
    if paused is not None:
        request_params["paused"] = paused
    if effective_start is not None:
        request_params["start_time"] = effective_start
    if effective_end is not None:
        request_params["end_time"] = effective_end
    if budget_obj is not None:
        request_params["budget"] = budget_obj
    if packages is not None:
        request_params["packages"] = packages
    if push_notification_config is not None:
        request_params["push_notification_config"] = push_notification_config
    if context is not None:
        request_params["context"] = context
    if reporting_webhook is not None:
        request_params["reporting_webhook"] = reporting_webhook
    if ext is not None:
        request_params["ext"] = ext
    if canceled is True:
        request_params["canceled"] = True
    if cancellation_reason is not None:
        request_params["cancellation_reason"] = cancellation_reason

    try:
        req = UpdateMediaBuyRequest(**request_params)
    except ValidationError as e:
        raise AdCPValidationError(format_validation_error(e, context="update_media_buy request")) from e

    # BR-RULE-022: reject empty updates (no updatable fields beyond identifier)
    if not req.has_updatable_fields():
        raise AdCPValidationError(
            "Update request must include at least one updatable field "
            "(paused, canceled, cancellation_reason, start_time, end_time, packages, budget, "
            "push_notification_config, reporting_webhook, context, ext)"
        )

    return req


async def update_media_buy(
    media_buy_id: Annotated[str | None, Field(description="Publisher media buy ID to update")] = None,
    paused: Annotated[bool | None, Field(description="True to pause campaign delivery, False to resume")] = None,
    flight_start_date: Annotated[str | None, Field(description="New campaign start date in YYYY-MM-DD format")] = None,
    flight_end_date: Annotated[str | None, Field(description="New campaign end date in YYYY-MM-DD format")] = None,
    budget: Annotated[float | None, Field(description="New total campaign budget amount")] = None,
    currency: Annotated[str | None, Field(description="ISO 4217 currency code (e.g. 'USD')")] = None,
    targeting_overlay: TargetingOverlay | None = None,
    start_time: Annotated[str | None, Field(description="New campaign start time in ISO 8601 format")] = None,
    end_time: Annotated[str | None, Field(description="New campaign end time in ISO 8601 format")] = None,
    pacing: Annotated[str | None, Field(description="Budget pacing strategy: 'even' or 'asap'")] = None,
    daily_budget: Annotated[float | None, Field(description="Maximum daily spend cap")] = None,
    packages: list[UpdatePackage] | None = None,
    creatives: list = None,
    push_notification_config: PushNotificationConfig | None = None,
    context: ContextObject | None = None,  # payload-level context
    reporting_webhook: ReportingWebhook | None = None,  # AdCP ReportingWebhook
    ext: dict[str, Any] | None = None,  # AdCP ExtensionObject for custom fields
    canceled: Annotated[
        bool | None,
        Field(
            description=(
                "Set to true to irreversibly cancel the entire media buy. Cancellation is "
                "terminal: re-cancel returns NOT_CANCELLABLE; other updates on a canceled "
                "buy return INVALID_STATE."
            )
        ),
    ] = None,
    cancellation_reason: Annotated[
        str | None,
        Field(
            description="Human-readable reason for cancellation. Only valid with canceled=true.",
            max_length=500,
        ),
    ] = None,
    ctx: Context | ToolContext | None = None,
):
    """Update a media buy with campaign-level and/or package-level changes.

    MCP tool wrapper that delegates to the shared implementation.
    FastMCP automatically validates and coerces JSON inputs to Pydantic models.

    Args:
        media_buy_id: Media buy ID to update (required)
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
        context: Application-level context per adcp spec
        reporting_webhook: Webhook configuration for automated reporting delivery (optional, per AdCP spec)
        ext: Extension object for custom fields (optional, per AdCP spec)
        canceled: Set to true to irreversibly cancel the entire media buy (AdCP spec).
        cancellation_reason: Optional human-readable reason; only valid with canceled=true.
        ctx: FastMCP context (automatically provided)

    Returns:
        ToolResult with UpdateMediaBuyResponse data
    """
    # Construct spec-compliant request at the boundary — no model_dump needed
    # FastMCP already coerced JSON inputs to typed Pydantic models
    req = _build_update_request(
        media_buy_id=media_buy_id,
        paused=paused,
        flight_start_date=flight_start_date,
        flight_end_date=flight_end_date,
        budget=budget,
        currency=currency,
        start_time=start_time,
        end_time=end_time,
        pacing=pacing,
        daily_budget=daily_budget,
        packages=packages,
        push_notification_config=push_notification_config,
        context=context,
        reporting_webhook=reporting_webhook,
        ext=ext,
        canceled=canceled,
        cancellation_reason=cancellation_reason,
    )
    # Read identity and context_id pre-resolved by MCPAuthMiddleware
    identity = (await ctx.get_state("identity")) if isinstance(ctx, Context) else None
    _ctx_id = (await ctx.get_state("context_id")) if isinstance(ctx, Context) else None
    response = _update_media_buy_impl(req=req, identity=identity, context_id=_ctx_id)
    return ToolResult(content=str(response), structured_content=response)


def update_media_buy_raw(
    media_buy_id: str | None = None,
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
    reporting_webhook: ReportingWebhook | None = None,  # AdCP ReportingWebhook
    ext: dict[str, Any] | None = None,  # AdCP ExtensionObject for custom fields
    canceled: bool | None = None,
    cancellation_reason: str | None = None,
    ctx: Context | ToolContext | None = None,
    identity: ResolvedIdentity | None = None,
    context_id: str | None = None,
):
    """Update an existing media buy (raw function for A2A server use).

    Delegates to the shared implementation.

    Args:
        media_buy_id: The ID of the media buy to update (required)
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
        reporting_webhook: Webhook configuration for automated reporting delivery
        ext: Extension object for custom fields (optional, per AdCP spec)
        canceled: Set to true to irreversibly cancel the entire media buy (AdCP spec).
        cancellation_reason: Optional reason; only valid with canceled=true.
        ctx: Context for authentication (deprecated, use identity)
        identity: Pre-resolved identity (if available)
        context_id: Pre-resolved A2A context id (if available)

    Returns:
        UpdateMediaBuyResponse
    """
    req = _build_update_request(
        media_buy_id=media_buy_id,
        paused=paused,
        flight_start_date=flight_start_date,
        flight_end_date=flight_end_date,
        budget=budget,
        currency=currency,
        start_time=start_time,
        end_time=end_time,
        pacing=pacing,
        daily_budget=daily_budget,
        packages=packages,
        push_notification_config=push_notification_config,
        context=context,
        reporting_webhook=reporting_webhook,
        ext=ext,
        canceled=canceled,
        cancellation_reason=cancellation_reason,
    )
    if identity is None:
        from src.core.transport_helpers import resolve_identity_from_context

        identity = resolve_identity_from_context(ctx, require_valid_token=True)
    return _update_media_buy_impl(req=req, identity=identity, context_id=context_id)
