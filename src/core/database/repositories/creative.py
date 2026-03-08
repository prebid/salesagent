"""Creative repository — tenant-scoped data access for creatives and assignments.

Core invariant: every query includes tenant_id in the WHERE clause. The tenant_id
is set at construction time and injected into all queries automatically.

beads: salesagent-o9k4 (foundation)
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import NamedTuple, cast

from sqlalchemy import func, select
from sqlalchemy.orm import InstrumentedAttribute, Session, attributes

from src.core.database.models import Creative, CreativeAssignment, MediaBuy

logger = logging.getLogger(__name__)


class CreativeListResult(NamedTuple):
    """Result of a paginated creative listing query."""

    creatives: list[Creative]
    total_count: int


class CreativeRepository:
    """Tenant-scoped data access for Creative.

    All queries filter by tenant_id automatically. Callers cannot bypass
    tenant isolation.

    Write methods add objects to the session but never commit — the caller
    or Unit of Work handles commit/rollback at the boundary.

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
    # Single Creative lookups
    # ------------------------------------------------------------------

    def get_by_id(self, creative_id: str, principal_id: str) -> Creative | None:
        """Get a creative by its ID and principal within the tenant."""
        return self._session.scalars(
            select(Creative).where(
                Creative.tenant_id == self._tenant_id,
                Creative.principal_id == principal_id,
                Creative.creative_id == creative_id,
            )
        ).first()

    # ------------------------------------------------------------------
    # List queries
    # ------------------------------------------------------------------

    def get_by_principal(
        self,
        principal_id: str,
        *,
        status: str | None = None,
        format: str | None = None,
        tags: list[str] | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        search: str | None = None,
        media_buy_ids: list[str] | None = None,
        buyer_refs: list[str] | None = None,
        sort_by: str = "created_date",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 50,
    ) -> CreativeListResult:
        """Get creatives for a principal with filtering, sorting, and pagination.

        Returns a CreativeListResult with the matching creatives and total count.
        """
        # Build base query - filter by tenant AND principal for security
        stmt = select(Creative).filter_by(
            tenant_id=self._tenant_id,
            principal_id=principal_id,
        )

        # Filter out creatives without valid assets (legacy data)
        stmt = stmt.where(Creative.data["assets"].isnot(None))

        # Apply media_buy_ids filter via join
        if media_buy_ids:
            stmt = stmt.join(
                CreativeAssignment,
                Creative.creative_id == CreativeAssignment.creative_id,
            ).where(CreativeAssignment.media_buy_id.in_(media_buy_ids))

        # Apply buyer_refs filter via join
        if buyer_refs:
            if not media_buy_ids:
                stmt = stmt.join(
                    CreativeAssignment,
                    Creative.creative_id == CreativeAssignment.creative_id,
                )
            stmt = stmt.join(
                MediaBuy,
                CreativeAssignment.media_buy_id == MediaBuy.media_buy_id,
            ).where(MediaBuy.buyer_ref.in_(buyer_refs))

        if status:
            stmt = stmt.where(Creative.status == status)

        if format:
            stmt = stmt.where(Creative.format == format)

        if tags:
            for tag in tags:
                stmt = stmt.where(Creative.name.contains(tag))

        if created_after:
            stmt = stmt.where(Creative.created_at >= created_after)

        if created_before:
            stmt = stmt.where(Creative.created_at <= created_before)

        if search:
            search_term = f"%{search}%"
            stmt = stmt.where(Creative.name.ilike(search_term))

        # Get total count before pagination
        total_count_result = self._session.scalar(select(func.count()).select_from(stmt.subquery()))
        total_count = int(total_count_result) if total_count_result is not None else 0

        # Apply sorting
        sort_column: InstrumentedAttribute
        if sort_by == "name":
            sort_column = Creative.name
        elif sort_by == "status":
            sort_column = Creative.status
        else:
            sort_column = Creative.created_at

        if sort_order == "asc":
            stmt = stmt.order_by(sort_column.asc())
        else:
            stmt = stmt.order_by(sort_column.desc())

        # Apply pagination
        db_creatives = list(self._session.scalars(stmt.offset(offset).limit(limit)).all())

        return CreativeListResult(creatives=db_creatives, total_count=total_count)

    def list_by_principal(self, principal_id: str) -> list[Creative]:
        """Get all creatives for a principal within the tenant (no pagination)."""
        return list(
            self._session.scalars(
                select(Creative).filter_by(
                    tenant_id=self._tenant_id,
                    principal_id=principal_id,
                )
            ).all()
        )

    # ------------------------------------------------------------------
    # Creative writes
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        creative_id: str | None = None,
        name: str,
        agent_url: str,
        format: str,
        format_parameters: dict | None = None,
        principal_id: str,
        status: str = "pending",
        data: dict | None = None,
    ) -> Creative:
        """Create a new creative within this tenant.

        Generates a creative_id if not provided.
        Does NOT commit - the caller handles that.
        """
        db_creative = Creative(
            tenant_id=self._tenant_id,
            creative_id=creative_id or str(uuid.uuid4()),
            name=name,
            agent_url=agent_url,
            format=format,
            format_parameters=cast(dict | None, format_parameters),
            principal_id=principal_id,
            status=status,
            created_at=datetime.now(UTC),
            data=data or {},
        )
        self._session.add(db_creative)
        self._session.flush()
        return db_creative

    def update_data(self, creative: Creative, data: dict) -> None:
        """Update the JSONB data field on a creative and flag it as modified."""
        creative.data = data
        attributes.flag_modified(creative, "data")

    def flush(self) -> None:
        """Flush pending changes to the database without committing."""
        self._session.flush()


