"""MediaBuy repository — tenant-scoped data access for media buys and packages.

Core invariant: every query includes tenant_id in the WHERE clause. The tenant_id
is set at construction time and injected into all queries automatically.

Cross-tenant queries (for schedulers) use class methods that explicitly accept a
session and do not enforce tenant isolation — these are system-level operations.

beads: salesagent-t735 (foundation), salesagent-2lp8 (epic), salesagent-to9i (admin/scheduler migration),
       salesagent-dyb6 (write methods)
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, joinedload

from src.core.database.models import MediaBuy, MediaPackage


class MediaBuyRepository:
    """Tenant-scoped data access for MediaBuy and MediaPackage.

    All queries filter by tenant_id automatically. Callers cannot bypass
    tenant isolation — there is no way to query across tenants.

    Write methods add objects to the session but never commit — the Unit of Work
    (MediaBuyUoW) handles commit/rollback at the boundary.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    _MEDIA_BUY_IMMUTABLE_FIELDS: frozenset[str] = frozenset({"tenant_id", "media_buy_id", "created_at"})
    _PACKAGE_IMMUTABLE_FIELDS: frozenset[str] = frozenset({"media_buy_id", "package_id"})

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    # ------------------------------------------------------------------
    # Single MediaBuy lookups
    # ------------------------------------------------------------------

    def get_by_id(self, media_buy_id: str) -> MediaBuy | None:
        """Get a media buy by its ID within the tenant."""
        return self._session.scalars(
            select(MediaBuy).where(
                MediaBuy.tenant_id == self._tenant_id,
                MediaBuy.media_buy_id == media_buy_id,
            )
        ).first()

    def find_by_idempotency_key(self, idempotency_key: str, principal_id: str) -> MediaBuy | None:
        """Find an existing media buy by idempotency_key within (tenant, principal).

        Per adcp 3.12 spec: if a request with the same idempotency_key and account
        has already been processed, return the existing media buy.
        """
        return self._session.scalars(
            select(MediaBuy).where(
                MediaBuy.tenant_id == self._tenant_id,
                MediaBuy.principal_id == principal_id,
                MediaBuy.idempotency_key == idempotency_key,
            )
        ).first()

    def get_by_id_or_idempotency_key(self, identifier: str, principal_id: str) -> MediaBuy | None:
        """Get a media buy by ID first, then fall back to idempotency_key."""
        result = self.get_by_id(identifier)
        if result is None:
            result = self.find_by_idempotency_key(identifier, principal_id)
        return result

    def get_by_external_id(self, external_id: str) -> MediaBuy | None:
        """Get a media buy by adapter-side ID (e.g. GAM order ID)."""
        return self._session.scalars(
            select(MediaBuy).where(
                MediaBuy.tenant_id == self._tenant_id,
                MediaBuy.external_id == external_id,
            )
        ).first()

    def list_external_ids_for_source(self, source: str, external_ids: list[str]) -> set[str]:
        """Return the subset of ``external_ids`` that already have a row
        with the given ``source`` within this tenant.

        Used by the GAM projection to skip orders that have already been
        materialized so a buyer doesn't see duplicates.
        """
        if not external_ids:
            return set()
        rows = self._session.scalars(
            select(MediaBuy.external_id).where(
                MediaBuy.tenant_id == self._tenant_id,
                MediaBuy.source == source,
                MediaBuy.external_id.in_(external_ids),
            )
        ).all()
        return {r for r in rows if r is not None}

    def get_by_id_or_external_id(self, identifier: str) -> MediaBuy | None:
        """Resolve a media buy by canonical ``media_buy_id`` or by ``external_id``.

        Lookup convergence — buyers can pass either form. Native AdCP buys
        carry an ``mb_<uuid>`` PK plus an ``external_id`` populated after the
        adapter assigns one; imported buys use ``gam_<order_id>`` for both
        the PK (with the prefix) and the ``external_id`` (without).
        """
        result = self.get_by_id(identifier)
        if result is None:
            result = self.get_by_external_id(identifier)
        return result

    # ------------------------------------------------------------------
    # List queries
    # ------------------------------------------------------------------

    def get_by_principal(
        self,
        principal_id: str,
        *,
        media_buy_ids: list[str] | None = None,
        statuses: list[str] | None = None,
    ) -> list[MediaBuy]:
        """Get media buys for a principal within the tenant.

        Filters are combined with AND. Pass None to skip a filter.
        """
        stmt = select(MediaBuy).where(
            MediaBuy.tenant_id == self._tenant_id,
            MediaBuy.principal_id == principal_id,
        )
        if media_buy_ids is not None:
            stmt = stmt.where(MediaBuy.media_buy_id.in_(media_buy_ids))
        if statuses is not None:
            stmt = stmt.where(MediaBuy.status.in_(statuses))
        return list(self._session.scalars(stmt).all())

    def get_active(self) -> list[MediaBuy]:
        """Get all active media buys for the tenant."""
        return list(
            self._session.scalars(
                select(MediaBuy).where(
                    MediaBuy.tenant_id == self._tenant_id,
                    MediaBuy.status.in_(["active", "approved"]),
                )
            ).all()
        )

    # ------------------------------------------------------------------
    # Package queries — tenant isolation through MediaBuy FK join
    # ------------------------------------------------------------------

    def get_packages(self, media_buy_id: str) -> list[MediaPackage]:
        """Get all packages for a media buy, verified to belong to this tenant.

        Joins through MediaBuy to enforce tenant isolation — MediaPackage has
        no tenant_id column, so we verify via the parent MediaBuy.
        """
        return list(
            self._session.scalars(
                select(MediaPackage)
                .join(MediaBuy, MediaPackage.media_buy_id == MediaBuy.media_buy_id)
                .where(
                    MediaBuy.tenant_id == self._tenant_id,
                    MediaPackage.media_buy_id == media_buy_id,
                )
            ).all()
        )

    def get_package(self, media_buy_id: str, package_id: str) -> MediaPackage | None:
        """Get a specific package, verified to belong to this tenant."""
        return self._session.scalars(
            select(MediaPackage)
            .join(MediaBuy, MediaPackage.media_buy_id == MediaBuy.media_buy_id)
            .where(
                MediaBuy.tenant_id == self._tenant_id,
                MediaPackage.media_buy_id == media_buy_id,
                MediaPackage.package_id == package_id,
            )
        ).first()

    def get_packages_for_ids(self, media_buy_ids: list[str]) -> dict[str, list[MediaPackage]]:
        """Get packages for multiple media buys, grouped by media_buy_id.

        Only returns packages for media buys belonging to this tenant.
        Media buy IDs not belonging to this tenant are silently excluded.
        """
        if not media_buy_ids:
            return {}

        packages = self._session.scalars(
            select(MediaPackage)
            .join(MediaBuy, MediaPackage.media_buy_id == MediaBuy.media_buy_id)
            .where(
                MediaBuy.tenant_id == self._tenant_id,
                MediaPackage.media_buy_id.in_(media_buy_ids),
            )
        ).all()

        result: dict[str, list[MediaPackage]] = {}
        for pkg in packages:
            result.setdefault(pkg.media_buy_id, []).append(pkg)
        return result

    def find_package_with_media_buy(self, package_id: str) -> tuple[MediaPackage, MediaBuy] | None:
        """Find a package and its parent media buy by package_id within the tenant.

        Useful when you only have a package_id and need to resolve the parent
        media buy (e.g. during creative-to-package assignment).

        Returns (MediaPackage, MediaBuy) tuple or None if not found.
        """
        result = self._session.execute(
            select(MediaPackage, MediaBuy)
            .join(MediaBuy, MediaPackage.media_buy_id == MediaBuy.media_buy_id)
            .where(
                MediaPackage.package_id == package_id,
                MediaBuy.tenant_id == self._tenant_id,
            )
        ).first()
        if result is None:
            return None
        return result[0], result[1]

    # ------------------------------------------------------------------
    # Tenant-wide list queries (for admin/dashboard)
    # ------------------------------------------------------------------

    def list_all(self) -> list[MediaBuy]:
        """Get all media buys for the tenant."""
        return list(self._session.scalars(select(MediaBuy).where(MediaBuy.tenant_id == self._tenant_id)).all())

    def list_by_statuses(self, statuses: list[str]) -> list[MediaBuy]:
        """Get media buys for the tenant filtered by status list."""
        return list(
            self._session.scalars(
                select(MediaBuy).where(
                    MediaBuy.tenant_id == self._tenant_id,
                    MediaBuy.status.in_(statuses),
                )
            ).all()
        )

    def list_recent(
        self,
        limit: int = 10,
        *,
        eager_load_principal: bool = False,
    ) -> list[MediaBuy]:
        """Get the most recent media buys for the tenant, ordered by created_at desc."""
        stmt = (
            select(MediaBuy)
            .where(
                MediaBuy.tenant_id == self._tenant_id,
                MediaBuy.media_buy_id.isnot(None),
            )
            .order_by(MediaBuy.created_at.desc())
            .limit(limit)
        )
        if eager_load_principal:
            stmt = stmt.options(joinedload(MediaBuy.principal))
        return list(self._session.scalars(stmt).all())

    def list_in_flight_on_date(
        self,
        target_date: datetime.date,
        statuses: list[str] | None = None,
    ) -> list[MediaBuy]:
        """Get media buys whose flight period covers target_date.

        Useful for revenue trend calculations.
        """
        stmt = select(MediaBuy).where(
            MediaBuy.tenant_id == self._tenant_id,
            MediaBuy.start_date <= target_date,
            MediaBuy.end_date >= target_date,
        )
        if statuses:
            stmt = stmt.where(MediaBuy.status.in_(statuses))
        return list(self._session.scalars(stmt).all())

    def list_all_ordered_by_created(self) -> list[MediaBuy]:
        """Get all media buys for the tenant, ordered by created_at desc."""
        return list(
            self._session.scalars(
                select(MediaBuy).where(MediaBuy.tenant_id == self._tenant_id).order_by(MediaBuy.created_at.desc())
            ).all()
        )

    def list_filtered_with_cursor(
        self,
        *,
        status: str | None = None,
        principal_id: str | None = None,
        from_date: datetime.date | None = None,
        to_date: datetime.date | None = None,
        cursor_created_at: datetime.datetime | None = None,
        cursor_id: str | None = None,
        limit: int = 50,
    ) -> list[MediaBuy]:
        """List media buys for ``GET /media-buys`` drill-down.

        Sort: ``created_at desc, media_buy_id desc``. Cursor pagination uses
        the same ``(created_at, id)`` tuple pattern as audit log + sync
        history so concurrent inserts can't skip or duplicate rows.

        ``from_date``/``to_date`` filter on ``start_date`` (the flight start),
        not ``created_at`` — buyers ask "what was running in this window".
        """
        stmt = select(MediaBuy).where(MediaBuy.tenant_id == self._tenant_id)

        if status:
            stmt = stmt.where(MediaBuy.status == status)
        if principal_id:
            stmt = stmt.where(MediaBuy.principal_id == principal_id)
        if from_date:
            stmt = stmt.where(MediaBuy.start_date >= from_date)
        if to_date:
            stmt = stmt.where(MediaBuy.start_date <= to_date)

        if cursor_created_at is not None and cursor_id is not None:
            stmt = stmt.where(
                or_(
                    MediaBuy.created_at < cursor_created_at,
                    and_(
                        MediaBuy.created_at == cursor_created_at,
                        MediaBuy.media_buy_id < cursor_id,
                    ),
                )
            )

        stmt = stmt.order_by(MediaBuy.created_at.desc(), MediaBuy.media_buy_id.desc()).limit(limit)
        return list(self._session.scalars(stmt).all())

    # ------------------------------------------------------------------
    # MediaBuy writes
    # ------------------------------------------------------------------

    def create_from_request(
        self,
        *,
        media_buy_id: str,
        req: Any,
        principal_id: str,
        advertiser_name: str,
        budget: Decimal | float,
        currency: str,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
        status: str = "draft",
        order_name: str | None = None,
        campaign_objective: str | None = None,
        kpi_goal: str | None = None,
        package_id_map: dict[int, str] | None = None,
        by_alias: bool = False,
        created_at: datetime.datetime | None = None,
        account_id: str | None = None,
    ) -> MediaBuy:
        """Create a MediaBuy from a request model, serializing raw_request at the DB boundary.

        This is the preferred method for creating media buys from _impl functions.
        The request model is serialized here (not in business logic) per the
        no-model-dump-in-impl architectural principle.

        Args:
            media_buy_id: Unique media buy identifier.
            req: CreateMediaBuyRequest Pydantic model (serialized here, not by caller).
            principal_id: Principal ID for ownership.
            advertiser_name: Display name of the advertiser.
            budget: Total budget for the media buy.
            currency: Currency code (e.g., "USD").
            start_time: Campaign start time.
            end_time: Campaign end time.
            status: Initial status (default: "draft").
            order_name: Order name (defaults to req.po_number or "Order-{id}").
            campaign_objective: Optional campaign objective.
            kpi_goal: Optional KPI goal.
            package_id_map: Map of package index → package_id to inject into serialized packages.
            by_alias: Whether to serialize with field aliases (e.g., content_uri).
            created_at: Optional explicit created_at timestamp.

        Returns:
            The created MediaBuy ORM object (added to session, not committed).
        """
        raw = req.model_dump(mode="json", by_alias=by_alias)
        if package_id_map:
            packages = raw.get("packages", [])
            for idx, pkg_id in package_id_map.items():
                if idx < len(packages):
                    packages[idx]["package_id"] = pkg_id

        kwargs: dict[str, Any] = {
            "media_buy_id": media_buy_id,
            "tenant_id": self._tenant_id,
            "principal_id": principal_id,
            "idempotency_key": getattr(req, "idempotency_key", None),
            "order_name": order_name or getattr(req, "po_number", None) or f"Order-{media_buy_id}",
            "advertiser_name": advertiser_name,
            "budget": budget,
            "currency": currency,
            "start_date": start_time.date(),
            "end_date": end_time.date(),
            "start_time": start_time,
            "end_time": end_time,
            "status": status,
            "raw_request": raw,
        }
        if campaign_objective is not None:
            kwargs["campaign_objective"] = campaign_objective
        if kpi_goal is not None:
            kwargs["kpi_goal"] = kpi_goal
        if created_at is not None:
            kwargs["created_at"] = created_at
        if account_id is not None:
            kwargs["account_id"] = account_id

        media_buy = MediaBuy(**kwargs)
        self._session.add(media_buy)
        self._session.flush()
        return media_buy

    def create_from_gam_import(
        self,
        *,
        media_buy_id: str,
        principal_id: str,
        order_name: str,
        advertiser_name: str,
        budget: Decimal | None,
        currency: str,
        start_date: datetime.date,
        end_date: datetime.date,
        start_time: datetime.datetime | None,
        end_time: datetime.datetime | None,
        status: str,
        external_id: str,
        raw_request: dict,
    ) -> MediaBuy:
        """Create a media buy materialized from an imported GAM order.

        Marks the row with ``source='gam_import'`` and stamps
        ``external_id`` with the GAM order id so the buyer can update it
        later via either id form. Does NOT commit — UoW handles that.
        """
        media_buy = MediaBuy(
            media_buy_id=media_buy_id,
            tenant_id=self._tenant_id,
            principal_id=principal_id,
            order_name=order_name,
            advertiser_name=advertiser_name,
            budget=budget,
            currency=currency,
            start_date=start_date,
            end_date=end_date,
            start_time=start_time,
            end_time=end_time,
            status=status,
            raw_request=raw_request,
            source="gam_import",
            external_id=external_id,
        )
        self._session.add(media_buy)
        self._session.flush()
        return media_buy

    def create(self, media_buy: MediaBuy) -> MediaBuy:
        """Persist a new media buy within this tenant.

        The media_buy.tenant_id must match the repository's tenant_id.
        Raises ValueError if there is a tenant mismatch.

        Does NOT commit — the UoW handles that.
        """
        if media_buy.tenant_id != self._tenant_id:
            raise ValueError(
                f"Tenant mismatch: media_buy.tenant_id={media_buy.tenant_id!r} "
                f"!= repository tenant_id={self._tenant_id!r}"
            )
        self._session.add(media_buy)
        self._session.flush()
        return media_buy

    def update_status(
        self,
        media_buy_id: str,
        status: str,
        *,
        approved_at: datetime.datetime | None = None,
        approved_by: str | None = None,
    ) -> MediaBuy | None:
        """Update the status of a media buy within this tenant.

        Returns the updated MediaBuy, or None if not found in this tenant.
        """
        media_buy = self.get_by_id(media_buy_id)
        if media_buy is None:
            return None
        media_buy.status = status
        if approved_at is not None:
            media_buy.approved_at = approved_at
        if approved_by is not None:
            media_buy.approved_by = approved_by
        self._session.flush()
        return media_buy

    def update_fields(self, media_buy_id: str, **kwargs: Any) -> MediaBuy | None:
        """Update arbitrary fields on a media buy within this tenant.

        Only updates fields that are valid MediaBuy column attributes.
        Returns the updated MediaBuy, or None if not found in this tenant.
        Raises ValueError if any kwarg is not a valid MediaBuy attribute or
        if the caller attempts to update an immutable field (tenant_id,
        media_buy_id, created_at).
        """
        blocked = self._MEDIA_BUY_IMMUTABLE_FIELDS & kwargs.keys()
        if blocked:
            raise ValueError(f"Cannot update immutable field(s): {', '.join(sorted(blocked))}")
        media_buy = self.get_by_id(media_buy_id)
        if media_buy is None:
            return None
        for key, value in kwargs.items():
            if not hasattr(media_buy, key):
                raise ValueError(f"MediaBuy has no attribute {key!r}")
            setattr(media_buy, key, value)
        self._session.flush()
        return media_buy

    # ------------------------------------------------------------------
    # MediaPackage writes
    # ------------------------------------------------------------------

    def create_package(
        self,
        media_buy_id: str,
        package_id: str,
        package_config: dict,
        *,
        budget: Decimal | None = None,
        bid_price: Decimal | None = None,
        pacing: str | None = None,
    ) -> MediaPackage:
        """Create a new package for a media buy within this tenant.

        Verifies the parent media buy belongs to this tenant before creating.
        Raises ValueError if the media buy is not found in this tenant.
        """
        media_buy = self.get_by_id(media_buy_id)
        if media_buy is None:
            raise ValueError(f"MediaBuy {media_buy_id!r} not found in tenant {self._tenant_id!r}")
        package = MediaPackage(
            media_buy_id=media_buy_id,
            package_id=package_id,
            package_config=package_config,
            budget=budget,
            bid_price=bid_price,
            pacing=pacing,
        )
        self._session.add(package)
        self._session.flush()
        return package

    def update_package_config(
        self,
        media_buy_id: str,
        package_id: str,
        package_config: dict,
    ) -> MediaPackage | None:
        """Update the package_config of a package within this tenant.

        Returns the updated MediaPackage, or None if not found.
        """
        package = self.get_package(media_buy_id, package_id)
        if package is None:
            return None
        package.package_config = package_config
        self._session.flush()
        return package

    def update_package_fields(
        self,
        media_buy_id: str,
        package_id: str,
        **kwargs: Any,
    ) -> MediaPackage | None:
        """Update arbitrary fields on a package within this tenant.

        Only updates fields that are valid MediaPackage column attributes.
        Returns the updated MediaPackage, or None if not found.
        Raises ValueError if any kwarg is not a valid MediaPackage attribute or
        if the caller attempts to update an immutable field (media_buy_id,
        package_id).
        """
        blocked = self._PACKAGE_IMMUTABLE_FIELDS & kwargs.keys()
        if blocked:
            raise ValueError(f"Cannot update immutable field(s): {', '.join(sorted(blocked))}")
        package = self.get_package(media_buy_id, package_id)
        if package is None:
            return None
        for key, value in kwargs.items():
            if not hasattr(package, key):
                raise ValueError(f"MediaPackage has no attribute {key!r}")
            setattr(package, key, value)
        self._session.flush()
        return package

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def create_packages_bulk(
        self,
        media_buy_id: str,
        packages: list[MediaPackage],
    ) -> list[MediaPackage]:
        """Create multiple packages for a media buy within this tenant.

        Verifies the parent media buy belongs to this tenant before creating.
        Each package's media_buy_id must match the provided media_buy_id.
        Raises ValueError if the media buy is not found or if any package
        has a mismatched media_buy_id.
        """
        media_buy = self.get_by_id(media_buy_id)
        if media_buy is None:
            raise ValueError(f"MediaBuy {media_buy_id!r} not found in tenant {self._tenant_id!r}")
        for pkg in packages:
            if pkg.media_buy_id != media_buy_id:
                raise ValueError(
                    f"Package {pkg.package_id!r} has media_buy_id={pkg.media_buy_id!r} but expected {media_buy_id!r}"
                )
            self._session.add(pkg)
        self._session.flush()
        return packages

    # ------------------------------------------------------------------
    # Cross-tenant queries (for system-level schedulers)
    # ------------------------------------------------------------------

    @staticmethod
    def get_all_by_statuses(session: Session, statuses: list[str]) -> list[MediaBuy]:
        """Get media buys across ALL tenants filtered by status.

        This is a system-level query for schedulers that need to process
        media buys regardless of tenant. Not tenant-scoped.
        """
        return list(session.scalars(select(MediaBuy).where(MediaBuy.status.in_(statuses))).all())
