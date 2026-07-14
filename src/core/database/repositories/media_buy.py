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
import logging
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, object_session

from src.core.database.models import (
    MEDIA_BUY_FINALIZING_STATUS,
    MediaBuy,
    MediaPackage,
    is_media_buy_seller_confirmed,
)

if TYPE_CHECKING:
    from adcp.types import ContextObject

logger = logging.getLogger(__name__)


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

    # "revision" is repository-managed (bumped on every successful mutation);
    # callers may never write it directly — see _bump_revision.
    _MEDIA_BUY_IMMUTABLE_FIELDS: frozenset[str] = frozenset({"tenant_id", "media_buy_id", "created_at", "revision"})
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

    def get_by_id(
        self,
        media_buy_id: str,
        *,
        for_update: bool = False,
        populate_existing: bool = False,
        lock_timeout: str | None = None,
        context: ContextObject | dict[str, Any] | None = None,
    ) -> MediaBuy | None:
        """Get a media buy by its ID within the tenant.

        ``for_update=True`` acquires a row lock (``SELECT ... FOR UPDATE``) so
        concurrent writers to the same buy's mutable columns serialize under
        READ COMMITTED. Pass ``populate_existing=True`` when the caller needs
        the locked row to refresh an instance already present in the identity
        map; this is required for authoritative revision/status checks.

        ``lock_timeout`` (e.g. ``"5s"``) arms a transaction-scoped ``SET LOCAL
        lock_timeout`` before the locked read, so a second request contending for
        the SAME row's lock fails fast (PostgreSQL SQLSTATE ``55P03``) instead of
        blocking to the global ``statement_timeout``. That EXPECTED contention is
        translated to a typed transient :class:`AdCPConflictError`
        (``recovery="transient"``) — it is NOT a DB outage and must not trip the
        DB circuit breaker. Keeping the timeout + SQLSTATE handling here (not in
        the ``_impl``) keeps transport-agnostic business logic free of raw ``SET
        LOCAL`` / driver error codes — the lock policy is a data-access concern.
        ``SET LOCAL`` scopes the timeout to the caller's transaction; it bounds
        only the WAITER, not the lock HOLDER (an in-flight adapter call keeps the
        lock until commit — bounding the holder needs bounded/idempotent adapter
        execution, tracked separately). ``context`` is echoed into the CONFLICT
        envelope. #1544.
        """
        stmt = select(MediaBuy).where(
            MediaBuy.tenant_id == self._tenant_id,
            MediaBuy.media_buy_id == media_buy_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        if populate_existing:
            stmt = stmt.execution_options(populate_existing=True)
        if lock_timeout is None:
            return self._session.scalars(stmt).first()

        from sqlalchemy import text
        from sqlalchemy.exc import OperationalError

        from src.core.database.database_session import LOCK_NOT_AVAILABLE

        try:
            # lock_timeout is a config setting (not a bindable value); callers pass
            # a server-controlled literal, never user input.
            self._session.execute(text(f"SET LOCAL lock_timeout = '{lock_timeout}'"))
            return self._session.scalars(stmt).first()
        except OperationalError as exc:
            if getattr(getattr(exc, "orig", None), "pgcode", None) != LOCK_NOT_AVAILABLE:
                raise
            from src.core.exceptions import AdCPConflictError

            raise AdCPConflictError(
                f"Media buy '{media_buy_id}' is being modified by another request; retry shortly.",
                field="media_buy_id",
                suggestion="Another update holds the row lock. Re-read the media buy and retry.",
                recovery="transient",
                context=context,
            ) from exc

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

    def get_package_or_raise(
        self, media_buy_id: str, package_id: str, *, context: ContextObject | dict[str, Any] | None = None
    ) -> MediaPackage:
        """Get a package or raise ``AdCPPackageNotFoundError``.

        Collapses the package fetch-and-raise guard duplicated across the update
        tool. ``context`` is echoed into the error envelope. Coexists with
        ``get_package`` for callers that tolerate ``None``.
        """
        package = self.get_package(media_buy_id, package_id)
        if package is None:
            from src.core.exceptions import AdCPPackageNotFoundError

            raise AdCPPackageNotFoundError(
                f"Package '{package_id}' not found for media buy '{media_buy_id}'",
                suggestion="Verify the package_id exists in this media buy; list the media buy's packages to find valid ids.",
                context=context,
            )
        return package

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
            "status": status,
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
        self._session.add(media_buy)
        self._session.flush()  # materialize server-default created_at before stamping
        if self._stamp_confirmation_if_needed(media_buy):
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

    @staticmethod
    def _bump_revision(media_buy: MediaBuy) -> None:
        """Increment the persisted monotonic revision counter by 1.

        Single shared bump used by every mutation path (update_status,
        update_fields, bump_revision, apply_status_transition). The counter is
        the AdCP 3.1.0-beta.3 ``revision`` optimistic-concurrency token: it
        starts at 1 on create and MUST strictly increase on every successful
        mutation — never derived from timestamps.

        The increment is a **server-side** SQL expression, not a Python
        read-modify-write: assigning ``revision + 1`` to the mapped attribute
        defers the compute to flush time, so the database emits
        ``UPDATE ... SET revision = coalesce(revision, 0) + 1`` and serializes
        two concurrent bumps at the row write-lock. A stale identity-mapped read
        therefore cannot collapse two bumps onto the same value — the guarantee
        holds even on paths that loaded the row without ``FOR UPDATE`` (the
        cross-tenant scheduler sweep and creative-sync via
        ``apply_status_transition``). SQLAlchemy expires the attribute after
        flush, so the next read re-selects the committed value automatically.
        """
        media_buy.revision = func.coalesce(MediaBuy.revision, 0) + 1

    @staticmethod
    def _stamp_confirmation_if_needed(media_buy: MediaBuy) -> bool:
        """Write-once seller-confirmation stamp, shared by every mutation seam.

        The AdCP 3.1.0-beta.3 ``confirmed_at`` field records the instant the
        seller committed to running the buy: it is set exactly once — the first
        time the buy enters a seller-confirmed status
        (:func:`is_media_buy_seller_confirmed`) — and stays stable across later
        creative/status transitions. Keyed on the buy's status at the call, so
        every seam that lands a seller-confirmed status stamps identically: the
        create path, :meth:`update_status`, :meth:`update_fields` (staged
        ``status``), and :meth:`apply_status_transition` (scheduler sweep,
        creative-sync). Keeping it in one place is what stops the create and get
        paths from drifting — a buy that reached a committed state on any seam
        carries ``confirmed_at`` on the wire. See #1544.

        Returns True when the buy first entered a seller-confirmed status here,
        so a caller that must persist the stamp immediately (the create path,
        before the row leaves the repository) knows to flush; the mutation seams
        ignore it because they flush the whole mutation afterwards regardless.
        """
        if media_buy.confirmed_at is None and is_media_buy_seller_confirmed(media_buy.status):
            # The in-memory ``confirmed_at`` guard is only trustworthy when the row
            # was loaded under a row lock (or is freshly created). The single-row
            # mutators load ``FOR UPDATE`` + ``populate_existing`` and the create
            # path just inserted the row, so ``confirmed_at is None`` here reflects
            # the committed state. The one seam that loads WITHOUT a lock —
            # :meth:`apply_status_transition` (scheduler sweep / creative-sync) —
            # locks and refreshes ``confirmed_at`` itself before calling this, so a
            # stale ``None`` cannot slip through and clobber a concurrently
            # committed stamp. Value: the approval instant, else the create instant.
            media_buy.confirmed_at = media_buy.approved_at or media_buy.created_at
            return True
        return False

    def _locked_mutate_and_bump(
        self,
        media_buy_id: str,
        mutate: Callable[[MediaBuy], None],
        *,
        expected_revision: int | None = None,
        expected_status: str | tuple[str, ...] | None = None,
        expected_lease_id: str | None = None,
        context: ContextObject | dict[str, Any] | None = None,
        bump: bool = True,
    ) -> MediaBuy | None:
        """Shared single-row mutation skeleton: load-under-lock → mutate → bump → flush.

        Loads the buy with a row write-lock, applies ``mutate`` in place, stamps
        the write-once ``confirmed_at`` if the new status warrants it, bumps the
        persisted revision counter, and flushes. Returns the mutated row, or
        None if the buy is not found in this tenant (no bump/flush). Every
        single-row mutator (``bump_revision``, ``update_status``,
        ``update_fields``) routes through here so the load-guard, the confirm
        stamp, the bump, and the flush live in exactly one place.

        ``expected_revision`` is the buyer's optimistic-concurrency token
        (AdCP 3.1.0-beta.3 update-media-buy-request ``revision``): when
        provided, the check happens HERE, under the held row lock. The update
        tool also gates on the token before dispatching to the adapter, but that
        gate and this mutation run in the same UoW under the same lock; this
        check is the authoritative backstop for callers that reach the
        repository directly (admin routes, ``bump_revision`` callers, tests).
        Raises the shared CONFLICT on mismatch, before any mutation.

        ``expected_status`` is a single-winner CLAIM: when provided, if the
        committed (post-lock) ``status`` is not among it, return ``None`` with NO
        mutation/bump. Unlike ``expected_revision`` this does NOT raise — a lost
        claim is a normal race outcome (a competing approve/reject already moved
        the buy out of the eligible state), not a buyer-visible CONFLICT. The
        ``FOR UPDATE`` load serializes concurrent claimants, so the loser sees the
        winner's committed status and bails; exactly one caller proceeds. #1544.
        """
        media_buy = self.get_by_id(media_buy_id, for_update=True, populate_existing=True)
        if media_buy is None:
            return None
        if expected_status is not None:
            allowed = (expected_status,) if isinstance(expected_status, str) else expected_status
            if media_buy.status not in allowed:
                return None
        # Phase-2 OWNERSHIP check (#1637): like ``expected_status``, a lost lease is a
        # normal race outcome — a reconciler (or a competing worker) took over the
        # finalization, so this caller must do NOTHING (no publish/fail/terminalize).
        if expected_lease_id is not None and media_buy.finalize_lease_id != expected_lease_id:
            return None
        if expected_revision is not None:
            from src.core.exceptions import media_buy_revision_conflict

            # populate_existing=True on the locked SELECT above already overwrote
            # the identity-mapped revision with the committed value, so compare
            # directly — no second SELECT needed.
            if media_buy.revision != expected_revision:
                raise media_buy_revision_conflict(
                    media_buy_id, expected=expected_revision, current=media_buy.revision, context=context
                )
        mutate(media_buy)
        self._stamp_confirmation_if_needed(media_buy)
        if bump:
            # ``bump=False`` is the crash-recoverable finalize (#1637): the
            # approval already bumped revision when it claimed ``finalizing``, so
            # the deferred ``finalizing`` -> serving transition (and any reconciler
            # retry of it) must NOT advance the token again.
            self._bump_revision(media_buy)
        self._session.flush()
        return media_buy

    def bump_revision(
        self,
        media_buy_id: str,
        *,
        expected_revision: int | None = None,
        context: ContextObject | dict[str, Any] | None = None,
    ) -> MediaBuy | None:
        """Bump the revision of a media buy that was mutated outside this repository.

        For tool paths that persist changes to a buy's packages/assignments
        directly on the session (e.g. package targeting_overlay writes) and
        therefore never pass through ``update_status``/``update_fields``.
        Returns the updated MediaBuy, or None if not found in this tenant.
        ``expected_revision`` is checked under the row lock (CONFLICT on mismatch).
        """
        # No column change of its own — the shared skeleton does the bump/flush.
        return self._locked_mutate_and_bump(
            media_buy_id, lambda _media_buy: None, expected_revision=expected_revision, context=context
        )

    def update_status(
        self,
        media_buy_id: str,
        status: str,
        *,
        approved_at: datetime.datetime | None = None,
        approved_by: str | None = None,
    ) -> MediaBuy | None:
        """Update the status of a media buy within this tenant.

        Bumps the persisted revision counter (successful mutation).
        Returns the updated MediaBuy, or None if not found in this tenant.
        """

        def _apply(media_buy: MediaBuy) -> None:
            media_buy.status = status
            if approved_at is not None:
                media_buy.approved_at = approved_at
            if approved_by is not None:
                media_buy.approved_by = approved_by

        return self._locked_mutate_and_bump(media_buy_id, _apply)

    def update_status_computed(
        self,
        media_buy_id: str,
        compute_target: Callable[[MediaBuy], str | None],
        *,
        approved_at: datetime.datetime | None = None,
        approved_by: str | None = None,
        expected_status: str | tuple[str, ...] | None = None,
        expected_lease_id: str | None = None,
        clear_finalize_state: bool = False,
        bump: bool = True,
    ) -> MediaBuy | None:
        """Like :meth:`update_status`, but the destination status is COMPUTED under the lock.

        For approval routes whose target is a window→status decision
        (``scheduled``/``active``/``completed``) or ``pending_creatives``: the
        target depends on the buy's own flight window, so it must be derived from
        the committed row, not a stale pre-lock read (same lost-update race the
        scheduler hits — see :meth:`apply_computed_status_transition`).
        :meth:`_locked_mutate_and_bump` loads ``FOR UPDATE`` with
        ``populate_existing=True`` (every column refreshed to the committed
        value), so ``compute_target`` runs against the live window. A ``None``
        return from ``compute_target`` leaves the status unchanged
        (``approved_at``/``approved_by`` still stamped); the shared skeleton
        stamps ``confirmed_at`` and bumps revision.

        ``expected_status`` makes this a single-winner CLAIM (approval
        orchestration): the transition applies ONLY if the committed status is
        among it, else the method returns ``None`` untouched — so exactly one of
        several concurrent approve/reject requests wins the decision and proceeds
        to the adapter. Returns None if the buy is not found OR the claim was lost.
        #1544.
        """

        def _apply(media_buy: MediaBuy) -> None:
            target = compute_target(media_buy)
            if target is not None:
                media_buy.status = target
            if approved_at is not None:
                media_buy.approved_at = approved_at
            if approved_by is not None:
                media_buy.approved_by = approved_by
            if clear_finalize_state:
                # Successful publish (#1637): the finalization operation is over —
                # drop the lease, the adapter-invoked marker, and any
                # manual_required disposition set while this (slow) owner was
                # still running (self-heal).
                media_buy.finalize_lease_id = None
                media_buy.finalize_lease_expires_at = None
                media_buy.finalize_adapter_invoked_at = None
                media_buy.finalize_recovery_mode = None

        return self._locked_mutate_and_bump(
            media_buy_id, _apply, expected_status=expected_status, expected_lease_id=expected_lease_id, bump=bump
        )

    @staticmethod
    def _new_finalize_lease() -> str:
        return f"lease_{uuid.uuid4().hex[:12]}"

    def claim_finalizing(
        self,
        media_buy_id: str,
        *,
        expected_status: str | tuple[str, ...],
        lease_ttl_seconds: int,
        approved_at: datetime.datetime | None = None,
        approved_by: str | None = None,
    ) -> tuple[MediaBuy, str] | None:
        """Phase-1 single-winner CLAIM: eligible status → ``finalizing`` + fresh lease.

        One locked mutate sets status=finalizing, a fresh lease (owner token +
        expiry), stamps ``approved_at``/``approved_by`` when supplied, and RESETS the
        adapter-invoked marker + recovery disposition — a fresh claim (e.g. an
        operator re-approval after manual reconciliation) starts with a clean
        operation state. Bumps revision (the approval's single token advance).
        Returns ``(row, lease_id)`` for phase 2, or ``None`` on a lost claim. #1637.
        """
        lease_id = self._new_finalize_lease()

        def _apply(media_buy: MediaBuy) -> None:
            media_buy.status = MEDIA_BUY_FINALIZING_STATUS
            media_buy.finalize_lease_id = lease_id
            media_buy.finalize_lease_expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
                seconds=lease_ttl_seconds
            )
            media_buy.finalize_adapter_invoked_at = None
            media_buy.finalize_recovery_mode = None
            if approved_at is not None:
                media_buy.approved_at = approved_at
            if approved_by is not None:
                media_buy.approved_by = approved_by

        claimed = self._locked_mutate_and_bump(media_buy_id, _apply, expected_status=expected_status)
        return (claimed, lease_id) if claimed is not None else None

    def acquire_finalize_lease(self, media_buy_id: str, *, lease_ttl_seconds: int) -> str | None:
        """Reconciler CAS: take over a ``finalizing`` buy whose lease is absent/expired.

        Under the row lock, proceeds ONLY when status is ``finalizing``, the recovery
        disposition is automatic (NULL), and the current lease is absent or expired —
        an unexpired lease means a live worker owns phase 2. No revision bump (lease
        churn is not a buyer-visible mutation). Returns the new lease id, or ``None``
        (someone owns it / disposition is manual / buy moved on). #1637.
        """
        lease_id = self._new_finalize_lease()
        now = datetime.datetime.now(datetime.UTC)

        def _apply(media_buy: MediaBuy) -> None:
            media_buy.finalize_lease_id = lease_id
            media_buy.finalize_lease_expires_at = now + datetime.timedelta(seconds=lease_ttl_seconds)

        media_buy = self.get_by_id(media_buy_id, for_update=True, populate_existing=True)
        if media_buy is None or media_buy.status != MEDIA_BUY_FINALIZING_STATUS:
            return None
        if media_buy.finalize_recovery_mode is not None:
            return None
        if media_buy.finalize_lease_expires_at is not None and media_buy.finalize_lease_expires_at > now:
            return None
        _apply(media_buy)
        self._session.flush()
        return lease_id

    def release_finalize_lease(self, media_buy_id: str, lease_id: str) -> bool:
        """Clear the lease iff still owner (the RETRYING path — no TTL wait). #1637."""

        def _apply(media_buy: MediaBuy) -> None:
            media_buy.finalize_lease_id = None
            media_buy.finalize_lease_expires_at = None

        released = self._locked_mutate_and_bump(media_buy_id, _apply, expected_lease_id=lease_id, bump=False)
        return released is not None

    def set_finalize_adapter_invoked(self, media_buy_id: str, lease_id: str) -> bool:
        """CAS-set the adapter-invoked marker (still owner + still finalizing). #1637.

        Committed by the caller IMMEDIATELY BEFORE ``run_adapter``: presence means
        "remote mutations may exist", gating which adapters may auto-resume past it.
        """

        def _apply(media_buy: MediaBuy) -> None:
            media_buy.finalize_adapter_invoked_at = datetime.datetime.now(datetime.UTC)

        marked = self._locked_mutate_and_bump(
            media_buy_id,
            _apply,
            expected_status=MEDIA_BUY_FINALIZING_STATUS,
            expected_lease_id=lease_id,
            bump=False,
        )
        return marked is not None

    def clear_finalize_adapter_invoked(self, media_buy_id: str, lease_id: str) -> bool:
        """CAS-clear the adapter-invoked marker (the uncertain-before-mutation path). #1637."""

        def _apply(media_buy: MediaBuy) -> None:
            media_buy.finalize_adapter_invoked_at = None

        cleared = self._locked_mutate_and_bump(media_buy_id, _apply, expected_lease_id=lease_id, bump=False)
        return cleared is not None

    def set_finalize_recovery_manual(self, media_buy_id: str) -> bool:
        """Mark a stranded buy ``manual_required`` (fail-closed disposition). #1637.

        CAS: only while still ``finalizing`` with an EXPIRED/absent lease and an
        automatic disposition — a live owner or an already-flagged buy is left
        alone. Does NOT take the lease, so a slow-but-alive worker's eventual
        publish CAS (which checks its own lease) still succeeds and self-heals.
        """
        now = datetime.datetime.now(datetime.UTC)
        media_buy = self.get_by_id(media_buy_id, for_update=True, populate_existing=True)
        if media_buy is None or media_buy.status != MEDIA_BUY_FINALIZING_STATUS:
            return False
        if media_buy.finalize_recovery_mode is not None:
            return False
        if media_buy.finalize_lease_expires_at is not None and media_buy.finalize_lease_expires_at > now:
            return False
        media_buy.finalize_recovery_mode = "manual_required"
        self._session.flush()
        return True

    @staticmethod
    def get_finalizing_recoverable(session: Session, now: datetime.datetime) -> list[MediaBuy]:
        """Cross-tenant reconciler scan: ``finalizing`` buys eligible for auto-recovery.

        Excludes buys with an UNEXPIRED lease (a live worker owns phase 2) and buys
        flagged ``manual_required`` (hot-loop prevention — the reconciler never
        re-touches those). The per-buy ``acquire_finalize_lease`` CAS remains the
        authoritative single-winner gate; this filter is noise reduction. #1637.
        """
        return list(
            session.scalars(
                select(MediaBuy).where(
                    MediaBuy.status == MEDIA_BUY_FINALIZING_STATUS,
                    MediaBuy.finalize_recovery_mode.is_(None),
                    or_(
                        MediaBuy.finalize_lease_expires_at.is_(None),
                        MediaBuy.finalize_lease_expires_at < now,
                    ),
                )
            ).all()
        )

    def update_status_computed_or_raise(
        self,
        media_buy_id: str,
        compute_target: Callable[[MediaBuy], str | None],
        *,
        approved_at: datetime.datetime | None = None,
        approved_by: str | None = None,
    ) -> MediaBuy:
        """``update_status_computed``, raising if the buy vanished mid-request (No Quiet Failures)."""
        return self._require_mutated(
            self.update_status_computed(media_buy_id, compute_target, approved_at=approved_at, approved_by=approved_by),
            media_buy_id,
            "computed status transition",
        )

    def _require_mutated(self, media_buy: MediaBuy | None, media_buy_id: str, action: str) -> MediaBuy:
        """Shared vanished-row invariant behind the ``*_or_raise`` mutation variants.

        The base mutators return ``None`` for a buy not found in this tenant.
        Callers that verified the buy exists before mutating (admin routes, the
        update tool past ``get_by_id_or_raise``) must not tolerate that —
        proceeding would report success for a write that never happened
        (No Quiet Failures). Callers that deliberately tolerate a missing buy
        keep using the base mutators and check the return themselves.
        """
        if media_buy is None:
            raise RuntimeError(
                f"media buy {media_buy_id!r} disappeared during {action} — it existed when the request began"
            )
        return media_buy

    def update_status_or_raise(
        self,
        media_buy_id: str,
        status: str,
        *,
        approved_at: datetime.datetime | None = None,
        approved_by: str | None = None,
    ) -> MediaBuy:
        """``update_status``, raising if the buy vanished mid-request (No Quiet Failures)."""
        return self._require_mutated(
            self.update_status(media_buy_id, status, approved_at=approved_at, approved_by=approved_by),
            media_buy_id,
            f"status transition to {status!r}",
        )

    def bump_revision_or_raise(
        self,
        media_buy_id: str,
        *,
        expected_revision: int | None = None,
        context: ContextObject | dict[str, Any] | None = None,
    ) -> MediaBuy:
        """``bump_revision``, raising if the buy vanished mid-request (No Quiet Failures)."""
        return self._require_mutated(
            self.bump_revision(media_buy_id, expected_revision=expected_revision, context=context),
            media_buy_id,
            "revision bump",
        )

    def update_fields(
        self,
        media_buy_id: str,
        *,
        expected_revision: int | None = None,
        context: ContextObject | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MediaBuy | None:
        """Update arbitrary fields on a media buy within this tenant.

        Only updates fields that are valid MediaBuy column attributes.
        Bumps the persisted revision counter (successful mutation).
        Returns the updated MediaBuy, or None if not found in this tenant.
        ``expected_revision`` is checked under the row lock (CONFLICT on
        mismatch, before any mutation). Raises ValueError if any kwarg is not
        a valid MediaBuy attribute or if the caller attempts to update an
        immutable/repository-managed field (tenant_id, media_buy_id,
        created_at, revision).
        """
        blocked = self._MEDIA_BUY_IMMUTABLE_FIELDS & kwargs.keys()
        if blocked:
            raise ValueError(f"Cannot update immutable field(s): {', '.join(sorted(blocked))}")

        def _apply(media_buy: MediaBuy) -> None:
            for key, value in kwargs.items():
                if not hasattr(media_buy, key):
                    raise ValueError(f"MediaBuy has no attribute {key!r}")
                setattr(media_buy, key, value)

        return self._locked_mutate_and_bump(media_buy_id, _apply, expected_revision=expected_revision, context=context)

    def update_fields_or_raise(
        self,
        media_buy_id: str,
        *,
        expected_revision: int | None = None,
        context: ContextObject | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> MediaBuy:
        """``update_fields``, raising if the buy vanished mid-request (No Quiet Failures)."""
        return self._require_mutated(
            self.update_fields(media_buy_id, expected_revision=expected_revision, context=context, **kwargs),
            media_buy_id,
            "field update",
        )

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

    # Lifecycle inputs refreshed under the row lock before a target status is
    # (re)computed: the flight window AND the current status/confirmation. A
    # target derived from any of these on a STALE unlocked read can lose a race
    # (e.g. a concurrent end_time extension), so the compute must see the
    # committed values. See :meth:`apply_computed_status_transition` / #1544.
    _LIFECYCLE_REFRESH_FIELDS: tuple[str, ...] = (
        "status",
        "confirmed_at",
        "start_time",
        "end_time",
        "start_date",
        "end_date",
    )

    @staticmethod
    def apply_computed_status_transition(
        media_buy: MediaBuy,
        compute_target: Callable[[MediaBuy], str | None],
    ) -> MediaBuy:
        """Lock the row, refresh EVERY lifecycle input, THEN compute the target.

        The seam for lifecycle transitions whose target depends on the buy's own
        state — the flight window (``start``/``end``) and current ``status`` — on
        paths that loaded the row WITHOUT a lock (the cross-tenant scheduler sweep
        via :meth:`get_all_by_statuses`, the admin approve/creative-unblock routes
        that resolve a window→status decision). Computing the target from the
        unlocked identity-mapped row is a lost-update race: the scheduler can read
        ``end_time`` in the past, decide ``completed``, and then a concurrent
        transaction extends ``end_time`` (leaving ``status`` ``active``) and
        commits — the stale ``completed`` would overwrite the live ``active`` buy.

        So the caller supplies ``compute_target`` as a CALLBACK, evaluated only
        AFTER the ``FOR UPDATE`` refresh of every lifecycle input, never a
        precomputed string. ``compute_target`` returning ``None`` is the sole
        no-op signal — no status write, no ``confirmed_at`` stamp, no revision
        bump — so a caller that decides "nothing to do" against the committed row
        returns ``None`` explicitly (the scheduler does). A returned status is
        applied even when it equals the current one: re-asserting a status IS a
        mutation for revision purposes (the unlocked seam relies on that — see
        ``test_two_concurrent_apply_status_transition_yield_distinct_revisions``),
        and every real caller that would otherwise pass the same value returns
        ``None`` instead. The transition applies and stamps/bumps via the shared
        seams, so no committed buy is left without a confirmation instant. The
        lock is held until the caller commits, serializing concurrent transitions
        of the same buy. ``revision`` needs no refresh — it bumps via a
        server-side expression that serializes at the write-lock. Returns the same
        (mutated) row. See #1544.
        """
        session = object_session(media_buy)
        if session is not None:
            session.refresh(
                media_buy,
                list(MediaBuyRepository._LIFECYCLE_REFRESH_FIELDS),
                with_for_update=True,
            )
        target = compute_target(media_buy)
        if target is None:
            return media_buy
        media_buy.status = target
        MediaBuyRepository._stamp_confirmation_if_needed(media_buy)
        MediaBuyRepository._bump_revision(media_buy)
        return media_buy

    @staticmethod
    def apply_status_transition(media_buy: MediaBuy, new_status: str) -> MediaBuy:
        """Transition an already-loaded MediaBuy to ``new_status`` and bump revision.

        The seam for paths that already hold a ``MediaBuy`` row on their own
        session and therefore cannot use tenant-scoped, single-row
        :meth:`update_status`: the cross-tenant scheduler sweep (rows from
        :meth:`get_all_by_statuses`) and the creative-sync assignment pass
        (rows loaded inside ``CreativeUoW``). The caller owns the
        session/transaction and commits. Bumps the AdCP 3.1.0-beta.3
        ``revision`` counter so seller-initiated lifecycle transitions
        (``pending_start`` → ``active``, ``active`` → ``completed``,
        ``draft`` → ``pending_creatives``) advance the optimistic-concurrency
        token like any other state change, and stamps the write-once
        ``confirmed_at`` when the transition enters a seller-confirmed status —
        via the same :meth:`_stamp_confirmation_if_needed` seam the tenant-scoped
        mutators use, so no path can leave a committed buy without a confirmation
        instant. Returns the same (mutated) row so the seam matches the
        ``MediaBuy | None`` shape of every sibling mutator — the return is never
        None here because the caller supplies a loaded row. See #1544.

        Unlike the tenant-scoped mutators, callers here loaded the row WITHOUT a
        row lock (the scheduler sweep via :meth:`get_all_by_statuses`, creative-
        sync inside ``CreativeUoW``), so BOTH the identity-mapped ``status`` and
        ``confirmed_at`` may be stale while a concurrent transaction has committed
        a newer decision. Lock and refresh ``status`` + ``confirmed_at`` before
        applying the transition:

        * If ``status`` changed under the stale read — e.g. an admin committed
          ``rejected`` while the scheduler still held ``active`` — the caller's
          target was computed from the stale value, so applying it would overwrite
          the newer decision (``rejected`` → ``completed``). No-op instead: leave
          the committed state and skip the bump. The scheduler re-evaluates next
          cycle; creative-sync's stamp is best-effort.
        * Otherwise the refreshed ``confirmed_at`` feeds the write-once check in
          :meth:`_stamp_confirmation_if_needed`, so a stale ``None`` cannot clobber
          a concurrently committed stamp.

        The ``FOR UPDATE`` lock is held until the caller commits, serializing
        concurrent transitions of the same buy. ``revision`` needs no refresh — it
        bumps via a server-side expression that serializes at the write-lock.

        This is the STATIC-target variant of :meth:`apply_computed_status_transition`:
        the caller already knows the destination status (it does not depend on the
        refreshed window), so the only race to guard is a concurrent status change.
        Expressed as the compute callback below — return the fixed ``new_status``
        only while the committed status still matches what the caller based it on;
        otherwise no-op — it reuses the one lock→refresh→stamp→bump tail.
        """
        # The source status the caller based ``new_status`` on, captured before the
        # locked refresh (inside the computed seam) overwrites it with the committed
        # value.
        expected_from_status = media_buy.status

        def _compute(refreshed: MediaBuy) -> str | None:
            if refreshed.status != expected_from_status:
                logger.info(
                    "apply_status_transition: skipping %s -> %s for media buy %s; "
                    "committed status changed to %s under a stale unlocked read",
                    expected_from_status,
                    new_status,
                    refreshed.media_buy_id,
                    refreshed.status,
                )
                return None
            return new_status

        return MediaBuyRepository.apply_computed_status_transition(media_buy, _compute)

    @staticmethod
    def apply_revision_bump(media_buy: MediaBuy) -> MediaBuy:
        """Advance the revision of an already-loaded buy WITHOUT a status change.

        The seam for a non-status mutation that still materially changes the buy —
        a creative assignment created, or an assignment weight actually changed, on
        the creative-sync pass (rows loaded inside ``CreativeUoW``). Bumps the AdCP
        3.1.0-beta.3 ``revision`` optimistic-concurrency token so a buyer's next
        update observes a fresh token, WITHOUT writing ``status`` or stamping
        ``confirmed_at`` (the buy's lifecycle state is unchanged). Uses the same
        server-side ``coalesce(revision, 0) + 1`` expression as every other seam,
        so it is concurrency-safe on the unlocked ``CreativeUoW`` row and never
        collapses two bumps onto one value.

        The CALLER filters idempotent no-op assignments (existing assignment, weight
        already at target) — this method ALWAYS bumps, and must not be called for a
        buy that also transitions status this pass (``apply_status_transition``
        already bumps once; calling both would double-count). Returns the same
        (mutated) row. See #1544 (B3).
        """
        MediaBuyRepository._bump_revision(media_buy)
        return media_buy