class CreativeAssignmentRepository:
    """Tenant-scoped data access for CreativeAssignment.

    All queries filter by tenant_id automatically.

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
    # Lookups
    # ------------------------------------------------------------------

    def get_by_creative(self, creative_id: str) -> list[CreativeAssignment]:
        """Get all assignments for a creative within the tenant."""
        return list(
            self._session.scalars(
                select(CreativeAssignment).where(
                    CreativeAssignment.tenant_id == self._tenant_id,
                    CreativeAssignment.creative_id == creative_id,
                )
            ).all()
        )

    def get_by_package(self, package_id: str) -> list[CreativeAssignment]:
        """Get all assignments for a package within the tenant."""
        return list(
            self._session.scalars(
                select(CreativeAssignment).where(
                    CreativeAssignment.tenant_id == self._tenant_id,
                    CreativeAssignment.package_id == package_id,
                )
            ).all()
        )

    def get_existing(
        self,
        media_buy_id: str,
        package_id: str,
        creative_id: str,
    ) -> CreativeAssignment | None:
        """Get an existing assignment by its unique composite key."""
        return self._session.scalars(
            select(CreativeAssignment).filter_by(
                tenant_id=self._tenant_id,
                media_buy_id=media_buy_id,
                package_id=package_id,
                creative_id=creative_id,
            )
        ).first()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        media_buy_id: str,
        package_id: str,
        creative_id: str,
        principal_id: str | None = None,
        weight: int = 100,
    ) -> CreativeAssignment:
        """Create a new assignment within this tenant.

        Does NOT commit - the caller handles that.
        """
        assignment = CreativeAssignment(
            tenant_id=self._tenant_id,
            assignment_id=str(uuid.uuid4()),
            media_buy_id=media_buy_id,
            package_id=package_id,
            creative_id=creative_id,
            principal_id=principal_id,
            weight=weight,
            created_at=datetime.now(UTC),
        )
        self._session.add(assignment)
        return assignment

    def delete(self, assignment_id: str) -> bool:
        """Delete an assignment by its ID within this tenant.

        Returns True if deleted, False if not found.
        """
        assignment = self._session.scalars(
            select(CreativeAssignment).where(
                CreativeAssignment.tenant_id == self._tenant_id,
                CreativeAssignment.assignment_id == assignment_id,
            )
        ).first()
        if assignment is None:
            return False
        self._session.delete(assignment)
        return True
