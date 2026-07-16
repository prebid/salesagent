"""Workflow repository — tenant-scoped data access for workflow step tables.

Covers three ORM models:
- WorkflowStep: individual steps/tasks in a workflow
- ObjectWorkflowMapping: maps workflow steps to business objects
- Context (DBContext): conversation tracker for async operations

Core invariant: every query includes tenant_id in the WHERE clause (via Context join).
The tenant_id is set at construction time and injected into all queries automatically.

Write methods add objects to the session but never commit — the caller (or UoW)
handles commit/rollback at the boundary.

beads: salesagent-4d4
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from src.core.database.models import Context as DBContext
from src.core.database.models import ObjectWorkflowMapping, Principal, WorkflowStep

# Workflow-step statuses that are final outcomes. Single source of truth for the
# repository's atomic terminal-transition guard and the A2A boundary's step→TaskState map.
TERMINAL_STEP_STATUSES = frozenset({"completed", "rejected", "failed", "canceled"})

# Statuses from which a buyer cancel is still safe — i.e. states where NO irreversible
# external (ad-server) work has begun. Deliberately EXCLUDES both ``approved`` and
# ``in_progress``:
#   * ``approved`` — the admin-approve path commits it BEFORE execute_approved_media_buy,
#     so cancelling it would leave a real order behind a canceled task.
#   * ``in_progress`` — the create/update execution paths set it BEFORE running their
#     adapter/business side-effects (media_buy_create.py, media_buy_update.py), so it marks
#     that irreversible work is already underway; cancelling then would strand external
#     state behind a canceled task.
# ``approval`` is the legacy adapter-emitted awaiting-decision alias of ``requires_approval``
# (GAM/Broadstreet/base_workflow — see APPROVABLE_STEP_STATUSES below): a pre-side-effect state,
# so it is cancellable for the same reason ``requires_approval`` is. (Normalizing the alias away
# is tracked in #1659; until then it is carried in BOTH the cancellable and approvable sets.)
# Terminal statuses are (trivially) excluded too. A cancel is only accepted while the step is
# still purely pending human/forecasting action, before any side-effects have run.
CANCELLABLE_STEP_STATUSES = frozenset({"pending", "requires_approval", "pending_approval", "approval"})

# Statuses a step can be approved or rejected FROM — i.e. it is still awaiting a human
# decision and no irreversible execution has started. Approval/rejection is a compare-and-set
# from one of these to a decided status. Because ``approved`` is (deliberately) NON-terminal,
# a broad "not terminal" guard would let a SECOND concurrent approver win an ``approved →
# approved`` no-op and also run the irreversible adapter creation (duplicate order), and would
# let a reject run ``approved → rejected`` (stranding a live order behind a rejected workflow).
# Restricting the source states to this set makes exactly one decider win.
#
# ``approval`` is the LEGACY awaiting-decision status emitted by the adapter workflow managers
# (base_workflow.py default; GAM order-activation / manual-order / creative-approval steps;
# Broadstreet via the base manager). It is semantically identical to ``requires_approval`` —
# a step a publisher must approve/reject — so it MUST be approvable, otherwise those live human
# workflows can never be actioned. Normalizing every producer to the canonical
# ``requires_approval`` (+ a migration for existing ``approval`` rows) is tracked in #1659;
# until then this set carries the legacy alias.
APPROVABLE_STEP_STATUSES = frozenset({"requires_approval", "pending_approval", "approval"})


class WorkflowRepository:
    """Tenant-scoped data access for WorkflowStep and ObjectWorkflowMapping.

    All queries filter by tenant_id (via Context join) automatically. Write
    methods modify the session but never commit — the Unit of Work handles that.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    # ------------------------------------------------------------------
    # WorkflowStep reads
    # ------------------------------------------------------------------

    def get_by_step_id(self, step_id: str) -> WorkflowStep | None:
        """Get a workflow step by its ID within the tenant."""
        return self._session.scalars(
            select(WorkflowStep)
            .join(DBContext)
            .where(
                WorkflowStep.step_id == step_id,
                DBContext.tenant_id == self._tenant_id,
            )
        ).first()

    def get_approvable_step_for_object(
        self, object_type: str, object_id: str, *, step_id: str | None = None
    ) -> WorkflowStep | None:
        """The workflow step awaiting a decision for a mapped business object (tenant-scoped).

        Joins ObjectWorkflowMapping and filters status to APPROVABLE_STEP_STATUSES — the
        canonical awaiting-decision set (including the legacy ``approval`` alias emitted by the
        adapter workflow producers). The admin media-buy detail approve/reject route uses this
        so its prefilter matches the ``claim_approval`` / ``reject_if_approvable`` source-state
        guard; an inline ``{requires_approval, pending_approval}`` filter previously dropped
        legacy ``approval`` steps before they could reach the CAS.

        When ``step_id`` is supplied, the step must also be the exact decision rendered to the
        administrator. This prevents a stale form from approving a different mapped workflow
        when several approval operations exist for one media buy. Without ``step_id`` (the GET
        page), the oldest mapped approval is selected deterministically.
        """
        stmt = (
            select(WorkflowStep)
            .join(ObjectWorkflowMapping, WorkflowStep.step_id == ObjectWorkflowMapping.step_id)
            .join(DBContext, WorkflowStep.context_id == DBContext.context_id)
            .where(
                DBContext.tenant_id == self._tenant_id,
                ObjectWorkflowMapping.object_type == object_type,
                ObjectWorkflowMapping.object_id == object_id,
                WorkflowStep.status.in_(APPROVABLE_STEP_STATUSES),
            )
        )
        if step_id is not None:
            stmt = stmt.where(WorkflowStep.step_id == step_id)
        return self._session.scalars(
            stmt.order_by(ObjectWorkflowMapping.created_at, WorkflowStep.created_at, WorkflowStep.step_id)
        ).first()

    def get_by_external_task_id(self, external_task_id: str, *, principal_id: str) -> WorkflowStep | None:
        """Get the workflow step carrying a given transport outer task id.

        The A2A boundary persists its outer ``task_*`` id (the id returned to the
        buyer) on the create step's ``request_data.external_task_id`` (see
        ``_create_media_buy_impl``), so a durable ``tasks/get`` poll can resolve the
        buyer's id → step → terminal status + stored ``response_data`` artifact,
        surviving a server restart (the admin approval that terminalized the step runs
        in a different process, so the in-memory task map is not enough). #1544 B6.

        Scoped to BOTH the tenant (Context join, like every read) AND the owning
        ``principal_id``: task ids are bearer-ish identifiers, and the durable
        get/cancel must authorize the CALLER — another principal in the same tenant
        who learns a task id must be able to neither read its stored response_data
        nor cancel its workflow. Keyword-required so no call site can omit the scope.
        """
        return self._session.scalars(
            select(WorkflowStep)
            .join(DBContext)
            .where(
                WorkflowStep.request_data["external_task_id"].as_string() == external_task_id,
                DBContext.tenant_id == self._tenant_id,
                DBContext.principal_id == principal_id,
            )
        ).first()

    def get_by_step_id_or_raise(self, step_id: str) -> WorkflowStep:
        """Get a workflow step by ID or raise ``AdCPTaskNotFoundError``.

        Collapses the task fetch-and-raise guard shared by get_task/complete_task.
        No ``context`` parameter by design: those tools carry the FastMCP transport
        ``Context``, not an AdCP ``ContextObject``, so the task not-found envelope
        stays context-less rather than echoing a transport object into a repository.
        """
        step = self.get_by_step_id(step_id)
        if step is None:
            from src.core.exceptions import AdCPTaskNotFoundError

            raise AdCPTaskNotFoundError(f"Task {step_id} not found")
        return step

    def list_by_tenant(
        self,
        *,
        status: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[WorkflowStep]:
        """List workflow steps for the tenant, with optional filters.

        Args:
            status: Filter by step status (e.g., "pending", "requires_approval").
            object_type: Filter by associated object type (e.g., "media_buy").
            object_id: Filter by specific object ID (requires object_type).
            offset: Number of steps to skip.
            limit: Maximum number of steps to return.
        """
        stmt = (
            select(WorkflowStep)
            .join(DBContext)
            .where(
                DBContext.tenant_id == self._tenant_id,
            )
        )

        if status:
            stmt = stmt.where(WorkflowStep.status == status)

        if object_type and object_id:
            stmt = stmt.join(ObjectWorkflowMapping).where(
                ObjectWorkflowMapping.object_type == object_type,
                ObjectWorkflowMapping.object_id == object_id,
            )
        elif object_type:
            stmt = stmt.join(ObjectWorkflowMapping).where(
                ObjectWorkflowMapping.object_type == object_type,
            )

        stmt = stmt.order_by(WorkflowStep.created_at.desc()).offset(offset).limit(limit)
        return list(self._session.scalars(stmt).all())

    def count_by_tenant(
        self,
        *,
        status: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
    ) -> int:
        """Count workflow steps matching the given filters.

        Uses the same filter logic as list_by_tenant but returns only the count.
        """
        stmt = (
            select(WorkflowStep)
            .join(DBContext)
            .where(
                DBContext.tenant_id == self._tenant_id,
            )
        )

        if status:
            stmt = stmt.where(WorkflowStep.status == status)

        if object_type and object_id:
            stmt = stmt.join(ObjectWorkflowMapping).where(
                ObjectWorkflowMapping.object_type == object_type,
                ObjectWorkflowMapping.object_id == object_id,
            )
        elif object_type:
            stmt = stmt.join(ObjectWorkflowMapping).where(
                ObjectWorkflowMapping.object_type == object_type,
            )

        result = self._session.scalar(select(func.count()).select_from(stmt.subquery()))
        return result or 0

    # ------------------------------------------------------------------
    # ObjectWorkflowMapping reads
    # ------------------------------------------------------------------

    def get_latest_mapping_for_object(self, object_type: str, object_id: str) -> ObjectWorkflowMapping | None:
        """Get the most recent workflow mapping for a specific object within the tenant."""
        return self._session.scalars(
            select(ObjectWorkflowMapping)
            .join(WorkflowStep, ObjectWorkflowMapping.step_id == WorkflowStep.step_id)
            .join(DBContext, WorkflowStep.context_id == DBContext.context_id)
            .where(
                ObjectWorkflowMapping.object_type == object_type,
                ObjectWorkflowMapping.object_id == object_id,
                DBContext.tenant_id == self._tenant_id,
            )
            .order_by(ObjectWorkflowMapping.created_at.desc())
        ).first()

    def get_step_by_id(self, step_id: str) -> WorkflowStep | None:
        """Alias of :meth:`get_by_step_id` (identical tenant-scoped lookup).

        Retained for the admin/service callers that use this name; delegates so
        the query lives in exactly one place.
        """
        return self.get_by_step_id(step_id)

    def get_mappings_for_step(self, step_id: str) -> list[ObjectWorkflowMapping]:
        """Get all object mappings for a workflow step within the tenant."""
        return list(
            self._session.scalars(
                select(ObjectWorkflowMapping)
                .join(WorkflowStep, ObjectWorkflowMapping.step_id == WorkflowStep.step_id)
                .join(DBContext, WorkflowStep.context_id == DBContext.context_id)
                .where(
                    ObjectWorkflowMapping.step_id == step_id,
                    DBContext.tenant_id == self._tenant_id,
                )
            ).all()
        )

    def get_mappings_for_steps(self, step_ids: list[str]) -> dict[str, list[ObjectWorkflowMapping]]:
        """Get object mappings for multiple workflow steps within the tenant.

        Returns a dict mapping step_id -> list of ObjectWorkflowMapping.
        """
        if not step_ids:
            return {}

        mappings = list(
            self._session.scalars(
                select(ObjectWorkflowMapping)
                .join(WorkflowStep, ObjectWorkflowMapping.step_id == WorkflowStep.step_id)
                .join(DBContext, WorkflowStep.context_id == DBContext.context_id)
                .where(
                    ObjectWorkflowMapping.step_id.in_(step_ids),
                    DBContext.tenant_id == self._tenant_id,
                )
            ).all()
        )

        result: dict[str, list[ObjectWorkflowMapping]] = {sid: [] for sid in step_ids}
        for mapping in mappings:
            result[mapping.step_id].append(mapping)
        return result

    def get_all_steps(self, *, limit: int | None = None) -> list[WorkflowStep]:
        """Get all workflow steps for this tenant, newest first."""
        stmt = (
            select(WorkflowStep)
            .join(DBContext)
            .where(DBContext.tenant_id == self._tenant_id)
            .order_by(WorkflowStep.created_at.desc())
        )
        if limit:
            stmt = stmt.limit(limit)
        return list(self._session.scalars(stmt).all())

    # ------------------------------------------------------------------
    # ObjectWorkflowMapping writes
    # ------------------------------------------------------------------

    def add_mapping(
        self,
        *,
        step_id: str,
        object_type: str,
        object_id: str,
        action: str,
    ) -> ObjectWorkflowMapping:
        """Create and add an ObjectWorkflowMapping to the session.

        Does NOT commit — the caller (or UoW) handles that.
        """
        mapping = ObjectWorkflowMapping(
            step_id=step_id,
            object_type=object_type,
            object_id=object_id,
            action=action,
        )
        self._session.add(mapping)
        return mapping

    # ------------------------------------------------------------------
    # Principal reads (for audit logging)
    # ------------------------------------------------------------------

    def get_principal_name(self, principal_id: str) -> str | None:
        """Look up a principal's display name within the tenant.

        Returns the name string, or None if the principal is not found.
        """
        principal = self._session.scalars(
            select(Principal).filter_by(
                tenant_id=self._tenant_id,
                principal_id=principal_id,
            )
        ).first()
        return principal.name if principal else None

    # ------------------------------------------------------------------
    # WorkflowStep writes
    # ------------------------------------------------------------------

    @staticmethod
    def resolve_tenant_for_step(session: Session, step_id: str) -> str | None:
        """Resolve a step's tenant from its Context (repository owns this join).

        Lets callers that lack a tenant scope up front (e.g. ContextManager) build a
        tenant-scoped repository without issuing a raw ``WorkflowStep``/``DBContext``
        query outside the repository layer. Returns None when the step (or its
        context) does not exist. Read-only; does not commit.
        """
        return session.scalar(
            select(DBContext.tenant_id)
            .join(WorkflowStep, WorkflowStep.context_id == DBContext.context_id)
            .where(WorkflowStep.step_id == step_id)
        )

    def _atomic_transition(
        self,
        step_id: str,
        *,
        status: str,
        status_guard: Any,
        completed_at: datetime | None = None,
        response_data: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> WorkflowStep | None:
        """Shared atomic conditional transition: set ``status`` IFF ``status_guard`` holds.

        ONE conditional UPDATE (tenant-scoped ``WHERE step_id … AND <status_guard>``)
        with ``RETURNING`` — the single-statement re-evaluation against committed state
        is what makes competing writers safe in either ordering. ``status_guard`` is a
        SQLAlchemy predicate on ``WorkflowStep.status`` (e.g. NOT IN terminal, or IN
        cancellable). Returns the re-loaded step, or None when no row matched (step
        absent or its status failed the guard). Does NOT commit.
        """
        values: dict[str, Any] = {"status": status}
        if completed_at is not None:
            values["completed_at"] = completed_at
        if response_data is not None:
            values["response_data"] = response_data
        if error_message is not None:
            values["error_message"] = error_message
        elif status == "completed":
            # Clear error message on successful completion.
            values["error_message"] = None

        scoped_step_ids = (
            select(WorkflowStep.step_id)
            .join(DBContext)
            .where(
                WorkflowStep.step_id == step_id,
                DBContext.tenant_id == self._tenant_id,
            )
        )
        # returning() makes the DML yield rows, so success is observable without
        # the CursorResult.rowcount attribute (untyped on Session.execute's Result).
        # synchronize_session="fetch" keeps any already-loaded ORM copy consistent
        # so callers that further mutate the returned step see the new status.
        updated = (
            self._session.execute(
                update(WorkflowStep)
                .where(WorkflowStep.step_id.in_(scoped_step_ids), status_guard)
                .values(**values)
                .returning(WorkflowStep.step_id)
                .execution_options(synchronize_session="fetch")
            )
            .scalars()
            .first()
        )
        if updated is None:
            return None
        return self.get_by_step_id(step_id)

    def transition_if_nonterminal(
        self,
        step_id: str,
        *,
        status: str,
        completed_at: datetime | None = None,
        response_data: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> WorkflowStep | None:
        """Atomically set a step's status IFF it is not already terminal.

        The SINGLE terminal-transition primitive shared by every competing writer
        (admin approve/reject, background approval, manual complete): a terminal
        workflow step (completed/rejected/failed/canceled) is IMMUTABLE. The FIRST
        committed writer wins and no later writer — in either ordering — can
        overwrite a committed terminal decision.

        Returns the updated step (re-loaded) or None when it does not exist OR is
        already terminal (write refused). Does NOT commit.
        """
        return self._atomic_transition(
            step_id,
            status=status,
            status_guard=WorkflowStep.status.not_in(TERMINAL_STEP_STATUSES),
            completed_at=completed_at,
            response_data=response_data,
            error_message=error_message,
        )

    def update_status(
        self,
        step_id: str,
        *,
        status: str,
        completed_at: datetime | None = None,
        response_data: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> WorkflowStep | None:
        """Terminal-immutable status update — see ``transition_if_nonterminal``.

        Returns the updated step, or None if the step does not exist OR is already
        terminal (the write is refused rather than overwriting a terminal decision).
        Does NOT commit — the caller handles that.
        """
        return self.transition_if_nonterminal(
            step_id,
            status=status,
            completed_at=completed_at,
            response_data=response_data,
            error_message=error_message,
        )

    def claim_approval(self, step_id: str) -> WorkflowStep | None:
        """Atomically claim a step for approval: requires_approval/pending_approval → approved.

        A compare-and-set restricted to APPROVABLE_STEP_STATUSES. Because ``approved`` is
        (deliberately) NON-terminal, the broad ``transition_if_nonterminal`` guard would let a
        SECOND concurrent approver win an ``approved → approved`` no-op and also run
        ``execute_approved_media_buy`` — duplicating irreversible adapter work. This narrower
        source-state guard makes exactly ONE approver win; a later approver sees ``approved``
        (not in the source set) and gets None. Returns the updated step, or None when the step
        is absent OR not in an approvable status (already approved/executing/terminal). Does
        NOT commit.
        """
        return self._atomic_transition(
            step_id,
            status="approved",
            status_guard=WorkflowStep.status.in_(APPROVABLE_STEP_STATUSES),
        )

    def reject_if_approvable(
        self,
        step_id: str,
        *,
        error_message: str | None = None,
        response_data: dict[str, Any] | None = None,
    ) -> WorkflowStep | None:
        """Atomically reject a step awaiting a decision: requires_approval/pending_approval → rejected.

        Mirror of ``claim_approval`` with the SAME source-state guard, so a step that has
        already been ``approved`` (irreversible execution underway) cannot be rejected — which
        would otherwise strand a live ad-server order behind a rejected workflow. Returns the
        updated step, or None when the step is absent OR not in an approvable status. Does NOT
        commit.
        """
        return self._atomic_transition(
            step_id,
            status="rejected",
            status_guard=WorkflowStep.status.in_(APPROVABLE_STEP_STATUSES),
            error_message=error_message,
            response_data=response_data,
        )

    def cancel_if_cancellable(self, step_id: str, *, completed_at: datetime) -> bool:
        """Atomically cancel a step IFF it is in a CANCELLABLE status.

        The buyer-facing cancel primitive (A2A ``tasks/cancel``). It refuses to cancel a step
        once irreversible external work has begun — CANCELLABLE_STEP_STATUSES excludes both
        ``approved`` (admin-approve commits it before order creation) and ``in_progress`` (the
        create/update paths persist it before their adapter side-effects), as well as all
        terminal states — so a cancel can never strand a real order behind a canceled task. The
        atomicity is a single conditional UPDATE (``_atomic_transition``): the guard is
        re-evaluated against committed state, so competing writers are safe in either ordering.
        Returns True when canceled, False when the step is absent or not in a cancellable status.
        Does NOT commit.
        """
        return (
            self._atomic_transition(
                step_id,
                status="canceled",
                status_guard=WorkflowStep.status.in_(CANCELLABLE_STEP_STATUSES),
                completed_at=completed_at,
            )
            is not None
        )
