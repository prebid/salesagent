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
from typing import Any

# ---------------------------------------------------------------------------
# Financial policy constants (F-05)
# ---------------------------------------------------------------------------

#: Absolute upper bound for any campaign-level budget update.
#: Configurable via MAX_CAMPAIGN_BUDGET_USD env var; default 10,000,000.
MAX_CAMPAIGN_BUDGET: Decimal = Decimal(os.environ.get("MAX_CAMPAIGN_BUDGET_USD", "10000000"))

from adcp.decisioning.state_machines import MEDIA_BUY_TRANSITIONS
from adcp.types import CreativeAction, Error
from sqlalchemy import select

from src.core.exceptions import (
    AdCPAuthenticationError,
    AdCPAuthorizationError,
    AdCPInvalidStateError,
    AdCPMediaBuyNotFoundError,
    AdCPNotCancellableError,
    AdCPPackageNotFoundError,
    AdCPValidationError,
)

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
    UpdateMediaBuyError,
    UpdateMediaBuyRequest,
    UpdateMediaBuySuccess,
)
from src.core.testing_hooks import AdCPTestContext
from src.core.tools._gam_projection import (
    get_or_materialize_media_buy,
    is_projected_media_buy_id,
    log_materialization_audit,
)
from src.core.tools.media_buy_create import _status_after_creative_attachment


def _is_terminal_media_buy_state(status: str | None) -> bool:
    """Return True iff ``status`` is a terminal state per the AdCP spec graph.

    Reads :data:`adcp.decisioning.state_machines.MEDIA_BUY_TRANSITIONS` as
    the single source of truth — terminal states are those mapping to an
    empty ``frozenset()`` of legal next states. The upstream graph
    currently lists ``canceled``, ``completed``, and ``rejected``;
    earlier this branch hard-coded ``("canceled", "completed")``, which
    silently drifts the moment the spec adds a new terminal state (e.g.
    ``rejected``, which the inline tuple missed).

    Statuses outside the spec's vocabulary (our DB models include
    ``pending_approval`` / ``draft`` ahead of the lifecycle handoff to
    the spec graph) are NOT terminal — they're pre-lifecycle internal
    states that the spec's state machine doesn't model, and the
    pause/resume guard MUST NOT reject them on the basis of being
    "unknown". The upstream :func:`assert_media_buy_transition` raises
    ``INVALID_STATE`` for unknown ``from_state`` values; that's the
    wrong shape here.

    The ``None`` guard is for the type narrower: mypy can't infer ``str``
    from ``status in MEDIA_BUY_TRANSITIONS`` and would reject the
    subscript ``MEDIA_BUY_TRANSITIONS[status]`` without it.
    """
    if status is None:
        return False
    return status in MEDIA_BUY_TRANSITIONS and not MEDIA_BUY_TRANSITIONS[status]


def _apply_creative_attachment_status_transition(media_buy_obj: Any, media_buy_id: str, reason: str) -> None:
    """Advance media-buy status after creatives are attached, when applicable."""
    previous_status = media_buy_obj.status
    next_status = _status_after_creative_attachment(
        current_status=previous_status,
        approved_at=media_buy_obj.approved_at,
        start_time=media_buy_obj.start_time,
        end_time=media_buy_obj.end_time,
    )
    if next_status is None:
        return

    media_buy_obj.status = next_status
    logger.info(
        f"[UPDATE] Media buy {media_buy_id} transitioned from {previous_status} to {media_buy_obj.status} ({reason})"
    )


def _is_explicit_cancel_request(req: UpdateMediaBuyRequest) -> bool:
    """Return True only when the buyer explicitly sent ``canceled=True``.

    The AdCP-generated request type historically defaulted ``canceled`` to
    true, so cancellation decisions must be based on fields supplied by the
    buyer, not just the attribute value.
    """
    return "canceled" in getattr(req, "model_fields_set", set()) and req.canceled is True


def _add_media_buy_update_workflow_mapping(session: Any, step_id: str, media_buy_id: str) -> None:
    """Link an update workflow step to its media buy before completion/webhooks."""
    # FIXME(salesagent-9f2): workflow mapping should use a repository method
    from src.core.database.models import ObjectWorkflowMapping

    session.add(
        ObjectWorkflowMapping(
            step_id=step_id,
            object_type="media_buy",
            object_id=media_buy_id,
            action="update",
        )
    )


def _has_media_buy_update_workflow_mapping(step: Any, media_buy_id: str) -> bool:
    for mapping in getattr(step, "object_mappings", None) or []:
        if (
            getattr(mapping, "object_type", None) == "media_buy"
            and getattr(mapping, "object_id", None) == media_buy_id
            and getattr(mapping, "action", None) == "update"
        ):
            return True
    return False


class _MaterializationAuditCtx:
    """Fires ``log_materialization_audit`` on context exit when a payload is set.

    Wraps the outer UoW so the audit fires guaranteed-once on every exit
    path — success, mutation rejection, validation early-return, or
    unexpected exception — without forcing a try/finally indent shift on
    the entire ``_update_media_buy_impl`` body. Exits the audit context
    AFTER the UoW commits, so the audit logger's separate session/commit
    can't expire the freshly-flushed MediaBuy on the parent session.
    """

    def __init__(self) -> None:
        self.payload: dict | None = None

    def __enter__(self) -> "_MaterializationAuditCtx":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Return None (== falsy) so exceptions are never suppressed.
        if self.payload is not None:
            log_materialization_audit(**self.payload)


