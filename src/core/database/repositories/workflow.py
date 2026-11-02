"""Workflow repository — tenant-scoped data access for workflow step tables.

Covers three ORM models:
- WorkflowStep: individual steps/tasks in a workflow
- ObjectWorkflowMapping: maps workflow steps to business objects
- Context (DBContext): conversation tracker for async operations

Core invariant: every query includes tenant_id in the WHERE clause (via Context join).
The tenant_id is set at construction time and injected into all queries automatically.

Write methods add objects to the session but never commit — the caller (or UoW)
handles commit/rollback at the boundary.

"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from typing import cast as type_cast

from sqlalchemy import func, literal, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from src.core.database.jsonb_append import jsonb_list_append
from src.core.database.models import Context as DBContext
from src.core.database.models import ObjectWorkflowMapping, Principal, WorkflowStep
from src.core.errors.details import EntityRefDetails


def build_context(
    session: Session,
    *,
    tenant_id: str,
    principal_id: str,
    initial_conversation: list[dict[str, Any]] | None = None,
) -> DBContext:
    """Construct a :class:`Context` row and add it to ``session``.

    The single construction site for a context row. Does NOT commit — the
    caller (a repository delegate, or ``ContextManager`` which keeps its own
    commit/refresh/expunge behaviour) owns the transaction boundary.

    issue: #2002
    """
    context = DBContext(
        context_id=f"ctx_{uuid.uuid4().hex[:12]}",
        tenant_id=tenant_id,
        principal_id=principal_id,
        conversation_history=initial_conversation or [],
        last_activity_at=datetime.now(UTC),
    )
    session.add(context)
    return context


def build_workflow_step(
    session: Session,
    *,
    context_id: str,
    step_type: str,
    owner: str,
    status: str = "pending",
    tool_name: str | None = None,
    request_data: dict[str, Any] | Any | None = None,
    response_data: dict[str, Any] | None = None,
    assigned_to: str | None = None,
    error_message: str | None = None,
    transaction_details: dict[str, Any] | None = None,
    object_mappings: list[dict[str, str]] | None = None,
    initial_comment: str | None = None,
    request_metadata: dict[str, Any] | None = None,
) -> WorkflowStep:
    """Construct a :class:`WorkflowStep` (+ any mappings) and add to ``session``.

    The single construction site for a workflow step row: the Pydantic
    boundary serialization, the ``request_metadata`` merge, the comments
    seeding and the ``completed_at`` rule all live here so that
    ``ContextManager`` and the repository cannot drift apart.

    ``step_id`` is generated here and returned on the instance, so a caller
    can pass it to :meth:`WorkflowRepository.add_mapping` with no read-back
    (a read-back would force a flush the caller may not want yet).

    Does NOT commit.

    issue: #2002
    """
    # Serialize Pydantic models at the DB boundary.
    from pydantic import BaseModel

    if isinstance(request_data, BaseModel):
        request_data = request_data.model_dump(mode="json")
    if request_metadata and request_data is not None:
        request_data.update(request_metadata)

    comments: list[dict[str, Any]] = []
    if initial_comment:
        comments.append({"user": "system", "timestamp": datetime.now(UTC).isoformat(), "text": initial_comment})

    step = WorkflowStep(
        step_id=f"step_{uuid.uuid4().hex[:12]}",
        context_id=context_id,
        step_type=step_type,
        owner=owner,
        status=status,
        tool_name=tool_name,
        request_data=request_data if request_data is not None else {},
        response_data=response_data if response_data is not None else {},
        assigned_to=assigned_to,
        error_message=error_message,
        transaction_details=transaction_details if transaction_details is not None else {},
        comments=comments,
        created_at=datetime.now(UTC),
    )
    if status == "completed":
        step.completed_at = datetime.now(UTC)

    session.add(step)

    if object_mappings:
        for mapping in object_mappings:
            session.add(
                ObjectWorkflowMapping(
                    object_type=mapping["object_type"],
                    object_id=mapping["object_id"],
                    step_id=step.step_id,
                    action=mapping.get("action", step_type),
                    created_at=datetime.now(UTC),
                )
            )

    return step


def append_step_comment(
    session: Session,
    step_id: str,
    *,
    user: str,
    text: str,
    tenant_id: str | None = None,
) -> int:
    """Atomically append a CommentModel-shaped comment to ``WorkflowStep.comments``.

    Single-statement JSONB append: concurrent comments serialize on the row
    lock instead of erasing each other (the old whole-list read-modify-write
    lost updates — salesagent-pgqs). The comment carries the canonical
    ``user``/``timestamp``/``text`` shape from ``CommentModel``
    (src/core/json_validators.py).

    ``tenant_id`` scopes the update through the Context join (DBContext) when
    the caller acts on behalf of a tenant; ``None`` is for trusted internal
    callers that hold a bare step_id. Returns the number of rows updated
    (0 = step missing, or not in this tenant).
    """
    elem = func.jsonb_build_object(
        "user",
        literal(user),
        "timestamp",
        literal(datetime.now(UTC).isoformat()),
        "text",
        literal(text),
    )
    stmt = (
        update(WorkflowStep)
        .where(WorkflowStep.step_id == step_id)
        .values(comments=jsonb_list_append(WorkflowStep.comments, elem))
        .execution_options(synchronize_session=False)
    )
    if tenant_id is not None:
        stmt = stmt.where(
            WorkflowStep.context_id.in_(select(DBContext.context_id).where(DBContext.tenant_id == tenant_id))
        )
    return type_cast("CursorResult[Any]", session.execute(stmt)).rowcount


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

    def append_comment(self, step_id: str, *, user: str, text: str) -> int:
        """Atomically append a comment to a step within the tenant.

        Delegates to :func:`append_step_comment` with this repository's tenant
        scope (Context join via DBContext). Returns rows updated.
        """
        return append_step_comment(self._session, step_id, user=user, text=text, tenant_id=self._tenant_id)

    def get_by_step_id(self, step_id: str, principal_id: str | None = None) -> WorkflowStep | None:
        """Get a workflow step by its ID within the tenant, optionally within a principal.

        ``principal_id`` narrows the lookup to the tasks that principal owns. It is OPTIONAL
        rather than required because the two internal callers must NOT be narrowed: the
        ``get_step_by_id`` alias serves admin/service callers acting as the publisher, and
        ``update_step_status`` is the system completing a task it does not own. Defaulting to
        None leaves both exactly as they were.

        The BUYER-facing path passes it, and must:
        protocol/get-task-status-status-request.json (AdCP 3.1.1) says "Sellers MUST return
        REFERENCE_NOT_FOUND for a task_id that exists only under a different account or
        principal", and the get_products_async storyboard grades it
        (step get_products_task_status_wrong_account, "Sellers MUST NOT reveal whether the
        task exists under another account or principal"). Tenant scoping alone let any
        authenticated principal read another principal's task in the same tenant, including
        its request_data and response_data (salesagent-prkv.85).
        """
        conditions = [
            WorkflowStep.step_id == step_id,
            DBContext.tenant_id == self._tenant_id,
        ]
        if principal_id is not None:
            conditions.append(DBContext.principal_id == principal_id)
        return self._session.scalars(select(WorkflowStep).join(DBContext).where(*conditions)).first()

    def get_by_step_id_or_raise(self, step_id: str, principal_id: str | None = None) -> WorkflowStep:
        """Get a workflow step by ID or raise ``AdCPTaskNotFoundError``.

        Collapses the task fetch-and-raise guard shared by get_task_status/complete_task.
        No ``context`` parameter by design: those tools carry the FastMCP transport
        ``Context``, not an AdCP ``ContextObject``, so the task not-found envelope
        stays context-less rather than echoing a transport object into a repository.

        ``principal_id`` narrows the lookup (see :meth:`get_by_step_id`). The error it
        raises is already the one the spec asks for -- AdCPTaskNotFoundError emits
        REFERENCE_NOT_FOUND -- and it is identical whether the task is absent or owned by
        someone else, which is what "MUST NOT reveal whether the task exists under another
        account or principal" requires.
        """
        step = self.get_by_step_id(step_id, principal_id=principal_id)
        if step is None:
            from src.core.exceptions import AdCPTaskNotFoundError

            raise AdCPTaskNotFoundError(details=EntityRefDetails(step_id=step_id))
        return step

    def list_by_tenant(
        self,
        *,
        status: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        offset: int = 0,
        limit: int = 20,
        principal_id: str | None = None,
    ) -> list[WorkflowStep]:
        """List workflow steps for the tenant, with optional filters.

        Args:
            status: Filter by step status (e.g., "pending", "requires_approval").
            object_type: Filter by associated object type (e.g., "media_buy").
            object_id: Filter by specific object ID (requires object_type).
            offset: Number of steps to skip.
            limit: Maximum number of steps to return.
            principal_id: Narrow to one principal's tasks. The BUYER-facing caller
                (list_tasks) passes it; the publisher-facing admin views do not, which is
                why it is optional. See :meth:`get_by_step_id` for the obligation.
        """
        stmt = (
            select(WorkflowStep)
            .join(DBContext)
            .where(
                DBContext.tenant_id == self._tenant_id,
            )
        )

        if principal_id is not None:
            stmt = stmt.where(DBContext.principal_id == principal_id)

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
        principal_id: str | None = None,
    ) -> int:
        """Count workflow steps matching the given filters.

        Uses the same filter logic as list_by_tenant but returns only the count. The two
        MUST be narrowed together: a count over the tenant beside a page scoped to one
        principal reports how many tasks the OTHER principals have, which is the same
        disclosure by arithmetic.
        """
        stmt = (
            select(WorkflowStep)
            .join(DBContext)
            .where(
                DBContext.tenant_id == self._tenant_id,
            )
        )

        if principal_id is not None:
            stmt = stmt.where(DBContext.principal_id == principal_id)

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
    # Context / WorkflowStep writes (no-commit; the UoW owns the boundary)
    # ------------------------------------------------------------------

    def create_context(
        self,
        *,
        principal_id: str,
        initial_conversation: list[dict[str, Any]] | None = None,
    ) -> DBContext:
        """Create a Context row inside the caller's transaction.

        Takes NO ``tenant_id``: it uses ``self._tenant_id``, so a caller
        cannot name another tenant's context. Does NOT commit.

        issue: #2002
        """
        return build_context(
            self._session,
            tenant_id=self._tenant_id,
            principal_id=principal_id,
            initial_conversation=initial_conversation,
        )

    def create_step(
        self,
        *,
        persistent_context: DBContext,
        **fields: Any,
    ) -> WorkflowStep:
        """Create a WorkflowStep row inside the caller's transaction.

        ``fields`` are forwarded verbatim to :func:`build_workflow_step`,
        which owns their names, defaults and types. This delegate deliberately
        does NOT re-enumerate them: doing so duplicated the twelve-parameter
        forwarding list that ``ContextManager.create_workflow_step`` already
        has, which the DRY ratchet (pylint R0801) correctly rejects.

        Takes the Context INSTANCE, never a bare ``context_id`` string. That
        is a correctness requirement, not a convenience: this method is the
        shared construction seam for call sites where the context id is
        BUYER-SUPPLIED (salesagent-n4vxk, salesagent-ft58z), and
        ``ContextManager.get_context`` resolves a context by id with NO tenant
        predicate. ``test_architecture_workflow_tenant_isolation.py`` matches
        only ``select()``/``session.get()`` and so cannot catch a
        construct-and-add write. Requiring the instance forces a caller
        holding a raw id to resolve it through a tenant-scoped read first.

        Raises ValueError if the context belongs to another tenant — it does
        not log-and-continue, because a cross-tenant write is not a
        degraded-service case.

        Does NOT commit.

        issue: #2002
        """
        if persistent_context.tenant_id != self._tenant_id:
            raise ValueError(
                f"Context {persistent_context.context_id} belongs to tenant {persistent_context.tenant_id}, "
                f"not {self._tenant_id} — refusing to attach a workflow step across tenants"
            )
        return build_workflow_step(self._session, context_id=persistent_context.context_id, **fields)

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

    def update_status(
        self,
        step_id: str,
        *,
        status: str,
        completed_at: datetime | None = None,
        response_data: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> WorkflowStep | None:
        """Update the status of a workflow step.

        Returns the updated step, or None if not found.
        Does NOT commit — the caller handles that.
        """
        step = self.get_by_step_id(step_id)
        if step is None:
            return None

        step.status = status
        if completed_at is not None:
            step.completed_at = completed_at
        if response_data is not None:
            step.response_data = response_data
        if error_message is not None:
            step.error_message = error_message
        elif status == "completed":
            # Clear error message on successful completion
            step.error_message = None

        self._session.flush()
        return step
