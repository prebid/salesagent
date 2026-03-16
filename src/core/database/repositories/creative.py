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

from src.core.database.models import (
    Creative,
    CreativeAssignment,
    CreativeReview,
    MediaBuy,
    MediaPackage,
    Principal,
    Product,
)

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

    def begin_nested(self):
        """Start a savepoint (nested transaction) for partial-success patterns."""
        return self._session.begin_nested()

    def commit(self) -> None:
        """Commit the current transaction."""
        self._session.commit()

    def create_review(self, review: CreativeReview) -> CreativeReview:
        """Persist a CreativeReview record within this tenant.

        The review.tenant_id must match the repository's tenant_id.
        Does NOT commit — the caller handles that.
        """
        if review.tenant_id != self._tenant_id:
            raise ValueError(
                f"Tenant mismatch: review.tenant_id={review.tenant_id!r} != repository tenant_id={self._tenant_id!r}"
            )
        self._session.add(review)
        return review

    def get_provenance_policies(self) -> list[dict]:
        """Get creative_policy dicts from products that require AI provenance.

        Returns list of creative_policy dicts where provenance_required is True.
        """
        tenant_products = self._session.scalars(select(Product).filter_by(tenant_id=self._tenant_id)).all()
        return [
            p.creative_policy
            for p in tenant_products
            if p.creative_policy and p.creative_policy.get("provenance_required")
        ]

    # ------------------------------------------------------------------
    # Cross-model lookups (shared by admin and _impl)
    # ------------------------------------------------------------------

    def get_principal_name(self, principal_id: str) -> str:
        """Look up principal name within the tenant, falling back to principal_id."""
        principal = self._session.scalars(
            select(Principal).filter_by(
                tenant_id=self._tenant_id,
                principal_id=principal_id,
            )
        ).first()
        return principal.name if principal else principal_id

    def get_prior_ai_review(self, creative_id: str) -> CreativeReview | None:
        """Get the most recent AI review for a creative within the tenant."""
        return self._session.scalars(
            select(CreativeReview)
            .filter_by(creative_id=creative_id, tenant_id=self._tenant_id, review_type="ai")
            .order_by(CreativeReview.reviewed_at.desc())
            .limit(1)
        ).first()

    # ------------------------------------------------------------------
    # Admin-specific lookups (no principal_id required)
    # Added for admin blueprint migration (salesagent-4tb)
    # ------------------------------------------------------------------

    def admin_get_by_id(self, creative_id: str) -> Creative | None:
        """Get a creative by its ID within the tenant (admin use — no principal filter)."""
        return self._session.scalars(
            select(Creative).where(
                Creative.tenant_id == self._tenant_id,
                Creative.creative_id == creative_id,
            )
        ).first()

    def admin_list_all(self) -> list[Creative]:
        """Get all creatives for the tenant ordered by status then date (admin use)."""
        return list(
            self._session.scalars(
                select(Creative)
                .filter_by(tenant_id=self._tenant_id)
                .order_by(Creative.status, Creative.created_at.desc())
            ).all()
        )

    def admin_get_by_ids(self, creative_ids: list[str]) -> list[Creative]:
        """Get multiple creatives by their IDs within the tenant (admin use)."""
        if not creative_ids:
            return []
        return list(
            self._session.scalars(
                select(Creative).where(
                    Creative.tenant_id == self._tenant_id,
                    Creative.creative_id.in_(creative_ids),
                )
            ).all()
        )


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

    def get_by_media_buy(self, media_buy_id: str) -> list[CreativeAssignment]:
        """Get all assignments for a media buy within the tenant."""
        return list(
            self._session.scalars(
                select(CreativeAssignment).where(
                    CreativeAssignment.tenant_id == self._tenant_id,
                    CreativeAssignment.media_buy_id == media_buy_id,
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

    # ------------------------------------------------------------------
    # Cross-model lookups (for assignment workflow)
    # ------------------------------------------------------------------

    def find_package_with_media_buy(self, package_id: str) -> tuple[MediaPackage, MediaBuy] | None:
        """Find a package and its parent media buy within the tenant.

        Delegates to MediaBuyRepository — all MediaPackage queries are owned by
        that repository per the no-raw-MediaPackage-select guard.

        Returns (MediaPackage, MediaBuy) tuple or None if not found.
        """
        from src.core.database.repositories.media_buy import MediaBuyRepository

        mb_repo = MediaBuyRepository(self._session, self._tenant_id)
        return mb_repo.find_package_with_media_buy(package_id)

    def get_creative_by_id(self, creative_id: str) -> Creative | None:
        """Get a creative by tenant + creative_id (no principal filter)."""
        return self._session.scalars(
            select(Creative).where(
                Creative.tenant_id == self._tenant_id,
                Creative.creative_id == creative_id,
            )
        ).first()

    def get_product_by_id(self, product_id: str) -> Product | None:
        """Get a product by tenant + product_id."""
        return self._session.scalars(
            select(Product).where(
                Product.tenant_id == self._tenant_id,
                Product.product_id == product_id,
            )
        ).first()

    def commit(self) -> None:
        """Commit the current transaction."""
        self._session.commit()
