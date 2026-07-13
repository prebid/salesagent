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

from adcp.types import ContextObject, ReportingWebhook, TargetingOverlay
from adcp.types import PackageUpdate as UpdatePackage
from fastmcp.server.context import Context
from fastmcp.tools.tool import ToolResult
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from src.core.exceptions import (
    AdCPAdapterError,
    AdCPAuthorizationError,
    AdCPBudgetExceededError,
    AdCPBudgetTooLowError,
    AdCPCapabilityNotSupportedError,
    AdCPConflictError,
    AdCPContextNotFoundError,
    AdCPCreativeRejectedError,
    AdCPGoneError,
    AdCPInvalidRequestError,
    AdCPValidationError,
    media_buy_revision_conflict,
)

# PostgreSQL SQLSTATE for a lock_timeout expiry (lock_not_available). Expected
# contention, not a database outage — translated to a typed transient error and
# must not trip the DB circuit breaker. #1544.
LOCK_NOT_AVAILABLE = "55P03"
from src.core.tool_context import ToolContext

logger = logging.getLogger(__name__)

from src.core.audit_logger import get_audit_logger
from src.core.auth import (
    require_identity,
    require_principal_id,
    require_tenant,
    resolve_principal_or_raise,
)
from src.core.context_manager import get_context_manager
from src.core.database.models import (
    Creative as DBCreative,
)
from src.core.database.models import (
    CreativeAssignment as DBAssignment,
)
from src.core.database.models import (
    MediaBuy,
    ObjectWorkflowMapping,
)
from src.core.database.models import (
    Product as DBProduct,
)
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
from src.core.tools.creatives import _sync_creatives_impl
from src.core.tools.financial_validation import (
    raise_if_validation_failed,
    validate_max_campaign_budget,
    validate_max_daily_package_spend,
    validate_min_package_budget,
)
from src.core.transport_helpers import resolve_identity_from_context
from src.core.utils import utc_flight_start
from src.core.validation_helpers import format_validation_error, package_field_path
from src.services.targeting_capabilities import (
    property_list_unsupported_advisories,
    raise_if_property_targeting_violations,
    validate_geo_overlap,
    validate_overlay_targeting,
    validate_property_targeting_allowed,
    validate_unknown_targeting_fields,
)


def _require_current_buy(mb: MediaBuy | None) -> MediaBuy:
    """The post-mutation buy re-read from the DB, guaranteed present.

    Every update response site runs after ``_verify_principal`` has already
    raised MEDIA_BUY_NOT_FOUND for a missing buy, so a ``None`` here is an
    internal invariant violation — we raise rather than fabricate a fallback.
    Callers read ``revision`` and ``status`` off the returned (non-optional)
    row: a defaulted revision could report a token *regression* (a later read
    returning a lower value than an earlier one), which an optimistic-concurrency
    counter must never do, and a defaulted ``""`` status would advertise a
    graceful path that does not exist.
    """
    if mb is None:
        raise RuntimeError("update response built with no media buy — the buy must exist post-mutation")
    return mb


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


