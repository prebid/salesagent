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

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.integrity import resolve_or_write
from src.core.database.models import PushNotificationConfig
from src.core.webhook_validator import WebhookURLValidator

# Preserve-if-not-passed sentinel for upsert(): distinguishes "caller did not
# supply this field" (keep the existing row's value) from an explicit None
# (clear it). Without it, migrating callers onto upsert() would silently null
# a validation_token set through another path for the same config id.
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
        stmt = select(PushNotificationConfig).where(
            PushNotificationConfig.tenant_id == self._tenant_id,
            PushNotificationConfig.principal_id == principal_id,
            PushNotificationConfig.id == config_id,
        )
        if active_only:
            stmt = stmt.where(PushNotificationConfig.is_active.is_(True))
        return self._session.scalars(stmt).first()

    def list_active_by_principal(self, principal_id: str) -> list[PushNotificationConfig]:
        """Return all active configs for a principal within this tenant."""
        return list(
            self._session.scalars(
                select(PushNotificationConfig).where(
                    PushNotificationConfig.tenant_id == self._tenant_id,
                    PushNotificationConfig.principal_id == principal_id,
                    PushNotificationConfig.is_active.is_(True),
                )
            ).all()
        )

    def get_active_by_url(self, principal_id: str, url: str) -> PushNotificationConfig | None:
        """Return the active config with this exact URL, if any."""
        return self._session.scalars(
            select(PushNotificationConfig).where(
                PushNotificationConfig.tenant_id == self._tenant_id,
                PushNotificationConfig.principal_id == principal_id,
                PushNotificationConfig.url == url,
                PushNotificationConfig.is_active.is_(True),
            )
        ).first()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def upsert(
        self,
        *,
        config_id: str,
        principal_id: str,
        url: str,
        authentication_type: str | None,
        authentication_token: str | None,
        validation_token: str | None = _UNSET,
        session_id: str | None = _UNSET,
        webhook_secret: str | None = _UNSET,
    ) -> tuple[PushNotificationConfig, bool]:
        """Insert or update a config within the (tenant, principal) scope.

        ``validation_token`` / ``session_id`` / ``webhook_secret`` are
        preserve-if-not-passed: omitting one keeps the existing row's value
        (``None`` on insert); passing ``None`` explicitly clears it.

        Returns:
            (config, created): ``created`` is True if a new row was inserted,
            False if an existing row was updated (or reactivated).

        Raises:
            ValueError: If ``url`` fails the *registration* SSRF gate
                (``WebhookURLValidator.validate_webhook_url_registration`` —
                no DNS; optional localhost under ``ADCP_TESTING``). Deliberate
                defense-in-depth: callers also gate before upsert. Outbound
                protocol send uses ``validate_outbound_webhook_url``;
                application delivery (``kind="Application"``) uses the same
                ``reject_unsafe_outbound_webhook_url`` /
                ``validate_outbound_webhook_url`` path.
        """
        is_valid, error_msg = WebhookURLValidator.validate_webhook_url_registration(url)
        if not is_valid:
            raise ValueError(f"Invalid webhook URL: {error_msg}")

        existing = self.get_by_id(config_id, principal_id, active_only=False)
        now = datetime.now(UTC)

        if existing is not None:
            existing.url = url
            existing.authentication_type = authentication_type
            existing.authentication_token = authentication_token
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
            url=url,
            authentication_type=authentication_type,
            authentication_token=authentication_token,
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
        *,
        principal_id: str,
        url: str,
        authentication_type: str | None,
        authentication_token: str | None,
        webhook_secret: str | None,
    ) -> PushNotificationConfig | None:
        """Race-safe admin registration of a webhook URL.

        Returns the EXISTING active config when this URL is already registered
        for the principal — whether the pre-check saw it or this call lost the
        insert race to a concurrent registration — and ``None`` when a config
        was created (or a soft-deleted one reactivated).
        """

        def conflict() -> PushNotificationConfig | None:
            return self.get_active_by_url(principal_id, url)

        def write() -> None:
            self.upsert(
                config_id=self.admin_config_id(self._tenant_id, principal_id, url),
                principal_id=principal_id,
                url=url,
                authentication_type=authentication_type,
                authentication_token=authentication_token,
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