from src.core.tools.financial_validation import (
    validate_max_campaign_budget,
    validate_max_daily_package_spend,
    validate_min_package_budget,
)

# GAM enforces immutability of reservation-affecting fields on guaranteed
# line items after approval. Without a pre-flight check, the seller
# discovers the constraint only after Order has been mutated and ~3 min
# of NO_FORECAST_YET retries have run on the LineItem leg, leaving
# Order new + LineItem stale + DB new (three-way drift).
_GUARANTEED_LINE_ITEM_TYPES: set[str] = {"STANDARD", "SPONSORSHIP"}
_RESERVATION_FIELDS: set[str] = {"start_time", "end_time", "budget"}


def serialize_for_workflow_step(model: Any) -> dict[str, Any]:
    """Serialize a Pydantic model for storage on ``workflow_step.response_data``.

    The ``workflow_step`` table holds the original request/response blob in
    a JSONB column so the approval reviewer can replay or inspect the call.
    JSONB needs a dict, not a model — but this is *persistence*, not a
    transport boundary, so the no-``.model_dump()``-in-``_impl`` rule does
    not apply.

    Centralizes the dump kwargs (``mode='json'``) so all ~22 call sites
    stay consistent, and lets the architecture guard switch from a
    fragile ``(file, line_number)`` allowlist to a single function-name
    exemption — see issue #240.
    """
    return model.model_dump(mode="json")


def _check_guaranteed_immutable(
    req: UpdateMediaBuyRequest,
    media_buy_id: str,
    uow: MediaBuyUoW,
    session,
    tenant_id: str,
) -> UpdateMediaBuyError | None:
    """Refuse reservation-affecting updates against guaranteed line items.

    Returns ``None`` when the request is safe to proceed. Returns a
    prepared ``UpdateMediaBuyError`` (code ``guaranteed_line_item_immutable``)
    when at least one package's product is configured as a guaranteed
    GAM line item (``STANDARD`` or ``SPONSORSHIP``) and the request
    touches any reservation field (``start_time``, ``end_time``,
    ``budget``).

    Default-allow on unknown ``line_item_type`` so this never blocks new
    GAM types Google introduces later.
    """
    # ``budget`` is no longer a real Pydantic field — it's a property reading
    # from ``ext.salesagent.budget``. Detect it explicitly via the property.
    requested = set(req.model_dump(exclude_unset=True).keys()) & _RESERVATION_FIELDS
    if req.budget is not None:
        requested.add("budget")
    if not requested:
        return None

    from src.core.database.models import Product as ModelProduct

    assert uow.media_buys is not None
    packages = uow.media_buys.get_packages(media_buy_id)
    if not packages:
        return None

    product_ids = {pkg.package_config.get("product_id") for pkg in packages if pkg.package_config}
    product_ids.discard(None)
    if not product_ids:
        return None

    products = session.scalars(
        select(ModelProduct).where(
            ModelProduct.tenant_id == tenant_id,
            ModelProduct.product_id.in_(product_ids),
        )
    ).all()

    for product in products:
        impl_config = product.implementation_config or {}
        li_type = impl_config.get("line_item_type")
        if li_type in _GUARANTEED_LINE_ITEM_TYPES:
            blocked_fields = sorted(requested)
            return UpdateMediaBuyError(
                errors=[
                    Error(
                        code="guaranteed_line_item_immutable",
                        message=(
                            f"Media buy {media_buy_id} is a guaranteed line item "
                            f"({li_type}); reservation-affecting fields "
                            f"({', '.join(blocked_fields)}) cannot be modified after "
                            f"approval. Pause and recreate, or contact the publisher."
                        ),
                        details={"line_item_type": li_type, "blocked_fields": blocked_fields},
                    )
                ],
                context=req.context,
            )

    return None


