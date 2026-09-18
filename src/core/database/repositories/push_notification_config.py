"""PushNotificationConfig repository — tenant-scoped data access.

Core invariant: every query includes both ``tenant_id`` AND ``principal_id``
in the WHERE clause. PushNotificationConfig rows belong to a single
(tenant, principal) pair; cross-principal lookups are not exposed.

Write methods add objects to the session but never commit — the Unit of Work
(``PushNotificationConfigUoW``) handles commit/rollback at the boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from src.core.database.integrity import resolve_or_write
from src.core.database.models import PushNotificationConfig
from src.core.webhooks.registration import ValidatedWebhookRegistration

# Preserve-if-not-passed sentinel for upsert(): distinguishes "caller did not
# supply this field" (keep the existing row's value) from an explicit None
# (clear it). Without it, migrating callers onto upsert() would silently null
# a validation_token set through another path for the same config id.
#
# It covers the three SIDEBAND columns — the ones no single registration surface
# knows all of. ``validation_token`` is written only by A2A
# ``setTaskPushNotificationConfig``; ``webhook_secret`` only by admin
# registration; ``session_id`` only by the session-scoped paths. Every one of
# those callers omits the other two, so a plain ``None`` default makes each write
# a silent erase of the others. The columns carried by the registration receipt
# (url, auth pair, and the operation_id/token the seller must echo) are
# deliberately NOT sentinel-defaulted: they are required, and the receipt always
# has them.
_UNSET: Any = object()


class PushNotificationConfigRepository:
    """Tenant + principal scoped access for PushNotificationConfig.

    All queries filter by ``tenant_id`` automatically. Principal scope is
    required on every method — there is no cross-principal lookup.

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

    def _scoped(self, principal_id: str, *, active_only: bool) -> Select[tuple[PushNotificationConfig]]:
        """The (tenant, principal) scope every lookup here shares, in ONE place.

        This module's core invariant -- every query is scoped by tenant AND
        principal -- was previously enforced by prose repeated per method and by
        each method retyping the same two predicates. A third lookup would have
        made it a third copy, and the pair is exactly the thing that must not be
        forgotten once. Callers append their own single predicate.
        """
        stmt = select(PushNotificationConfig).where(
            PushNotificationConfig.tenant_id == self._tenant_id,
            PushNotificationConfig.principal_id == principal_id,
        )
        if active_only:
            stmt = stmt.where(PushNotificationConfig.is_active.is_(True))
        return stmt

    def get_by_id(
        self,
        config_id: str,
        principal_id: str,
        *,
        active_only: bool = True,
    ) -> PushNotificationConfig | None:
        """Get a single config by ID within the (tenant, principal) scope.

        Args:
            config_id: The config's primary-key id.
            principal_id: Principal scope filter.
            active_only: If True (default), only return configs where
                ``is_active`` is True. Pass False to include soft-deleted rows
                (e.g. for an upsert that needs to re-activate them).
        """
        stmt = self._scoped(principal_id, active_only=active_only).where(PushNotificationConfig.id == config_id)
        return self._session.scalars(stmt).first()

    def find_by_url(
        self,
        principal_id: str,
        url: str,
        *,
        active_only: bool = True,
    ) -> PushNotificationConfig | None:
        """Find a config by its URL within the (tenant, principal) scope.

        The duplicate check at registration used to hand-write this query in the
        admin route and omit ``is_active``, so a URL that had been deactivated
        still read as "already registered" -- the operator could not re-register
        it, and (before the same change) could not delete or re-enable it either.

        ``active_only=False`` is what the registration path passes: it needs to
        SEE the soft-deleted row so it can reuse that row's id and let
        :meth:`upsert` reactivate it, rather than inserting a second row for the
        same (principal, url) and leaving the first as debris.
        """
        stmt = self._scoped(principal_id, active_only=active_only).where(PushNotificationConfig.url == url)
        return self._session.scalars(stmt).first()

    def list_active_by_principal(self, principal_id: str) -> list[PushNotificationConfig]:
        """Return all active configs for a principal within this tenant."""
        return list(self._session.scalars(self._scoped(principal_id, active_only=True)).all())

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def upsert(
        self,
        registration: ValidatedWebhookRegistration,
        *,
        config_id: str,
        principal_id: str,
        validation_token: str | None = _UNSET,
        session_id: str | None = _UNSET,
        webhook_secret: str | None = _UNSET,
    ) -> tuple[PushNotificationConfig, bool]:
        """Insert or update a config within the (tenant, principal) scope.

        Takes the VALUE, not three loose strings. ``ValidatedWebhookRegistration``
        is the receipt that both ingest preconditions ran — the registration SSRF
        gate on the URL half and the pinned ``Authentication`` model built inside
        ``_accept`` on the credential half — so persisting a config that skipped a
        gate no longer type-checks.

        That is why this module no longer re-validates the URL. The former
        "defense-in-depth" check here existed because the receipt evaporated at
        this boundary: a caller that had never gated looked exactly like one that
        had. It also could not produce a good error — the repository cannot know
        the request path, so ``error.field`` was lost. SEND time is not this
        module's business either: every outbound request goes through the egress
        seam (``src.core.security.outbound_http``), which re-resolves and re-judges
        the URL when it is actually dialled.

        The three SIDEBAND columns stay explicit kwargs rather than value fields,
        because none of them is part of the registration a buyer submits:
        ``validation_token`` is sender-side ``X-Webhook-Token`` material,
        deliberately outside the auth resolver, and only the A2A
        ``setTaskPushNotificationConfig`` path stores one; ``webhook_secret`` is
        the admin-registered HMAC secret; ``session_id`` is set only on
        session-scoped paths.

        All three are preserve-if-not-passed: omitting one keeps the existing
        row's value (``None`` on insert), passing ``None`` explicitly clears it.
        Callers share config ids across paths, so a caller that does not OWN a
        field must not null it: a plain ``None`` default would make every caller
        that knows one column silently erase the other two on the reactivation
        branch below — letting, say, the create-media-buy path clear a
        ``validation_token`` another path set.

        Returns:
            (config, created): ``created`` is True if a new row was inserted,
            False if an existing row was updated (or reactivated).
        """
        columns = registration.to_columns()

        existing = self.get_by_id(config_id, principal_id, active_only=False)
        now = datetime.now(UTC)

        if existing is not None:
            existing.url = columns["url"]
            existing.authentication_type = columns["authentication_type"]
            existing.authentication_token = columns["authentication_token"]
            existing.operation_id = columns["operation_id"]
            existing.token = columns["token"]
            if validation_token is not _UNSET:
                existing.validation_token = validation_token
            if session_id is not _UNSET:
                existing.session_id = session_id
            if webhook_secret is not _UNSET:
                existing.webhook_secret = webhook_secret
            existing.updated_at = now
            existing.is_active = True
            self._session.flush()
            return existing, False

        config = PushNotificationConfig(
            id=config_id,
            tenant_id=self._tenant_id,
            principal_id=principal_id,
            session_id=None if session_id is _UNSET else session_id,
            url=columns["url"],
            authentication_type=columns["authentication_type"],
            authentication_token=columns["authentication_token"],
            operation_id=columns["operation_id"],
            token=columns["token"],
            validation_token=None if validation_token is _UNSET else validation_token,
            webhook_secret=None if webhook_secret is _UNSET else webhook_secret,
            is_active=True,
        )
        self._session.add(config)
        self._session.flush()
        return config, True

    # ------------------------------------------------------------------
    # Admin registration (race-safe)
    # ------------------------------------------------------------------

    @staticmethod
    def admin_config_id(tenant_id: str, principal_id: str, url: str) -> str:
        """Deterministic id for ADMIN-registered configs.

        Two racing admin registrations of the same URL compute the same id, so
        the primary key — not the pre-check — decides the race. This enforces
        a SCOPED invariant: one active admin-registered config per
        (tenant, principal, url). It deliberately does NOT impose DB-wide URL
        uniqueness: AdCP 3.1.1 keys push_notification_configs by id and is
        silent on URL uniqueness, so a protocol-path config (buyer-chosen id)
        with the same URL remains legal.
        """
        return "pnc_" + uuid5(NAMESPACE_URL, f"{tenant_id}:{principal_id}:{url}").hex

    def register_admin_webhook(
        self,
        registration: ValidatedWebhookRegistration,
        *,
        principal_id: str,
        webhook_secret: str | None,
    ) -> PushNotificationConfig | None:
        """Race-safe admin registration of a webhook URL.

        Takes the same receipt :meth:`upsert` does. The admin route is one of the
        five registration surfaces, and it is not allowed to be the one that
        persists a config which skipped the ingest gates.

        Keyed on ``registration.url``, never a raw form value: what gets STORED is
        the normalized ``AnyUrl`` — host lowercased, default port stripped,
        trailing slash added — so a pre-check keyed on a non-canonical spelling
        misses the row it just wrote, and a second active config is inserted for
        the same URL (the sender then delivers twice).

        Returns the EXISTING active config when this URL is already registered
        for the principal — whether the pre-check saw it or this call lost the
        insert race to a concurrent registration — and ``None`` when a config
        was created (or a soft-deleted one reactivated).

        Takes the same receipt :meth:`upsert` does, for the same reason: the
        admin route is one of the registration surfaces the gate covers, so
        letting this method accept loose ``url`` / auth strings would reopen the
        exact hole the receipt closes — a caller that never gated would be
        indistinguishable from one that did, and this module must not forge the
        receipt by re-running the gate itself.
        """
        url = registration.url

        def conflict() -> PushNotificationConfig | None:
            return self.find_by_url(principal_id, url)

        def write() -> None:
            self.upsert(
                registration,
                config_id=self.admin_config_id(self._tenant_id, principal_id, url),
                principal_id=principal_id,
                webhook_secret=webhook_secret,
            )

        return resolve_or_write(
            self._session,
            conflict=conflict,
            write=write,
            constraint="push_notification_configs_pkey",
        )

    def delete(self, config_id: str, principal_id: str) -> bool:
        """Hard-delete a config within the (tenant, principal) scope.

        Admin-management semantics: the row disappears from the management
        page entirely (unlike ``soft_delete``, whose inactive rows the page
        still lists with a re-activate toggle).

        Returns:
            True if a matching row was found and deleted, False otherwise.
        """
        config = self.get_by_id(config_id, principal_id, active_only=False)
        if config is None:
            return False
        self._session.delete(config)
        self._session.flush()
        return True

    def toggle_active(self, config_id: str, principal_id: str) -> bool | None:
        """Flip a config's ``is_active`` within the (tenant, principal) scope.

        Returns:
            The NEW ``is_active`` value, or ``None`` if no matching row exists.
        """
        config = self.get_by_id(config_id, principal_id, active_only=False)
        if config is None:
            return None
        config.is_active = not config.is_active
        config.updated_at = datetime.now(UTC)
        self._session.flush()
        return config.is_active

    def soft_delete(self, config_id: str, principal_id: str) -> bool:
        """Mark a config inactive within the (tenant, principal) scope.

        Finds the row regardless of its current ``is_active`` value and
        sets ``is_active=False``. Idempotent — calling on an already-inactive
        row still returns True.

        Returns:
            True if a matching row was found (and is now inactive),
            False if no row with that ``(tenant, principal, id)`` exists.
        """
        config = self.get_by_id(config_id, principal_id, active_only=False)
        if config is None:
            return False
        config.is_active = False
        config.updated_at = datetime.now(UTC)
        self._session.flush()
        return True
