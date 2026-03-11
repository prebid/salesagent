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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.database.models import Context as DBContext
from src.core.database.models import ObjectWorkflowMapping, Principal, WorkflowStep


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
        """Get a workflow step by its primary key within the tenant."""
        return self._session.scalars(
            select(WorkflowStep)
            .join(DBContext)
            .where(
                WorkflowStep.step_id == step_id,
                DBContext.tenant_id == self._tenant_id,
            )
        ).first()

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
