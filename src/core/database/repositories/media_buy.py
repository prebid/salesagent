"""MediaBuy repository — tenant-scoped data access for media buys and packages.

Core invariant: every query includes tenant_id in the WHERE clause. The tenant_id
is set at construction time and injected into all queries automatically.

Cross-tenant queries (for schedulers) use class methods that explicitly accept a
session and do not enforce tenant isolation — these are system-level operations.

        (write methods)
"""

from __future__ import annotations

import datetime
from collections.abc import Collection
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, NoReturn

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from src.core.database.models import (
    MediaBuy,
    MediaPackage,
    PersistedMediaBuyStatus,
)

if TYPE_CHECKING:
    from adcp.types import ContextObject


def _to_decimal_or_none(value: Any) -> Decimal | None:
    """Coerce a legacy raw_request numeric to ``Decimal``, tolerantly.

    Rejects ``bool`` (an ``int`` subtype — a legacy ``True``/``False`` budget is
    not ``1``/``0``) and returns ``None`` on a malformed value instead of raising
    ``InvalidOperation`` (a 500) on exactly the untrusted pre-dual-write data
    this path exists to rescue.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


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

    # "revision" and "confirmed_at" are repository-managed — "revision" is bumped on
    # every successful mutation (see _bump_revision) and "confirmed_at" is stamped
    # write-once the first time the buy reaches a committed status (see
    # _stamp_confirmation_if_needed). Callers may never write either directly:
    # letting update_fields set confirmed_at would walk straight through the
    # write-once guard, because the stamp helper no-ops on an already-set value.
    _MEDIA_BUY_IMMUTABLE_FIELDS: frozenset[str] = frozenset(
        {"tenant_id", "media_buy_id", "created_at", "revision", "confirmed_at"}
    )
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

    def get_by_id_or_raise(
        self, media_buy_id: str, *, context: ContextObject | dict[str, Any] | None = None
    ) -> MediaBuy:
        """Get a media buy by ID or raise ``AdCPMediaBuyNotFoundError``.

        Collapses the "look up the media buy, raise the typed not-found if it
        does not exist" guard duplicated across the update tool into one place.
        ``context`` is echoed into the error envelope so buyer agents can
        correlate the failure. Coexists with ``get_by_id`` — callers that
        deliberately tolerate ``None`` keep using that.
        """
        media_buy = self.get_by_id(media_buy_id)
        if media_buy is None:
            from src.core.exceptions import AdCPMediaBuyNotFoundError

            raise AdCPMediaBuyNotFoundError(
                f"Media buy '{media_buy_id}' not found",
                suggestion="Verify the media_buy_id is correct and belongs to your account.",
                context=context,
            )
        return media_buy

    def find_by_idempotency_key(
        self, idempotency_key: str, principal_id: str, account_id: str | None = None
    ) -> MediaBuy | None:
        """Find an existing media buy by idempotency_key within (tenant, principal, account).

        The AdCP idempotency scope is (agent, account, key): the same key under a
        different account is an independent request, never a hit. ``account_id is
        None`` matches rows stored with no account (``IS NULL``), mirroring the
        NULLS NOT DISTINCT unique backstop index.
        """
        return self._session.scalars(
            select(MediaBuy).where(
                MediaBuy.tenant_id == self._tenant_id,
                MediaBuy.principal_id == principal_id,
                # SQLAlchemy renders ``== None`` as ``IS NULL`` — matches no-account rows.
                MediaBuy.account_id == account_id,
                MediaBuy.idempotency_key == idempotency_key,
            )
        ).first()

    def get_by_id_or_idempotency_key(
        self, identifier: str, principal_id: str, account_id: str | None = None
    ) -> MediaBuy | None:
        """Get a media buy by ID first, then fall back to idempotency_key.

        ``account_id`` scopes the idempotency-key fallback to the spec's
        (agent, account, key) tuple. It is threaded through to
        ``find_by_idempotency_key`` rather than dropped — otherwise the
        fallback would silently match only no-account (``IS NULL``) rows.
        """
        result = self.get_by_id(identifier)
        if result is None:
            result = self.find_by_idempotency_key(identifier, principal_id, account_id=account_id)
        return result

    # ------------------------------------------------------------------
    # List queries
    # ------------------------------------------------------------------

    def get_by_principal(
        self,
        principal_id: str,
        *,
        media_buy_ids: list[str] | None = None,
        statuses: list[PersistedMediaBuyStatus] | None = None,
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
        """Get a specific package row, verified to belong to this tenant.

        Pure read: returns the canonical ``media_packages`` row or ``None``.
        Packages recorded only in ``MediaBuy.raw_request`` (media buys created
        before the dual-write landed in 8367e0a1f — no backfill migration
        exists — or adapters that return an empty ``response.packages``) have
        no row and return ``None`` here. Callers that must tolerate those use
        ``package_exists_or_raise`` (read-only guard), ``get_package_config``
        (read-only config access), or ``materialize_package`` (write paths).
        """
        return self._session.scalars(
            select(MediaPackage)
            .join(MediaBuy, MediaPackage.media_buy_id == MediaBuy.media_buy_id)
            .where(
                MediaBuy.tenant_id == self._tenant_id,
                MediaPackage.media_buy_id == media_buy_id,
                MediaPackage.package_id == package_id,
            )
        ).first()

    def _raw_packages_by_id(self, media_buy_id: str) -> dict[str, dict[str, Any]]:
        """Map ``package_id -> raw_request package dict`` for a buy, in ONE load.

        Resolves ``MediaBuy.raw_request["packages"]`` with a single ``get_by_id``.
        ``_find_raw_package`` (and thus ``get_package_config`` /
        ``materialize_package``) and the bulk ``packages_exist_or_raise`` guard
        all read through here, so a guard checking k packages is one buy load
        plus O(1) membership per package rather than k full-buy reloads. First
        occurrence wins on a duplicate ``package_id``, matching the prior
        first-match lookup.
        """
        media_buy = self.get_by_id(media_buy_id)
        raw_packages = (media_buy.raw_request or {}).get("packages") if media_buy is not None else None
        result: dict[str, dict[str, Any]] = {}
        for raw_pkg in raw_packages or []:
            if isinstance(raw_pkg, dict):
                pid = raw_pkg.get("package_id")
                if pid is not None:
                    result.setdefault(pid, raw_pkg)
        return result

    def _find_raw_package(self, media_buy_id: str, package_id: str) -> dict[str, Any] | None:
        """Find a package dict in ``MediaBuy.raw_request`` (read-only)."""
        return self._raw_packages_by_id(media_buy_id).get(package_id)

    def get_package_config(self, media_buy_id: str, package_id: str) -> dict[str, Any] | None:
        """Read a package's config without requiring a canonical row.

        Returns the row's ``package_config`` when the row exists, else the
        raw_request package dict for raw_request-only packages, else ``None``.
        Read-only — safe on validation paths that run before the dry_run gate.
        """
        package = self.get_package(media_buy_id, package_id)
        if package is not None:
            return package.package_config
        return self._find_raw_package(media_buy_id, package_id)

    def package_exists_or_raise(
        self, media_buy_id: str, package_id: str, *, context: ContextObject | dict[str, Any] | None = None
    ) -> None:
        """Existence guard tolerant of raw_request-only packages, or raise.

        Read-only: unlike ``get_package_or_raise`` it never materializes a
        row, so it is safe on guards that run before the dry_run early return
        — a dry_run request must not write. The two helpers share the single
        raise site in ``_raise_package_not_found``.
        """
        if self.get_package(media_buy_id, package_id) is not None:
            return
        if self._find_raw_package(media_buy_id, package_id) is not None:
            return
        self._raise_package_not_found(media_buy_id, package_id, context)

    def packages_exist_or_raise(
        self, media_buy_id: str, package_ids: list[str], *, context: ContextObject | dict[str, Any] | None = None
    ) -> None:
        """Bulk existence guard for many packages under one buy, in one raw load.

        The pre-dry_run guard checks every referenced package. Looping the
        singular ``package_exists_or_raise`` would re-load the owning
        ``MediaBuy`` once per package (each ``_find_raw_package`` →
        ``get_by_id``); here the ``raw_request`` map is resolved ONCE and each
        package is an O(1) membership test on top of its indexed
        ``media_packages`` lookup. Read-only (never materializes), order-
        preserving (raises on the first missing package), and shares the single
        ``_raise_package_not_found`` site — so raw_request-only packages on
        legacy buys are tolerated identically to the singular guard.
        """
        if not package_ids:
            return
        raw_packages = self._raw_packages_by_id(media_buy_id)
        for package_id in package_ids:
            if self.get_package(media_buy_id, package_id) is not None:
                continue
            if package_id in raw_packages:
                continue
            self._raise_package_not_found(media_buy_id, package_id, context)

    def materialize_package(self, media_buy_id: str, package_id: str) -> MediaPackage | None:
        """Get the package row, materializing it from raw_request if absent.

        Resolves the ``media_packages``/``raw_request`` duality for WRITE
        paths: raw_request-only packages get a canonical row so row-needing
        operations (targeting mutation) behave identically on legacy buys.
        (Spec-Grounding: tolerating raw_request-only packages is a reasonable
        reading but ungraded — no storyboard covers pre-dual-write data.)

        WRITES on the raw_request-only path (``session.add`` + ``flush``; the
        Unit of Work owns the commit) — only call past the dry_run gate. The
        dedicated columns (budget/bid_price/pacing) are filled from the raw
        package to match the create path's dual-write.
        """
        package = self.get_package(media_buy_id, package_id)
        if package is not None:
            return package

        raw_pkg = self._find_raw_package(media_buy_id, package_id)
        if raw_pkg is None:
            return None

        package = self._build_package_row(
            media_buy_id,
            package_id,
            dict(raw_pkg),
            budget=raw_pkg.get("budget"),
            bid_price=raw_pkg.get("bid_price"),
        )
        self._session.add(package)
        self._session.flush()
        return package

    def create_package_from_config(
        self,
        media_buy_id: str,
        package_id: str,
        package_config: dict[str, Any],
        *,
        budget: Any = None,
        bid_price: Any = None,
    ) -> MediaPackage:
        """Persist a dual-write package row from a raw create-path config.

        The public write seam for the create path (``media_buy_create.py``):
        builds the row via the shared ``_build_package_row`` and owns the
        ``session.add``, so persistence stays in the repository layer and the
        private builder keeps zero cross-module callers.

        Distinct from ``create_package``, which takes ALREADY-COERCED
        ``Decimal`` budget/bid_price plus a separate ``pacing``. This method
        takes the RAW values (a budget dict with ``total``/``pacing``, a bare
        scalar, or ``None``) and routes them through the single coercion
        authority — reusing ``create_package`` would re-open the budget-dict
        split and the ``_to_decimal_or_none`` coercion that ``_build_package_row``
        was extracted to centralize (#1736).

        Deliberately does NOT flush: the create sites add packages in a loop
        and flush ONCE after it (the create path needs the rows visible for the
        line_item_id queries that follow), so a per-call flush would turn one
        round trip into N. ``materialize_package`` flushes because its caller
        needs the row back immediately.
        """
        package = self._build_package_row(
            media_buy_id,
            package_id,
            package_config,
            budget=budget,
            bid_price=bid_price,
        )
        self._session.add(package)
        return package

    @staticmethod
    def _build_package_row(
        media_buy_id: str,
        package_id: str,
        package_config: dict[str, Any],
        *,
        budget: Any = None,
        bid_price: Any = None,
    ) -> MediaPackage:
        """Build a ``MediaPackage`` row for the dual-write, in ONE place.

        Single home for the dual-write construction shared by
        ``materialize_package`` (legacy raw_request path) and the create-side
        sites (``media_buy_create.py`` — resolves #1736): the budget dict →
        total/pacing split, the bool-rejecting / non-500 ``_to_decimal_or_none``
        coercion, and the row build. ``budget`` is the raw budget value (a dict
        with ``total``/``pacing``, a bare scalar, or ``None``); ``bid_price`` is
        the raw scalar bid price — sourced from ``pricing_info`` at the create
        sites, from the raw package on the legacy path. A future coercion fix
        lands here and reaches every caller instead of drifting past the copies.
        """
        budget_total = None
        pacing_value = None
        if isinstance(budget, dict):
            budget_total = budget.get("total")
            pacing_value = budget.get("pacing")
        elif isinstance(budget, int | float):
            # bool is an int subtype, but a True/False budget is not 1/0 —
            # _to_decimal_or_none (the single coercion authority) rejects it.
            budget_total = budget

        return MediaPackage(
            media_buy_id=media_buy_id,
            package_id=package_id,
            package_config=package_config,
            budget=_to_decimal_or_none(budget_total),
            bid_price=_to_decimal_or_none(bid_price),
            pacing=pacing_value,
        )

    def get_package_or_raise(
        self, media_buy_id: str, package_id: str, *, context: ContextObject | dict[str, Any] | None = None
    ) -> MediaPackage:
        """Get (materializing if needed) a package or raise ``AdCPPackageNotFoundError``.

        For write paths that need the canonical row: delegates to
        ``materialize_package``, so raw_request-only packages are tolerated.
        Guards that run before the dry_run gate use ``package_exists_or_raise``
        instead — this helper writes. ``context`` is echoed into the error
        envelope.
        """
        package = self.materialize_package(media_buy_id, package_id)
        if package is None:
            self._raise_package_not_found(media_buy_id, package_id, context)
        return package

    def _raise_package_not_found(
        self, media_buy_id: str, package_id: str, context: ContextObject | dict[str, Any] | None
    ) -> NoReturn:
        """Single raise site for the PACKAGE_NOT_FOUND guard family."""
        from src.core.exceptions import AdCPPackageNotFoundError

        raise AdCPPackageNotFoundError(
            f"Package '{package_id}' not found for media buy '{media_buy_id}'",
            suggestion="Verify the package_id exists in this media buy; list the media buy's packages to find valid ids.",
            context=context,
        )

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

    def list_by_statuses(self, statuses: list[PersistedMediaBuyStatus]) -> list[MediaBuy]:
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
        statuses: list[PersistedMediaBuyStatus] | None = None,
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

    # ------------------------------------------------------------------
    # MediaBuy writes
    # ------------------------------------------------------------------

    def create_from_request(
        self,
        *,
        seller_committed: bool = False,
        media_buy_id: str,
        req: Any,
        principal_id: str,
        advertiser_name: str,
        budget: Decimal | float,
        currency: str,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
        status: PersistedMediaBuyStatus = PersistedMediaBuyStatus.DRAFT,
        order_name: str | None = None,
        campaign_objective: str | None = None,
        kpi_goal: str | None = None,
        package_id_map: dict[int, str] | None = None,
        by_alias: bool = False,
        created_at: datetime.datetime | None = None,
        account_id: str | None = None,
        payload_hash: str | None = None,
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
            account_id: Resolved account scope (AdCP idempotency scope is agent+account+key).
            payload_hash: Canonical request hash from the idempotency probe; the
                degraded fallback's IDEMPOTENCY_CONFLICT signal.

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
            "status": PersistedMediaBuyStatus.parse(status, media_buy_id=media_buy_id),
            "raw_request": raw,
            # Canonical request hash as computed by the idempotency probe —
            # raw_request is not canonicalizable (injected package_ids,
            # alias-dependent names), so the degraded idempotency fallback
            # conflict-checks against this stored hash.
            "payload_hash": payload_hash,
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
        self._stamp_confirmation_if_needed(media_buy, seller_committed=seller_committed)
        self._session.add(media_buy)
        self._session.flush()
        return media_buy

    def create(self, media_buy: MediaBuy, *, seller_committed: bool = False) -> MediaBuy:
        """Persist a new media buy within this tenant.

        The media_buy.tenant_id must match the repository's tenant_id.
        Raises ValueError if there is a tenant mismatch.

        Does NOT commit — the UoW handles that.

        Stamps ``confirmed_at`` before the flush, so a caller-built row that is
        already in a committed status cannot be persisted with a NULL
        seller-commitment instant. This is a forward-lock rather than a fix for a
        live path: today every production create goes through
        ``create_from_request`` (this method has no production callers), but both
        entry points must hold the invariant or the next caller to pick this one
        reopens the hole.
        """
        if media_buy.tenant_id != self._tenant_id:
            raise ValueError(
                f"Tenant mismatch: media_buy.tenant_id={media_buy.tenant_id!r} "
                f"!= repository tenant_id={self._tenant_id!r}"
            )
        # The caller built this row itself, so its status column is still a raw
        # string; parse it at the door like any other untyped input.
        media_buy.status = PersistedMediaBuyStatus.parse(media_buy.status, media_buy_id=media_buy.media_buy_id)
        self._stamp_confirmation_if_needed(media_buy, seller_committed=seller_committed)
        self._session.add(media_buy)
        self._session.flush()
        return media_buy

    @staticmethod
    def _bump_revision(media_buy: MediaBuy) -> None:
        """Increment the persisted monotonic revision counter by 1.

        Assigning a SQL expression rather than doing a Python read-modify-write is
        deliberate: it emits ``UPDATE ... SET revision = revision + 1``, so the
        database serializes concurrent bumps. A read-modify-write would let
        two mutations that read the same value write the same value, and ``revision``
        is the buyer's optimistic-concurrency token — it MUST strictly increase on
        every successful mutation, including two landing in the same clock tick.

        The attribute holds a SQL expression until the next refresh, so a caller
        reading ``media_buy.revision`` between this call and the flush gets that
        expression object rather than an int.

        That does NOT announce itself. Measured across ten read shapes, only two raise
        — ``int(x)`` and ``bool(x)``. Arithmetic and comparison, the two an earlier
        version of this docstring named as the raising cases, silently build a further
        SQL expression: ``x + 1`` and ``x > 2`` both succeed and return an object, not a
        number.

        The ground that actually holds the invariant is the call sites, not the type.
        Four of the five direct callers flush on the very next line, and the fifth is
        ``_bump_parent_revision``, whose own two callers both flush on their next line.
        So no exercised path reads the attribute while it holds an expression. There is
        no guard here because the window is closed by construction rather than detected
        — but if a future caller stops flushing, nothing will raise.
        """
        media_buy.revision = MediaBuy.revision + 1

    @staticmethod
    def _stamp_confirmation_if_needed(media_buy: MediaBuy, *, seller_committed: bool) -> bool:
        """Write ``confirmed_at`` the first time the seller actually commits.

        Write-once by design: ``confirmed_at`` is the instant the seller committed to
        running the buy, so it must stay stable across every later transition rather
        than tracking the most recent one. Returns whether it stamped.

        ``seller_committed`` is passed by the caller and is NOT inferred from status.
        It used to be ``is_media_buy_seller_confirmed(media_buy.status)``, and that
        proxy is lossy at exactly one member: ``media_buy_create._compute_status``
        returns PENDING_CREATIVES for ``not has_creatives or not creatives_approved``,
        which is two different states wearing one name --

          * ``not has_creatives``     an auto-approved buy with nothing supplied yet.
                                      The adapter WAS contacted; the seller committed.
          * ``not creatives_approved`` a buy held on creative review. The hold returns
                                      before the adapter is ever reached; no commitment.

        A status-keyed rule must be wrong about one of them, whichever way it is set:
        including the member minted a commitment for a held buy that a later failure
        carried to its grave, and excluding it dropped the commitment an auto-approved
        buy had genuinely earned.

        Commitment is an EVENT, so it is recorded where it happens. The two writers
        that know are the synchronous create after the adapter returns, and the single
        post-adapter approval writer. Every other caller leaves the default and cannot
        mint one by accident -- fail-closed, because a false commitment instant is
        buyer-visible and write-once, while a missing one is corrected by the next
        genuine commit.

        Pinned contract: ``create-media-buy-response.json`` @ 3.1.1 arm0 types
        ``confirmed_at`` ["string","null"], lists it in ``required``, and describes it
        as "the moment the seller committed... May be null in deferred or
        manual-approval flows until seller commitment occurs" -- an event, in the
        spec's own words. Its one hard constraint is that a null value forbids
        ``status == "active"``, which holds: ACTIVE is only ever reached through a
        writer that commits.

        Graded by @T-UC-002-v31-success-revision-and-actions (the auto-approval arm,
        which requires a timestamp) and by the approval-route integration tests (the
        held arm, which requires NULL). Settles the question filed as #2116.
        """
        if media_buy.confirmed_at is not None:
            return False
        if not seller_committed:
            return False
        media_buy.confirmed_at = datetime.datetime.now(datetime.UTC)
        return True

    def update_status(
        self,
        media_buy_id: str,
        status: PersistedMediaBuyStatus,
        *,
        seller_committed: bool = False,
        approved_at: datetime.datetime | None = None,
        approved_by: str | None = None,
    ) -> MediaBuy | None:
        """Update the status of a media buy within this tenant.

        Returns the updated MediaBuy, or None if not found in this tenant.

        The status must be a member of the persisted vocabulary. Rejecting an
        unknown value HERE is what lets every reader stop guessing: the wire
        projection maps persisted statuses to protocol ones, and a value it has no
        row for would otherwise be reported as a generic serving state — publishing
        ``active`` for a state nobody defined, with no commitment instant to go with
        it, which the pinned response schema forbids. A closed vocabulary is enforced
        where values enter, not interpreted where they are read.
        """
        normalized = PersistedMediaBuyStatus.parse(status, media_buy_id=media_buy_id)
        media_buy = self.get_by_id(media_buy_id)
        if media_buy is None:
            return None
        # Store the canonical spelling: casing is not meaning, so it is normalized
        # once here rather than tolerated by every downstream reader.
        media_buy.status = normalized
        if approved_at is not None:
            media_buy.approved_at = approved_at
        if approved_by is not None:
            media_buy.approved_by = approved_by
        # Stamp before bumping: _bump_revision leaves revision holding a SQL
        # expression, and reading any attribute after that can trigger a refresh
        # mid-mutation. The stamp reads status and confirmed_at, so it goes first.
        self._stamp_confirmation_if_needed(media_buy, seller_committed=seller_committed)
        self._bump_revision(media_buy)
        self._session.flush()
        return media_buy

    def update_fields(self, media_buy_id: str, *, seller_committed: bool = False, **kwargs: Any) -> MediaBuy | None:
        """Update arbitrary fields on a media buy within this tenant.

        Only updates fields that are valid MediaBuy column attributes.
        Returns the updated MediaBuy, or None if not found in this tenant.
        Raises ValueError if any kwarg is not a valid MediaBuy attribute or
        if the caller attempts to update an immutable field (tenant_id,
        media_buy_id, created_at, revision, confirmed_at).
        """
        blocked = self._MEDIA_BUY_IMMUTABLE_FIELDS & kwargs.keys()
        if blocked:
            raise ValueError(f"Cannot update immutable field(s): {', '.join(sorted(blocked))}")
        # status is mutable here, so this door needs the same vocabulary check
        # update_status applies — otherwise the closed vocabulary is enforced on one
        # write path and bypassable on another.
        if "status" in kwargs:
            kwargs["status"] = PersistedMediaBuyStatus.parse(kwargs["status"], media_buy_id=media_buy_id)
        media_buy = self.get_by_id(media_buy_id)
        if media_buy is None:
            return None
        for key, value in kwargs.items():
            if not hasattr(media_buy, key):
                raise ValueError(f"MediaBuy has no attribute {key!r}")
            setattr(media_buy, key, value)
        # Same ordering rule as update_status: stamp (which reads attributes) before
        # the bump (which replaces one with a SQL expression).
        self._stamp_confirmation_if_needed(media_buy, seller_committed=seller_committed)
        self._bump_revision(media_buy)
        self._session.flush()
        return media_buy

    # ------------------------------------------------------------------
    # MediaPackage writes
    # ------------------------------------------------------------------

    def _bump_parent_revision(self, media_buy_id: str) -> None:
        """Fetch the parent buy and move its concurrency token.

        Package-level writes change what the buyer sees on the media buy, so they
        move its revision too: they persist the package directly on the session
        rather than going through update_status / update_fields, which would
        otherwise leave the parent's token stale.

        For the two package writers that hold only the package (update_package_config,
        update_package_fields). The two that already hold the parent row
        (create_package, create_packages_bulk) call ``_bump_revision`` directly rather
        than paying for a second lookup.

        Raises rather than skipping when the parent is missing: get_package joins
        through MediaBuy under the tenant filter, so a package that was found
        guarantees a parent that exists. Swallowing a miss here would leave the
        buyer's concurrency token stale with no signal.
        """
        self._bump_revision(self.get_by_id_or_raise(media_buy_id))

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
        self._bump_revision(media_buy)
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
        self._bump_parent_revision(media_buy_id)
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
        self._bump_parent_revision(media_buy_id)
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
        self._bump_revision(media_buy)
        self._session.flush()
        return packages

    # ------------------------------------------------------------------
    # Cross-tenant queries (for system-level schedulers)
    # ------------------------------------------------------------------

    @staticmethod
    def get_all_by_statuses(session: Session, statuses: Collection[PersistedMediaBuyStatus]) -> list[MediaBuy]:
        """Get media buys across ALL tenants filtered by status.

        This is a system-level query for schedulers that need to process
        media buys regardless of tenant. Not tenant-scoped.

        Takes the enum, not strings. A ``list[str]`` parameter let a caller spell the
        same set of states a second time, next to the enum set it already had, and the
        two drifted apart in silence — adding a member to one left the other behind.
        ``PersistedMediaBuyStatus`` is a ``StrEnum``, so members bind against the
        ``String`` column exactly as the bare strings did.
        """
        return list(session.scalars(select(MediaBuy).where(MediaBuy.status.in_(statuses))).all())
