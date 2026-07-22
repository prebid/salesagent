"""PushNotificationConfig repository — tenant-scoped data access.

Core invariant: every query includes both ``tenant_id`` AND ``principal_id``
in the WHERE clause. PushNotificationConfig rows belong to a single
(tenant, principal) pair; cross-principal lookups are not exposed.

Write methods add objects to the session but never commit — the Unit of Work
(``PushNotificationConfigUoW``) handles commit/rollback at the boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import PushNotificationConfig
from src.core.webhook_validator import WebhookURLValidator


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
        validation_token: str | None,
        session_id: str | None = None,
    ) -> tuple[PushNotificationConfig, bool]:
        """Insert or update a config within the (tenant, principal) scope.

        Returns:
            (config, created): ``created`` is True if a new row was inserted,
            False if an existing row was updated (or reactivated).

        Raises:
            ValueError: If ``url`` fails SSRF validation (same gate as protocol
                send and application webhook delivery).
        """
        is_valid, error_msg = WebhookURLValidator.validate_webhook_url(url)
        if not is_valid:
            raise ValueError(f"Invalid webhook URL: {error_msg}")

        existing = self.get_by_id(config_id, principal_id, active_only=False)
        now = datetime.now(UTC)

        if existing is not None:
            existing.url = url
            existing.authentication_type = authentication_type
            existing.authentication_token = authentication_token
            existing.validation_token = validation_token
            existing.session_id = session_id
            existing.updated_at = now
            existing.is_active = True
            self._session.flush()
            return existing, False

        config = PushNotificationConfig(
            id=config_id,
            tenant_id=self._tenant_id,
            principal_id=principal_id,
            session_id=session_id,
            url=url,
            authentication_type=authentication_type,
            authentication_token=authentication_token,
            validation_token=validation_token,
            is_active=True,
        )
        self._session.add(config)
        self._session.flush()
        return config, True

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
