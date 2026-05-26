"""PushNotificationConfig repository — tenant-scoped data access for buyer webhooks.

Encapsulates the lookups the order-approval and webhook delivery flows depend on:
finding a principal's most recent active webhook (for outbound notification
routing) and looking up a specific URL's auth config (for header construction).

Core invariant: every query includes ``tenant_id`` in the WHERE clause.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import PushNotificationConfig


class PushNotificationConfigRepository:
    """Tenant-scoped data access for ``PushNotificationConfig``.

    All queries filter by ``tenant_id`` automatically. Callers cannot bypass
    tenant isolation.

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

    def find_most_recent_active_for_principal(self, principal_id: str) -> PushNotificationConfig | None:
        """Return the principal's most recently created active webhook config.

        Used by the order-approval background polling service to route the
        completion / failure notification when the admin caller didn't pass
        an explicit URL. Returns ``None`` when the principal has not
        registered a webhook.
        """
        stmt = (
            select(PushNotificationConfig)
            .where(
                PushNotificationConfig.tenant_id == self._tenant_id,
                PushNotificationConfig.principal_id == principal_id,
                PushNotificationConfig.is_active.is_(True),
            )
            .order_by(PushNotificationConfig.created_at.desc())
        )
        return self._session.scalars(stmt).first()

    def find_active_by_url(self, principal_id: str, url: str) -> PushNotificationConfig | None:
        """Return the active webhook config matching a specific URL, or ``None``.

        Used by the webhook delivery path to look up the auth config (bearer
        token, basic credentials, validation token) for an outbound POST.
        """
        stmt = select(PushNotificationConfig).filter_by(
            tenant_id=self._tenant_id,
            principal_id=principal_id,
            url=url,
            is_active=True,
        )
        return self._session.scalars(stmt).first()