def _verify_principal(media_buy_id: str, context: "ResolvedIdentity", repo: MediaBuyRepository) -> None:
    """Verify that the principal from context owns the media buy.

    Uses the provided repository for database access (no own session).

    Args:
        media_buy_id: Media buy ID to verify
        context: ResolvedIdentity with principal info
        repo: Tenant-scoped MediaBuyRepository for DB lookups

    Raises:
        AdCPAuthenticationError: Missing principal
        AdCPMediaBuyNotFoundError: Media buy not found (or exists in a different
            tenant — tenant isolation hides the existence from cross-tenant callers).
        AdCPAuthorizationError: Principal doesn't own this media buy (same tenant).
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

    # Query database for media buy by ID. The repository is tenant-scoped, so
    # rows that belong to a different tenant come back as ``None`` — surface
    # that as MEDIA_BUY_NOT_FOUND so we don't leak cross-tenant existence.
    media_buy = repo.get_by_id(media_buy_id)

    if not media_buy:
        raise AdCPMediaBuyNotFoundError(f"Media buy '{media_buy_id}' not found.")

    if media_buy.source == "gam_import":
        if not repo.gam_import_is_assigned_to_principal(media_buy, principal_id):
            security_logger = get_audit_logger("AdCP", tenant["tenant_id"])
            security_logger.log_security_violation(
                operation="access_media_buy",
                principal_id=principal_id,
                resource_id=media_buy_id,
                reason="Materialized GAM import no longer has a live advertiser assignment for this principal",
            )
            raise AdCPAuthorizationError(f"Principal '{principal_id}' does not own media buy '{media_buy_id}'.")
        return

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


def _update_media_buy_impl(
    req: UpdateMediaBuyRequest,
    identity: ResolvedIdentity | None = None,
    context_id: str | None = None,
    bypass_manual_approval: bool = False,
) -> UpdateMediaBuySuccess | UpdateMediaBuyError:
    """Shared implementation for update_media_buy (used by both MCP and A2A).

    Callers construct the validated UpdateMediaBuyRequest at their boundary
    (MCP wrapper from typed FastMCP params, A2A raw from dict params).

    Uses a single MediaBuyUoW for the entire operation — one session, one transaction.

    Args:
        req: Validated UpdateMediaBuyRequest with all protocol fields
        identity: ResolvedIdentity with principal/tenant info (transport-agnostic)
        context_id: Optional workflow context ID
        bypass_manual_approval: When True, skip the manual-approval gate and
            apply the update immediately. Used by the workflows-blueprint
            replay path: an operator has already approved the deferred step
            and we are now executing it. Buyer-facing transports (MCP/A2A)
            never set this.

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

    # Single UoW for entire update operation — one session, one transaction.
    # The audit context wraps the UoW so the materialization audit fires
    # exactly once per first-time materialization, regardless of which
    # exit path the function takes. Audit fires AFTER the UoW commits.
    audit_ctx = _MaterializationAuditCtx()
    with audit_ctx, MediaBuyUoW(tenant["tenant_id"]) as uow:
        assert uow.media_buys is not None
        # FIXME(salesagent-9f2): raw session usages below should migrate to repository methods
        assert uow.session is not None
        session = uow.session

        # media_buy_id is required by library base class
        media_buy_id_to_use = req.media_buy_id

        if not media_buy_id_to_use:
            raise ValueError("media_buy_id is required")

        # Materialize projected GAM orders on first write. The buyer
        # received this id from get_media_buys' projection (gam_<order_id>);
        # we now create a real media_buys row so packages, push configs,
        # and audit logs have a stable PK to attach to. Authorization
        # check happens inside materialize_projected_buy via the
        # GamAdvertiser.principal_id assignment.
        #
        # Native AdCP buys (mb_<uuid> ids) skip this whole branch — no
        # extra DB calls in the hot path.
        imported_buy = None
        if is_projected_media_buy_id(media_buy_id_to_use):
            imported_buy = uow.media_buys.get_by_id(media_buy_id_to_use)
            if imported_buy is None:
                imported_buy = get_or_materialize_media_buy(
                    session=session,
                    tenant_id=tenant["tenant_id"],
                    principal_id=principal_id,
                    media_buy_id=media_buy_id_to_use,
                )
                # Capture audit fields by value so the audit log call
                # (which opens its own DB session and commits) doesn't
                # need to re-read from the freshly-flushed instance.
                # Setting on audit_ctx — fires from __exit__ after UoW commit.
                audit_ctx.payload = {
                    "tenant_id": tenant["tenant_id"],
                    "principal_id": principal_id,
                    "advertiser_name": imported_buy.advertiser_name,
                    "advertiser_id": (imported_buy.raw_request or {}).get("gam_advertiser_id"),
                    "order_id": imported_buy.external_id,
                    "media_buy_id": imported_buy.media_buy_id,
                }

        # Verify principal owns this media buy
        _verify_principal(media_buy_id_to_use, identity, uow.media_buys)

        # Imported buys (source='gam_import') don't yet support adapter
        # writeback — see #100 follow-ups. Returning success when the
        # mutation isn't propagated to GAM would lie about the seller's
        # state, so reject any request that carries mutating fields.
        # No-op calls (used to trigger materialization) are still allowed.
        if imported_buy is not None and imported_buy.source == "gam_import":
            mutating = (
                req.paused is not None
                or req.canceled is not None
                or req.start_time is not None
                or req.end_time is not None
                or req.budget is not None
                or bool(req.packages)
                or bool(req.new_packages)
            )
            if mutating:
                # Materialization persisted even though we're rejecting
                # the mutation — the wrapping ``_MaterializationAuditCtx``
                # fires the audit on context exit, so we can return
                # immediately without re-emitting here.
                return UpdateMediaBuyError(
                    media_buy_id=media_buy_id_to_use,
                    errors=[
                        Error(
                            code="not_implemented",
                            message=(
                                "Updating an imported GAM media buy is not yet supported. "
                                "Adapter writeback to GAM is in development; tracked at "
                                "github.com/bokelley/salesagent/issues/100."
                            ),
                        )
                    ],
                    context=req.context,
                )

        # Extract testing context early (needed for dry_run check)
        testing_ctx = identity.testing_context if identity.testing_context else AdCPTestContext()
        # Cancellation is a terminal buyer-side control; routing it through
        # manual approval returns a deferred success without actually canceling.
        cancel_requested = _is_explicit_cancel_request(req)
        deferred_cancel_step = None

        # Idempotency replay (defence-in-depth on the SDK's post-hoc
        # IdempotencyStore.wrap): if a prior call with this idempotency_key
        # already completed (response_data populated), replay its response
        # verbatim instead of re-executing. The SDK wrap caches AFTER the
        # handler completes, so two sequential same-key calls hitting the
        # impl before the first commits both reach this point. Mirrors the
        # create-path pattern at media_buy_create.py:1471-1489. Skipped in
        # dry_run since dry_run never writes a workflow step to read back.
        if not testing_ctx.dry_run and req.idempotency_key:
            from src.core.database.repositories.workflow import WorkflowRepository

            workflow_repo = WorkflowRepository(session, tenant["tenant_id"])
            existing_step = workflow_repo.find_by_idempotency_key(
                req.idempotency_key,
                principal_id,
                tool_name="update_media_buy",
            )
            if existing_step is not None and existing_step.response_data:
                if cancel_requested and existing_step.status == "requires_approval":
                    logger.warning(
                        "[IDEMPOTENCY] update_media_buy repairing deferred cancel step %s for key=%s...",
                        existing_step.step_id,
                        req.idempotency_key[:8],
                    )
                    deferred_cancel_step = existing_step
                else:
                    logger.info(
                        f"[IDEMPOTENCY] update_media_buy replaying step {existing_step.step_id} "
                        f"for key={req.idempotency_key[:8]}..."
                    )
                    cached = {k: v for k, v in existing_step.response_data.items() if k != "request_data"}
                    if cached.get("errors"):
                        return UpdateMediaBuyError.model_validate(cached)
                    return UpdateMediaBuySuccess.model_validate(cached)

        # Pre-flight: every referenced package_id must exist on this media
        # buy. Run before the manual-approval gate, workflow-step writes,
        # and adapter dispatch so a bogus package_id surfaces
        # PACKAGE_NOT_FOUND uniformly — regardless of which fields the
        # buyer set on the package update or whether the publisher
        # requires manual approval. Keeping the check inside the
        # per-package mutation loop (PR #215) missed two paths:
        # (1) manual-approval-required tenants short-circuit to a
        # "pending approval" success before the loop runs, and
        # (2) bare-reference package updates (only ``package_id`` set,
        # no fields to mutate) fall through the loop silently.
        # Issue #251.
        if req.packages:
            for pkg_update in req.packages:
                if (
                    pkg_update.package_id
                    and uow.media_buys.get_package(media_buy_id_to_use, pkg_update.package_id) is None
                ):
                    raise AdCPPackageNotFoundError(
                        f"Package '{pkg_update.package_id}' not found for media buy '{media_buy_id_to_use}'."
                    )

        # Create or get persistent context and workflow step
        # Skip for dry_run mode (no side effects, no database writes)
        ctx_manager = get_context_manager()
        ctx_id = context_id  # Extracted at transport boundary, passed in
        persistent_ctx = None
        step = None

        if not testing_ctx.dry_run:
            if deferred_cancel_step is not None:
                step = deferred_cancel_step
            else:
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
                errors=[Error(code="principal_not_found", message=error_msg)],
                context=req.context,
            )
            if step:
                ctx_manager.update_workflow_step(
                    step.step_id,
                    status="failed",
                    response_data=serialize_for_workflow_step(response_data),
                    error_message=error_msg,
                )
            return response_data

        adapter = get_adapter(principal, dry_run=testing_ctx.dry_run, testing_context=testing_ctx, tenant=tenant)
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
                            package_id=pkg_update.package_id or "",
                            paused=pkg_update.paused if pkg_update.paused is not None else False,
                            buyer_package_ref=pkg_update.package_id,
                            changes_applied={"dry_run": True, "would_update": pkg_update},
                        )
                    )

            # Build simulated response
            dry_run_response = UpdateMediaBuySuccess(
                media_buy_id=req.media_buy_id or "",
                affected_packages=simulated_affected,
                context=req.context,
            )

            return dry_run_response

        # Type narrowing: after dry_run early return, a workflow step is guaranteed to exist
        assert step is not None, "step should be created when not in dry_run mode"

        # Pre-flight: GAM rejects reservation-affecting mutations on guaranteed
        # line items after approval. Refusing here avoids ~3min of doomed
        # NO_FORECAST_YET retries and the half-mutated Order they leave behind.
        guaranteed_block = _check_guaranteed_immutable(req, media_buy_id_to_use, uow, session, tenant["tenant_id"])
        if guaranteed_block is not None:
            err_msg = guaranteed_block.errors[0].message
            ctx_manager.update_workflow_step(
                step.step_id,
                status="failed",
                response_data=serialize_for_workflow_step(guaranteed_block),
                error_message=err_msg,
            )
            return guaranteed_block

        # Check if manual approval is required
        manual_approval_required = adapter.manual_approval_required
        manual_approval_operations = adapter.manual_approval_operations

        if (
            manual_approval_required
            and "update_media_buy" in manual_approval_operations
            and not bypass_manual_approval
            and not cancel_requested
        ):
            # Store the original request alongside the response so the approval
            # execution path can re-execute the update after human approval.
            # This mirrors create_media_buy's raw_request pattern.
            #
            # Include the media buy's CURRENT persisted lifecycle status — the update
            # hasn't transitioned the buy yet (it's pending approval).
            # Without this, buyer agents walking the response can't
            # distinguish a deferred update from a noop, and the
            # ``media_buy_state_machine / pause_buy`` storyboard would fail
            # on ``field_present @ /media_buy_status`` (#353) for tenants that route
            # update_media_buy through manual approval.
            #
            # Coerce the persisted status through ``_to_wire_status`` before
            # emitting — the DB column stores values like ``draft`` /
            # ``pending_approval`` that the wire enum does not accept (#374).
            # Without the coercion, those persisted-only values reach
            # ``UpdateMediaBuySuccess`` and fastmcp rejects the response
            # with ``INVALID_REQUEST[status]``.
            from src.core.tools.media_buy_list import _to_wire_status

            current_buy = uow.media_buys.get_by_id(req.media_buy_id) if req.media_buy_id else None
            current_media_buy_status: str | None = _to_wire_status(
                getattr(current_buy, "status", None) if current_buy is not None else None
            )
            approval_response = UpdateMediaBuySuccess(
                media_buy_id=req.media_buy_id or "",
                media_buy_status=current_media_buy_status,
                affected_packages=[],  # Not yet applied — pending approval
                context=req.context,
                # Surface the workflow step id so buyers can disambiguate
                # "deferred for approval" from "applied with no package
                # effect" — both otherwise serialize to the same envelope
                # shape. (#158)
                workflow_step_id=step.step_id,
            )
            approval_data = serialize_for_workflow_step(approval_response)
            approval_data["request_data"] = serialize_for_workflow_step(req)
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
            _add_media_buy_update_workflow_mapping(session, step.step_id, media_buy_id_to_use)

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
                        errors=[Error(code="currency_not_supported", message=error_msg)],
                        context=req.context,
                    )
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        response_data=serialize_for_workflow_step(response_data),
                        error_message=error_msg,
                    )
                    return response_data

                # Unwrap StartTiming RootModel when req carries the typed form.
                req_start_inner: Any = (
                    req.start_time.root
                    if req.start_time is not None and hasattr(req.start_time, "root")
                    else req.start_time
                )
                start = req_start_inner if req_start_inner is not None else media_buy.start_time
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
                                    errors=[Error(code="budget_limit_exceeded", message=package_daily_spend_error)],
                                    context=req.context,
                                )
                                ctx_manager.update_workflow_step(
                                    step.step_id,
                                    status="failed",
                                    response_data=serialize_for_workflow_step(response_data),
                                    error_message=package_daily_spend_error,
                                )
                                return response_data

        # Cancel: terminal transition. Double-cancel raises NOT_CANCELLABLE.
        # Wired to the DB row directly (no adapter dispatch yet — the mock
        # adapter only had an in-memory state change for this; GAM order
        # archive on cancel is a follow-up). ``cancellation_reason`` is
        # echoed back on the response.
        #
        # IMPORTANT: ``UpdateMediaBuyRequest.canceled`` is the AdCP-generated
        # ``Literal[True] = True`` field — the Pydantic default is True
        # whenever the buyer didn't include it in the wire payload. A
        # naive ``req.canceled is True`` check would therefore fire on
        # EVERY update (pause, budget, packages, etc.), preempting their
        # branches. Gate on ``model_fields_set`` so only an explicit
        # ``canceled=True`` from the buyer triggers cancellation.
        if cancel_requested:
            current_mb = uow.media_buys.get_by_id(req.media_buy_id)
            if current_mb and str(current_mb.status) == "canceled":
                # Pre-validation: re-cancel of a terminal buy raises the typed
                # AdCPNotCancellableError BEFORE adapter dispatch. Idempotency-spec
                # friendly: same key + same payload yields the same wire code
                # regardless of which adapter would have been called. The delegate
                # translates this into the wire NOT_CANCELLABLE envelope with
                # recovery="correctable".
                error_msg = f"media_buy_id={req.media_buy_id!r} is already canceled — cannot cancel a terminal buy"
                ctx_manager.update_workflow_step(
                    step.step_id,
                    status="failed",
                    error_message="already canceled",
                )
                raise AdCPNotCancellableError(error_msg)

            # MediaBuy ORM has no cancellation_reason column today —
            # echoing the reason back on the response (via the SDK's
            # response context) is the spec-compliant minimum. Persisting
            # it would need a schema migration; tracked separately.
            uow.media_buys.update_fields(req.media_buy_id, status="canceled")

            # Include the resulting lifecycle status — buyers need
            # ``media_buy_status="canceled"``
            # to confirm the lifecycle transition without an extra
            # ``get_media_buys`` round-trip. The
            # ``media_buy_state_machine / cancel_buy`` storyboard asserts on
            # ``field_present @ /media_buy_status`` (#353).
            cancel_response = UpdateMediaBuySuccess(
                media_buy_id=req.media_buy_id or "",
                media_buy_status="canceled",
                affected_packages=[],
                context=req.context,
            )
            if deferred_cancel_step is None or not _has_media_buy_update_workflow_mapping(step, media_buy_id_to_use):
                _add_media_buy_update_workflow_mapping(session, step.step_id, media_buy_id_to_use)
            ctx_manager.update_workflow_step(
                step.step_id,
                status="completed",
                response_data=serialize_for_workflow_step(cancel_response),
            )
            return cancel_response

        # Handle campaign-level updates
        if req.paused is not None:
            # Pre-validation: pause/resume on a terminal-state buy violates the
            # AdCP state machine. The cancel branch (above) already guards
            # cancel-of-canceled with the narrower AdCPNotCancellableError;
            # the broader pause/resume case raises AdCPInvalidStateError so
            # buyers see the spec-canonical INVALID_STATE wire code on
            # ``/adcp_error/code`` (storyboard
            # ``media_buy_state_machine/pause_canceled_buy``). Runs BEFORE
            # adapter dispatch so the rejection is idempotency-spec friendly
            # — same payload yields the same wire code on retry regardless
            # of which adapter would have handled the transition.
            #
            # The "is this state terminal?" predicate reads the upstream
            # AdCP graph (:data:`MEDIA_BUY_TRANSITIONS`) so a future spec
            # addition of a new terminal state (e.g. ``rejected``, already
            # there since adcp 5.3) is picked up by construction. The
            # earlier hand-rolled tuple ``("canceled", "completed")`` had
            # already drifted: ``rejected`` is terminal per spec but was
            # not in the tuple, so pause/resume of a rejected buy would
            # fall through to the adapter and return a less-typed error.
            current_mb = uow.media_buys.get_by_id(req.media_buy_id)
            current_status = str(current_mb.status) if current_mb else None
            if _is_terminal_media_buy_state(current_status):
                action_name = "pause" if req.paused else "resume"
                error_msg = (
                    f"media_buy_id={req.media_buy_id!r} is in terminal state {current_status!r} — "
                    f"cannot {action_name} a {current_status} buy"
                )
                ctx_manager.update_workflow_step(
                    step.step_id,
                    status="failed",
                    error_message=f"terminal state: {current_status}",
                )
                raise AdCPInvalidStateError(error_msg)

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
                error_response = UpdateMediaBuyError(errors=result.errors, context=req.context)
                ctx_manager.update_workflow_step(
                    step.step_id,
                    status="failed",
                    response_data=serialize_for_workflow_step(error_response),
                    error_message=result.errors[0].message if result.errors else "Pause/resume failed",
                )
                return error_response
            else:
                # UpdateMediaBuySuccess extends adcp v1.2.1 with internal fields
                # Use getattr to safely access discriminated union fields
                media_buy_id = getattr(result, "media_buy_id", req.media_buy_id or "")
                affected_pkgs = getattr(result, "affected_packages", [])

                # Echo the resulting media-buy lifecycle status — ``paused`` after a
                # pause, ``active`` after a resume. Buyers need this to
                # confirm the transition; the
                # ``media_buy_state_machine / {pause,resume}_buy`` storyboards
                # assert on ``field_present @ /media_buy_status`` (#353).
                resulting_media_buy_status = "paused" if req.paused else "active"
                success_response = UpdateMediaBuySuccess(
                    media_buy_id=media_buy_id,
                    media_buy_status=resulting_media_buy_status,
                    affected_packages=affected_pkgs,
                    context=req.context,
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
                    response_data=serialize_for_workflow_step(success_response),
                )
                return success_response

        # Handle package-level updates
        # (Package existence pre-validated above for issue #251 — every
        # package_id has already been confirmed to exist on this buy.)
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
                        response_data = UpdateMediaBuyError(errors=result.errors, context=req.context)
                        ctx_manager.update_workflow_step(
                            step.step_id,
                            status="failed",
                            response_data=serialize_for_workflow_step(response_data),
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
                            context=req.context,
                        )
                        ctx_manager.update_workflow_step(
                            step.step_id,
                            status="failed",
                            response_data=serialize_for_workflow_step(response_data),
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
                                errors=[Error(code="budget_below_minimum", message=package_min_budget_error)],
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
                        response_data = UpdateMediaBuyError(errors=result.errors, context=req.context)
                        ctx_manager.update_workflow_step(
                            step.step_id,
                            status="failed",
                            response_data=serialize_for_workflow_step(response_data),
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
                            errors=[Error(code="missing_package_id", message=error_msg)],
                            context=req.context,
                        )
                        ctx_manager.update_workflow_step(
                            step.step_id,
                            status="failed",
                            response_data=serialize_for_workflow_step(response_data),
                            error_message=error_msg,
                        )
                        return response_data

                    from src.core.database.models import Creative as DBCreative
                    from src.core.database.models import CreativeAssignment as DBAssignment

                    # Resolve media_buy_id (tenant-scoped — None for cross-tenant)
                    media_buy_obj = uow.media_buys.get_by_id(req.media_buy_id)

                    if not media_buy_obj:
                        error_msg = f"Media buy '{req.media_buy_id}' not found"
                        ctx_manager.update_workflow_step(
                            step.step_id,
                            status="failed",
                            error_message=error_msg,
                        )
                        raise AdCPMediaBuyNotFoundError(error_msg)

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
                            context=req.context,
                        )
                        ctx_manager.update_workflow_step(
                            step.step_id,
                            status="failed",
                            response_data=serialize_for_workflow_step(response_data),
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
                    # Get package and product to check supported formats.
                    # Aliased as ModelProduct to avoid colliding with the schema
                    # Product class (the import-collision guard rejects bare
                    # select-of-Product to keep model/schema queries unambiguous).
                    from src.core.database.models import Product as ModelProduct

                    db_package = uow.media_buys.get_package(actual_media_buy_id, pkg_update.package_id)

                    # Get product_id from package_config
                    product_id = (
                        db_package.package_config.get("product_id")
                        if db_package and db_package.package_config
                        else None
                    )

                    if product_id:
                        # Get product to check supported formats
                        product_stmt = select(ModelProduct).where(
                            ModelProduct.tenant_id == tenant["tenant_id"],
                            ModelProduct.product_id == product_id,
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

                    # Creative IDs attach creatives to the buy. Once attached,
                    # pending_creatives clears independently of creative review state.
                    if pkg_update.creative_ids:
                        _apply_creative_attachment_status_transition(
                            media_buy_obj,
                            actual_media_buy_id,
                            f"creative_ids: {pkg_update.creative_ids}",
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
                            errors=[Error(code="missing_package_id", message=error_msg)],
                            context=req.context,
                        )
                        ctx_manager.update_workflow_step(
                            step.step_id,
                            status="failed",
                            response_data=serialize_for_workflow_step(response_data),
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
                            f"{r.creative_id}: {', '.join(str(e) for e in (r.errors or []))}" for r in failed_creatives
                        ]
                        error_msg = f"Failed to sync creatives: {'; '.join(error_msgs)}"
                        response_data = UpdateMediaBuyError(
                            errors=[Error(code="creative_sync_failed", message=error_msg)],
                            context=req.context,
                        )
                        ctx_manager.update_workflow_step(
                            step.step_id,
                            status="failed",
                            response_data=serialize_for_workflow_step(response_data),
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
                            errors=[Error(code="missing_package_id", message=error_msg)],
                            context=req.context,
                        )
                        ctx_manager.update_workflow_step(
                            step.step_id,
                            status="failed",
                            response_data=serialize_for_workflow_step(response_data),
                            error_message=error_msg,
                        )
                        return response_data

                    from src.core.database.models import CreativeAssignment as DBAssignment
                    from src.core.database.models import Product as ProductModel

                    # Resolve media_buy_id (tenant-scoped — None for cross-tenant)
                    media_buy_obj = uow.media_buys.get_by_id(req.media_buy_id)

                    if not media_buy_obj:
                        raise AdCPMediaBuyNotFoundError(f"Media buy '{req.media_buy_id}' not found")

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
                            raise AdCPPackageNotFoundError(
                                f"Package '{pkg_update.package_id}' not found for media buy '{actual_media_buy_id}'"
                            )

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
                                        context=req.context,
                                    )
                                    return response_data
                            elif product_obj and not product_obj.placements:
                                # Product doesn't define placements, so placement targeting not supported
                                error_msg = f"Product '{product_id}' does not support placement targeting (no placements defined)"
                                response_data = UpdateMediaBuyError(
                                    errors=[Error(code="placement_targeting_not_supported", message=error_msg)],
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

                    # State machine: creative_assignments may unblock pending media buys.
                    #    Per spec/storyboard ``pending_creatives_to_start``, the buy state
                    #    advances when creatives are *attached*, independently of creative
                    #    review state — the two lifecycles are decoupled. ``_compute_status``
                    #    on the read path will derive pending_start/active from flight dates
                    #    once we clear the persisted blocker.
                    if pkg_update.creative_assignments and updated_assignments:
                        _apply_creative_attachment_status_transition(
                            media_buy_obj,
                            actual_media_buy_id,
                            f"creative_assignments processed: {updated_assignments}",
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
                            errors=[Error(code="missing_package_id", message=error_msg)],
                            context=req.context,
                        )
                        ctx_manager.update_workflow_step(
                            step.step_id,
                            status="failed",
                            response_data=serialize_for_workflow_step(response_data),
                            error_message=error_msg,
                        )
                        return response_data

                    from sqlalchemy.orm import attributes

                    # Get the package via repository
                    media_package = uow.media_buys.get_package(req.media_buy_id, pkg_update.package_id)

                    if not media_package:
                        error_msg = f"Package {pkg_update.package_id} not found for media buy {req.media_buy_id}"
                        ctx_manager.update_workflow_step(
                            step.step_id,
                            status="failed",
                            error_message=error_msg,
                        )
                        raise AdCPPackageNotFoundError(error_msg)

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
                    errors=[Error(code="invalid_budget", message=error_msg)],
                    context=req.context,
                )
                ctx_manager.update_workflow_step(
                    step.step_id,
                    status="failed",
                    response_data=serialize_for_workflow_step(response_data),
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
                    errors=[Error(code="budget_ceiling_exceeded", message=budget_error)],
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
            update_values: dict[str, Any] = {}
            if req.start_time is not None:
                # req.start_time is StartTiming (RootModel[datetime | "asap"]) when constructed
                # via Pydantic; tests may pass a bare datetime/str through model_validate. Unwrap.
                start_inner: Any = req.start_time.root if hasattr(req.start_time, "root") else req.start_time
                if isinstance(start_inner, str):
                    if start_inner == "asap":
                        update_values["start_time"] = datetime.now(UTC)
                    else:
                        update_values["start_time"] = datetime.fromisoformat(start_inner.replace("Z", "+00:00"))
                elif isinstance(start_inner, datetime):
                    update_values["start_time"] = start_inner

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
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        error_message=error_msg,
                    )
                    raise AdCPMediaBuyNotFoundError(error_msg)

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
                        context=req.context,
                    )
                    ctx_manager.update_workflow_step(
                        step.step_id,
                        status="failed",
                        response_data=serialize_for_workflow_step(response_data),
                        error_message=error_msg,
                    )
                    return response_data

                uow.media_buys.update_fields(req.media_buy_id, **update_values)
                logger.info(
                    f"Updated MediaBuy {req.media_buy_id} dates in DB: "
                    f"start_time={update_values.get('start_time')}, end_time={update_values.get('end_time')}"
                )

                # Sync the change to the underlying ad server. The DB write
                # above captures the buyer's intent durably; if the adapter
                # call fails we log loudly but don't roll back, so a retry
                # can re-apply GAM-side without losing intent.
                orders_manager = getattr(adapter, "orders_manager", None)
                gam_order_id = existing_mb.external_id or existing_mb.media_buy_id
                if orders_manager is not None and gam_order_id:
                    try:
                        synced = orders_manager.update_order_dates(
                            order_id=gam_order_id,
                            start_time=update_values.get("start_time"),
                            end_time=update_values.get("end_time"),
                        )
                    except Exception as e:
                        synced = False
                        logger.error(
                            f"Adapter raised while syncing dates for {req.media_buy_id} "
                            f"(GAM order {gam_order_id}): {e}",
                            exc_info=True,
                        )
                    if not synced:
                        logger.error(
                            f"GAM date sync FAILED for {req.media_buy_id} (Order {gam_order_id}); DB updated, GAM stale"
                        )

        # Create ObjectWorkflowMapping to link media buy update to workflow step.
        # This enables webhook delivery when the update completes.
        _add_media_buy_update_workflow_mapping(session, step.step_id, media_buy_id_to_use)

        # Build final response first
        logger.info(f"[update_media_buy] Final affected_packages before return: {affected_packages_list}")

        # UpdateMediaBuySuccess extends adcp v1.2.1 with internal fields (workflow_step_id, affected_packages)
        # affected_packages_list contains AffectedPackage objects with both:
        # - AdCP-required fields (package_id) for spec compliance
        # - Internal tracking fields (buyer_package_ref, changes_applied) excluded via exclude=True

        # Compute current lifecycle status on the way out: read what we just persisted and
        # apply the same blocker / date logic the get_media_buys read path uses.
        # Without this, the response's ``media_buy_status`` is None and the storyboard
        # ``pending_creatives_to_start/assign_creative_to_package`` step can't
        # observe the transition we just performed.
        from sqlalchemy.exc import SQLAlchemyError

        from src.core.tools.media_buy_list import _compute_status

        response_media_buy_status: str | None = None
        # ``media_buy_status`` on UpdateMediaBuySuccess is best-effort — we want it
        # populated when we can read the buy back, but a fetch/parse failure
        # MUST NOT regress the rest of the response. Catch the narrow set of
        # exceptions the read-back can plausibly throw:
        #   * ``SQLAlchemyError`` — DB I/O / session state failures (prod)
        #   * ``ValueError`` — date math / enum coercion on corrupt rows (prod)
        #   * ``TypeError`` — comparison failures from unit-test fixtures that
        #     mock ``start_time`` / ``end_time`` as ``MagicMock`` (test-only;
        #     real prod rows always carry typed datetimes)
        #   * ``StopIteration`` — exhausted ``side_effect`` lists on
        #     repository mocks (test-only)
        # Programming errors (``AttributeError``, ``KeyError``, enum drift
        # in ``_compute_status``) intentionally bubble so tests surface them.
        try:
            post_update_buy = uow.media_buys.get_by_id(req.media_buy_id) if req.media_buy_id else None
            if post_update_buy is not None:
                today = datetime.now(UTC).date()
                response_media_buy_status = _compute_status(post_update_buy, today).value
        except (SQLAlchemyError, ValueError, TypeError, StopIteration) as exc:
            logger.warning(f"[update_media_buy] could not compute status for {req.media_buy_id}: {exc}")

        final_response = UpdateMediaBuySuccess(
            media_buy_id=req.media_buy_id or "",
            media_buy_status=response_media_buy_status,
            affected_packages=affected_packages_list,
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
            response_data=serialize_for_workflow_step(final_response),
        )

    return final_response