def _verify_principal(
    media_buy_id: str,
    identity: "ResolvedIdentity",
    repo: MediaBuyRepository,
    *,
    context: ContextObject | None = None,
) -> None:
    """Verify that the principal from identity owns the media buy.

    Uses the provided repository for database access (no own session).

    Args:
        media_buy_id: Media buy ID to verify
        identity: ResolvedIdentity with principal info
        repo: Tenant-scoped MediaBuyRepository for DB lookups

    Raises:
        AdCPAuthenticationError: Missing principal
        ValueError: Media buy not found
        PermissionError: Principal doesn't own media buy
    """
    principal_id = require_principal_id(identity, context=context)

    # Tenant is resolved at the transport boundary (resolve_identity_from_context)
    tenant = require_tenant(identity, context=context)

    # Fetch the media buy (raises AdCPMediaBuyNotFoundError if absent)
    media_buy = repo.get_by_id_or_raise(media_buy_id, context=context)

    if media_buy.principal_id != principal_id:
        # Log security violation
        security_logger = get_audit_logger("AdCP", tenant["tenant_id"])
        security_logger.log_security_violation(
            operation="access_media_buy",
            principal_id=principal_id,
            resource_id=media_buy_id,
            reason=f"Principal does not own media buy (owner: {media_buy.principal_id})",
        )
        raise AdCPAuthorizationError(f"Principal '{principal_id}' does not own media buy '{media_buy_id}'.")


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

    identity = require_identity(identity, context=req.context)

    principal_id = require_principal_id(identity, context=req.context)

    # Tenant is resolved at the transport boundary (resolve_identity_from_context)
    tenant = require_tenant(identity, context=req.context)

    # ── Workflow-step bookkeeping fence ──────────────────────────────────
    # Hoist ``ctx_manager`` and ``step`` out of the try below so the
    # AdCPError / Exception handlers at the function end can mark the step
    # as ``failed``. Without this, a raise from any validation site
    # (property_targeting, geo, budget, …) leaves the workflow step
    # orphaned in ``in_progress`` forever, which suppresses the
    # buyer-facing ``status="failed"`` push notification fired from
    # ``context_manager.update_workflow_step:330-332``.
    # Mirrors ``media_buy_create.py:3688-3697`` exactly.
    ctx_manager = get_context_manager()
    step = None

    with ctx_manager.audit_workflow_step_failure_ctx(lambda: step):
        # Single UoW for entire update operation — one session, one transaction
        with MediaBuyUoW(tenant["tenant_id"]) as uow:
            assert uow.media_buys is not None
            # FIXME(salesagent-9f2): raw session usages below should migrate to repository methods
            assert uow.session is not None
            session = uow.session

            # media_buy_id is required by library base class
            media_buy_id_to_use = req.media_buy_id

            if not media_buy_id_to_use:
                raise AdCPValidationError("media_buy_id is required")

            # Verify principal owns this media buy
            _verify_principal(media_buy_id_to_use, identity, uow.media_buys, context=req.context)

            # State-machine precondition: terminal states reject all mutations,
            # and non-terminal states only accept actions in their valid set.
            # ``AdCPGoneError`` carries the spec-mandated ``INVALID_STATE`` code
            # for both terminal states and disallowed actions — see
            # ``adcp.server.helpers.MEDIA_BUY_STATE_MACHINE`` for the source of truth.
            # Optimistic-concurrency gate — AdCP 3.1.0-beta.3
            # update-media-buy-request.json properties.revision: "When
            # provided, sellers MUST reject the update with CONFLICT if the
            # media buy's current revision does not match." (Schema-optional
            # field; graded by T-UC-003-partition-revision /
            # boundary-revision; no conformance storyboard step — ungraded.)
            # Acquire the authoritative row lock before any workflow or adapter
            # side effect. The lock is held by this UoW until commit, so two
            # same-token requests cannot both reach the adapter.
            #
            # Pessimistic-lock tradeoff: lock_timeout bounds a contended WAITER —
            # a second same-token request trying to ACQUIRE the lock fails fast
            # (retryable) after 5s instead of blocking to the global
            # statement_timeout. SET LOCAL scopes it to this transaction only.
            # It does NOT bound the lock HOLDER: while the adapter network call
            # below runs, this backend holds the row lock, and a DB-session
            # timeout cannot safely cap that — terminating the session mid-call
            # would release the lock while the external side effect is still in
            # flight (split-brain). Bounding the holder needs bounded/idempotent
            # adapter execution (reservation/outbox/CAS), tracked separately. The
            # expected lock_timeout error (SQLSTATE 55P03) is translated to a
            # typed transient error below and must NOT trip the DB circuit
            # breaker. #1544.
            try:
                session.execute(text("SET LOCAL lock_timeout = '5s'"))
                _current_mb = uow.media_buys.get_by_id(media_buy_id_to_use, for_update=True, populate_existing=True)
            except OperationalError as exc:
                if getattr(getattr(exc, "orig", None), "pgcode", None) != LOCK_NOT_AVAILABLE:
                    raise
                raise AdCPConflictError(
                    f"Media buy '{req.media_buy_id}' is being modified by another request; retry shortly.",
                    field="media_buy_id",
                    suggestion="Another update holds the row lock. Re-read the media buy and retry.",
                    recovery="transient",
                    context=req.context,
                ) from exc
            _current_status = _current_mb.status if _current_mb else ""
            # Precedence: the revision-conflict gate runs BEFORE the terminal-state
            # gate. Two separate things:
            #   * Spec fact — the pinned update-media-buy-request schema
            #     ``properties.revision`` says sellers MUST reject an update with
            #     CONFLICT when the supplied revision does not match.
            #   * Project decision — the spec does NOT specify precedence when a buy
            #     is BOTH stale-revision AND terminal. We run the CONFLICT check
            #     first so the buyer gets the refetch-and-retry recovery CONFLICT
            #     signals (a GONE buyer stops; a CONFLICT buyer re-reads, sees the
            #     terminal state, and stops for the right reason) rather than masking
            #     the stale write as INVALID_STATE. #1544.
            if req.revision is not None and _current_mb is not None:
                _persisted_revision = _current_mb.revision
                if req.revision != _persisted_revision:
                    raise media_buy_revision_conflict(
                        req.media_buy_id, expected=req.revision, current=_persisted_revision, context=req.context
                    )
            if is_terminal_status(_current_status):
                raise AdCPGoneError(
                    f"Cannot update media buy in terminal state: {_current_status}",
                    field="media_buy_id",
                    suggestion=(
                        f"Media buy is {_current_status} and cannot be modified. "
                        f"Create a new media buy to run a new campaign."
                    ),
                )

            _requested = _requested_actions(req)
            _allowed = set(valid_actions_for_status(_current_status))
            # Only enforce state machine for statuses defined in the spec.
            # Pre-confirmation internal states (e.g., "draft") are not in the
            # SDK state machine — allow all actions on those.
            if _allowed or _current_status in MEDIA_BUY_STATE_MACHINE:
                _disallowed = [a for a in _requested if a not in _allowed]
                if _requested and _disallowed:
                    raise AdCPGoneError(
                        f"Action(s) {_disallowed} not allowed in status '{_current_status}'",
                        field="media_buy_id",
                        suggestion=(f"Valid actions for status '{_current_status}': {sorted(_allowed) or '[]'}."),
                    )

            # Extract testing context early (needed for dry_run check)
            testing_ctx = identity.testing_context if identity.testing_context else AdCPTestContext()

            # Create or get persistent context and workflow step
            # (ctx_manager + step were hoisted before the try block so the
            # AdCPError / Exception handlers can mark the step as failed)
            ctx_id = context_id  # Extracted at transport boundary, passed in
            persistent_ctx = None

            if not testing_ctx.dry_run:
                persistent_ctx = ctx_manager.get_or_create_context(
                    tenant_id=tenant["tenant_id"],
                    principal_id=principal_id,  # Now guaranteed to be str
                    context_id=ctx_id,
                    is_async=True,
                )

                # Verify persistent_ctx is not None. In the async path this is
                # only None when a buyer-supplied context_id does not resolve —
                # a not-found condition, not a transient adapter outage.
                if persistent_ctx is None:
                    raise AdCPContextNotFoundError(
                        f"Context not found: {ctx_id}", field="context_id", context=req.context
                    )

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

            principal = resolve_principal_or_raise(principal_id, tenant_id=identity.tenant_id, context=req.context)

            adapter = get_adapter(principal, dry_run=testing_ctx.dry_run, testing_context=testing_ctx, tenant=tenant)
            today = req.today or date.today()

            # AdCP 3.0.0 spec (core/product.json `property_targeting_allowed`): reject property_list targeting
            # on products with property_targeting_allowed=False. Runs before the dry_run
            # early return so dry_run requests are also rejected (parity with create).
            # Raise shape is shared with create via ``raise_if_property_targeting_violations``
            # so both paths emit byte-identical error envelopes (same code, same field,
            # same details). The boundary's AdCPError handler updates any in-flight
            # workflow step to status="failed" for the audit trail.
            if req.packages:
                assert uow.products is not None, "MediaBuyUoW.products required for product targeting validation"
                # Run the same per-package targeting validators the create path runs, so a buyer
                # can't bypass unknown-field rejection, managed-only dimension checks, or
                # same-value geo inclusion/exclusion overlap by sending changes through update.
                overlay_violations: list[str] = []
                for pkg_update in req.packages:
                    if pkg_update.targeting_overlay is None:
                        continue
                    overlay_violations.extend(validate_unknown_targeting_fields(pkg_update.targeting_overlay))
                    overlay_violations.extend(validate_overlay_targeting(pkg_update.targeting_overlay))
                    overlay_violations.extend(validate_geo_overlap(pkg_update.targeting_overlay))
                if overlay_violations:
                    raise AdCPValidationError(f"Targeting validation failed: {'; '.join(overlay_violations)}")

                property_targeting_violations: list[str] = []
                for pkg_update in req.packages:
                    if (
                        pkg_update.targeting_overlay is None
                        or pkg_update.targeting_overlay.property_list is None
                        or not pkg_update.package_id
                    ):
                        continue
                    media_package = uow.media_buys.get_package(req.media_buy_id, pkg_update.package_id)
                    if media_package is None:
                        continue
                    package_product_id = (media_package.package_config or {}).get("product_id")
                    if not package_product_id:
                        continue
                    product = uow.products.get_by_id(package_product_id)
                    violation = validate_property_targeting_allowed(product, pkg_update.targeting_overlay)
                    if violation:
                        property_targeting_violations.append(violation)
                raise_if_property_targeting_violations(property_targeting_violations)

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
                _dry_run_mb = _require_current_buy(uow.media_buys.get_by_id(req.media_buy_id))

                # Build simulated response
                dry_run_response = UpdateMediaBuySuccess(
                    media_buy_id=req.media_buy_id or "",
                    # dry-run: nothing persisted — echo the current revision
                    revision=_dry_run_mb.revision,
                    affected_packages=simulated_affected,
                    valid_actions=valid_actions_for_status(_dry_run_mb.status),
                    context=req.context,
                    errors=property_list_unsupported_advisories(req.packages, adapter),
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
                _approval_mb = _require_current_buy(uow.media_buys.get_by_id(req.media_buy_id))
                approval_response = UpdateMediaBuySuccess(
                    media_buy_id=req.media_buy_id or "",
                    # Nothing applied yet (pending approval) — current persisted revision.
                    revision=_approval_mb.revision,
                    affected_packages=[],  # Not yet applied — pending approval
                    valid_actions=valid_actions_for_status(_approval_mb.status),
                    context=req.context,
                    errors=property_list_unsupported_advisories(req.packages, adapter),
                )
                ctx_manager.audit_workflow_step_result(
                    step.step_id,
                    approval_response,
                    status="requires_approval",
                    request_obj=req,
                    add_comment={
                        "user": "system",
                        "comment": "Publisher requires manual approval for all media buy updates",
                    },
                )

                # Create ObjectWorkflowMapping so the admin approval flow can
                # find this update and execute it after human approval.
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
            if (
                req.start_time
                or req.end_time
                or req.budget
                or (req.packages and any(pkg.budget for pkg in req.packages))
            ):
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
                        raise AdCPCapabilityNotSupportedError(
                            f"Currency {request_currency} is not supported by this publisher.",
                            context=req.context,
                        )

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
                                raise_if_validation_failed(
                                    package_daily_spend_error,
                                    exc_type=AdCPBudgetExceededError,
                                    context=req.context,
                                )

            # Handle campaign-level updates
            if req.paused is not None:
                # adcp 2.12.0+: paused=True means pause, paused=False means resume
                action = "pause_media_buy" if req.paused else "resume_media_buy"
                result = adapter.update_media_buy(
                    media_buy_id=req.media_buy_id,
                    action=action,
                    package_id=None,
                    budget=None,
                    today=utc_flight_start(today),
                )
                # Manual approval case - convert adapter result to appropriate Success/Error
                # adcp v1.2.1 oneOf pattern: Check if result is Error variant (has errors field)
                if isinstance(result, UpdateMediaBuyError) and result.errors:
                    error_response = UpdateMediaBuyError(errors=result.errors)
                    ctx_manager.audit_workflow_step_result(
                        step.step_id,
                        error_response,
                        status="failed",
                        error_message=result.errors[0].message if result.errors else "Pause/resume failed",
                    )
                    return error_response
                else:
                    # UpdateMediaBuySuccess extends adcp v1.2.1 with internal fields
                    # Use getattr to safely access discriminated union fields
                    media_buy_id = getattr(result, "media_buy_id", req.media_buy_id or "")
                    affected_pkgs = getattr(result, "affected_packages", [])

                    # A successful pause/resume is a mutation — bump the
                    # persisted revision so the token advances (parity with the
                    # package-level pause path, which already bumps).
                    # bump_revision_or_raise loads FOR UPDATE, bumps, and raises
                    # on a vanished row (a missing buy here is an internal
                    # invariant violation — _verify_principal already proved it
                    # exists); derive the post-action status from that same row
                    # so valid_actions reflects what the buyer can do next.
                    _post_action_mb = uow.media_buys.bump_revision_or_raise(
                        req.media_buy_id, expected_revision=req.revision, context=req.context
                    )
                    success_response = UpdateMediaBuySuccess(
                        media_buy_id=media_buy_id,
                        # Current persisted revision for this buy. bump_revision_or_raise
                        # returns the non-optional locked row, so read it directly.
                        revision=_post_action_mb.revision,
                        affected_packages=affected_pkgs,
                        valid_actions=valid_actions_for_status(_post_action_mb.status),
                        errors=property_list_unsupported_advisories(req.packages, adapter),
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
                    ctx_manager.audit_workflow_step_result(step.step_id, success_response)
                    return success_response

            # Every column mutation from this update is staged here and applied
            # with a SINGLE update_fields() call at the end of the flow, so one
            # accepted update bumps the persisted revision exactly once (AdCP 3.1.0-beta.3
            # revision is a per-resource version token). Intra-update status
            # transitions (draft → pending_creatives on creative assignment)
            # stage into this dict rather than writing ``.status`` directly —
            # see #1544 and the guard test_architecture_media_buy_status_writes.
            pending_field_updates: dict[str, Any] = {}

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
                            today=utc_flight_start(today),
                        )
                        # adcp v1.2.1 oneOf pattern: Check if result is Error variant
                        if isinstance(result, UpdateMediaBuyError) and result.errors:
                            error_message = (
                                result.errors[0].message
                                if (result.errors and len(result.errors) > 0)
                                else "Update failed"
                            )
                            response_data = UpdateMediaBuyError(errors=result.errors)
                            ctx_manager.audit_workflow_step_result(
                                step.step_id,
                                response_data,
                                status="failed",
                                error_message=error_message,
                            )
                            return response_data

                    # Handle budget updates
                    if pkg_update.budget is not None:
                        # Validate package_id is provided (required for budget updates)
                        if not pkg_update.package_id:
                            raise AdCPValidationError(
                                "package_id is required when updating package budget",
                                field=package_field_path("package_id"),
                                context=req.context,
                            )

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
                            raise_if_validation_failed(
                                package_min_budget_error,
                                exc_type=AdCPBudgetTooLowError,
                                context=req.context,
                            )

                        result = adapter.update_media_buy(
                            media_buy_id=req.media_buy_id,
                            action="update_package_budget",
                            package_id=pkg_update.package_id,
                            budget=int(budget_amount),
                            today=utc_flight_start(today),
                        )
                        # adcp v1.2.1 oneOf pattern: Check if result is Error variant
                        if isinstance(result, UpdateMediaBuyError) and result.errors:
                            error_message = (
                                result.errors[0].message
                                if (result.errors and len(result.errors) > 0)
                                else "Update failed"
                            )
                            response_data = UpdateMediaBuyError(errors=result.errors)
                            ctx_manager.audit_workflow_step_result(
                                step.step_id,
                                response_data,
                                status="failed",
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
                            raise AdCPValidationError(
                                "package_id is required when updating creative_ids",
                                field=package_field_path("package_id"),
                                context=req.context,
                            )

                        # Resolve media_buy_id
                        media_buy_obj = uow.media_buys.get_by_id_or_raise(req.media_buy_id, context=req.context)

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
                            raise AdCPCreativeRejectedError(
                                f"Creative IDs not found: {', '.join(missing_ids)}", context=req.context
                            )

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
                        db_package = uow.media_buys.get_package(actual_media_buy_id, pkg_update.package_id)

                        # Get product_id from package_config
                        product_id = (
                            db_package.package_config.get("product_id")
                            if db_package and db_package.package_config
                            else None
                        )

                        if product_id:
                            # Get product to check supported formats
                            product_stmt = select(DBProduct).where(
                                DBProduct.tenant_id == tenant["tenant_id"], DBProduct.product_id == product_id
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
                                error_msg,
                                details={"creative_errors": validation_errors},
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
                            pending_field_updates["status"] = "pending_creatives"
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
                            raise AdCPValidationError(
                                "package_id is required when uploading creatives",
                                field=package_field_path("package_id"),
                                context=req.context,
                            )

                        # Sync creatives (upload/update)
                        sync_response = _sync_creatives_impl(
                            creatives=pkg_update.creatives,
                            assignments={
                                c.creative_id: [pkg_update.package_id] for c in pkg_update.creatives if c.creative_id
                            },
                            identity=identity,
                        )

                        # Check for sync errors
                        failed_creatives = [r for r in sync_response.creatives if r.action == "failed"]
                        if failed_creatives:
                            error_msgs = [
                                f"{r.creative_id}: {', '.join(e.message for e in (r.errors or []))}"
                                for r in failed_creatives
                            ]
                            raise AdCPAdapterError(
                                f"Failed to sync creatives: {'; '.join(error_msgs)}", context=req.context
                            )

                        # Track in affected_packages
                        synced_ids = [
                            r.creative_id for r in sync_response.creatives if r.action in ["created", "updated"]
                        ]
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
                            raise AdCPValidationError(
                                "package_id is required when updating creative_assignments",
                                field=package_field_path("package_id"),
                                context=req.context,
                            )

                        # Resolve media_buy_id
                        media_buy_obj = uow.media_buys.get_by_id_or_raise(req.media_buy_id, context=req.context)

                        actual_media_buy_id = media_buy_obj.media_buy_id

                        # Validate placement_ids against product's available placements (adcp#208)
                        # Build set of placement_ids from all creative_assignments
                        all_requested_placement_ids: set[str] = set()
                        for ca in pkg_update.creative_assignments:
                            if ca.placement_ids:
                                all_requested_placement_ids.update(ca.placement_ids)

                        if all_requested_placement_ids:
                            # Get package to find product_id
                            pkg_record = uow.media_buys.get_package_or_raise(
                                actual_media_buy_id, pkg_update.package_id, context=req.context
                            )

                            product_id = (
                                pkg_record.package_config.get("product_id") if pkg_record.package_config else None
                            )

                            if product_id:
                                # Get product's placements
                                prod_stmt = select(DBProduct).where(
                                    DBProduct.tenant_id == tenant["tenant_id"],
                                    DBProduct.product_id == product_id,
                                )
                                product_obj = session.scalars(prod_stmt).first()

                                if product_obj and product_obj.placements:
                                    available_placement_ids: set[str] = {
                                        str(p.get("placement_id"))
                                        for p in product_obj.placements
                                        if p.get("placement_id")
                                    }
                                    invalid_ids = all_requested_placement_ids - available_placement_ids
                                    if invalid_ids:
                                        raise AdCPValidationError(
                                            f"Invalid placement_ids: {sorted(invalid_ids)}. "
                                            f"Available: {sorted(available_placement_ids)}",
                                            field="creative_assignments[].placement_ids",
                                            context=req.context,
                                        )
                                elif product_obj and not product_obj.placements:
                                    # Product doesn't define placements, so placement targeting not supported
                                    raise AdCPCapabilityNotSupportedError(
                                        f"Product '{product_id}' does not support placement targeting "
                                        f"(no placements defined)",
                                        context=req.context,
                                    )

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
                            pending_field_updates["status"] = "pending_creatives"
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
                            raise AdCPValidationError(
                                "package_id is required when updating targeting_overlay",
                                field=package_field_path("package_id"),
                                context=req.context,
                            )

                        from sqlalchemy.orm import attributes

                        # Get the package via repository
                        media_package = uow.media_buys.get_package_or_raise(
                            req.media_buy_id, pkg_update.package_id, context=req.context
                        )

                        # property_targeting_allowed validation runs earlier (before dry_run gate);
                        # by this point the request is known-valid against that rule.

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

            # Package-level changes above persist directly on the session
            # (package_config writes, creative assignment rows, adapter
            # pause/resume) and never pass through update_fields — track whether
            # any occurred so the single bump below also covers a package-only
            # update (pending_field_updates is staged from the top of the flow).
            package_level_changed = bool(
                req.packages and (affected_packages_list or any(pkg.paused is not None for pkg in req.packages))
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
                    raise AdCPValidationError(
                        f"Invalid budget: {total_budget}. Budget must be positive.",
                        field="budget",
                        context=req.context,
                    )

                budget_error = validate_max_campaign_budget(
                    campaign_budget=Decimal(str(total_budget)),
                    max_campaign_budget=MAX_CAMPAIGN_BUDGET,
                    currency=budget_currency,
                )
                raise_if_validation_failed(budget_error, exc_type=AdCPBudgetExceededError, context=req.context)

                # TODO: Sync budget change to GAM order
                # Currently only updates database - does NOT sync to GAM API
                # This creates data inconsistency between our database and GAM
                # Need to implement: adapter.orders_manager.update_order_budget(order_id, total_budget)

                # Stage the top-level budget update; persisted with one bump below.
                if req.budget:
                    pending_field_updates["budget"] = total_budget
                    pending_field_updates["currency"] = budget_currency
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
                    existing_mb = uow.media_buys.get_by_id_or_raise(req.media_buy_id, context=req.context)

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
                        final_end_time = (
                            end_val if isinstance(end_val, datetime) else datetime.fromisoformat(str(end_val))
                        )

                    if final_start_time and final_end_time and final_end_time <= final_start_time:
                        raise AdCPValidationError(
                            f"Invalid date range: end_time ({final_end_time.isoformat()}) "
                            f"must be after start_time ({final_start_time.isoformat()})",
                            field="end_time",
                            context=req.context,
                        )

                    pending_field_updates.update(update_values)
                    logger.warning(
                        f"Updated MediaBuy {req.media_buy_id} dates in database ONLY: "
                        f"start_time={update_values.get('start_time')}, end_time={update_values.get('end_time')}"
                    )
                    logger.warning("GAM sync NOT implemented - GAM still has old dates")

            # Apply every accumulated column update in a single revision bump.
            # update_fields() bumps once and covers any concurrent package-level
            # change; if ONLY package-level changes occurred (no column updates),
            # bump once directly. Exactly one increment per accepted update — see
            # #1544.
            if pending_field_updates:
                uow.media_buys.update_fields_or_raise(
                    req.media_buy_id, expected_revision=req.revision, context=req.context, **pending_field_updates
                )
            elif package_level_changed:
                uow.media_buys.bump_revision_or_raise(
                    req.media_buy_id, expected_revision=req.revision, context=req.context
                )

            # Create ObjectWorkflowMapping to link media buy update to workflow step
            # This enables webhook delivery when the update completes
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

            _final_mb = _require_current_buy(uow.media_buys.get_by_id(req.media_buy_id))
            final_response = UpdateMediaBuySuccess(
                media_buy_id=req.media_buy_id or "",
                # Persisted revision after this update's mutations (bumped by
                # update_fields / bump_revision above).
                revision=_final_mb.revision,
                affected_packages=affected_packages_list,
                valid_actions=valid_actions_for_status(_final_mb.status),
                context=req.context,
                errors=property_list_unsupported_advisories(req.packages, adapter),
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
            ctx_manager.audit_workflow_step_result(step.step_id, final_response)

        return final_response


def invalid_update_request_error(e: ValidationError) -> AdCPInvalidRequestError:
    """Translate a schema-level update-request rejection to the wire error.

    Single definition of the boundary behavior for a request that violates the
    AdCP 3.1.0-beta.3 update-media-buy-request schema (e.g. ``revision``
    below its ``minimum: 1``): INVALID_REQUEST, matching the REST boundary's
    RequestValidationError handler (src/app.py) so every transport emits one
    code for the same violation. The corrective ``suggestion`` rides its own
    top-level envelope key; in-process callers read it off the typed error.
    """
    return AdCPInvalidRequestError(
        format_validation_error(e, context="update_media_buy request"),
        suggestion="Correct the request to satisfy the update-media-buy-request schema and retry.",
    )


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
    idempotency_key: Annotated[str | None, Field(description="Idempotency key for retry safety")] = None,
    revision: int | None = None,
) -> UpdateMediaBuyRequest:
    """Build UpdateMediaBuyRequest from flat parameters.

    Handles deprecated field mapping and budget object construction.
    Used by both MCP wrapper and A2A raw function.
    """
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
    if idempotency_key is not None:
        request_params["idempotency_key"] = idempotency_key
    if revision is not None:
        request_params["revision"] = revision

    try:
        req = UpdateMediaBuyRequest(**request_params)
    except ValidationError as e:
        raise invalid_update_request_error(e) from e

    # BR-RULE-022: reject empty updates (no updatable fields beyond identifier)
    if not req.has_updatable_fields():
        raise AdCPValidationError(
            "Update request must include at least one updatable field "
            "(paused, start_time, end_time, packages, budget, "
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
    idempotency_key: Annotated[str | None, Field(description="Idempotency key for retry safety")] = None,
    revision: Annotated[
        int | None,
        Field(description="Expected current revision for optimistic concurrency (CONFLICT on mismatch)"),
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
        idempotency_key: Idempotency key for retry safety (optional, per AdCP spec)
        revision: Expected current revision for optimistic concurrency — CONFLICT on mismatch (optional, per AdCP spec)
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
        idempotency_key=idempotency_key,
        revision=revision,
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
    idempotency_key: str | None = None,  # AdCP idempotency key for retry safety
    revision: int | None = None,  # AdCP optimistic-concurrency token (CONFLICT on mismatch)
    ctx: Context | ToolContext | None = None,
    identity: ResolvedIdentity | None = None,
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
        idempotency_key: Idempotency key for retry safety (optional, per AdCP spec)
        revision: Expected current revision for optimistic concurrency — CONFLICT on mismatch (optional, per AdCP spec)
        ctx: Context for authentication (deprecated, use identity)
        identity: Pre-resolved identity (if available)

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
        idempotency_key=idempotency_key,
        revision=revision,
    )
    if identity is None:
        identity = resolve_identity_from_context(ctx, require_valid_token=True)
    # A2A/REST callers pass identity directly without a FastMCP Context, so there
    # is no workflow context_id to forward — _impl creates one if needed.
    return _update_media_buy_impl(req=req, identity=identity, context_id=None)
