"""Principal lookups for cross-cutting concerns outside the tenant-scoped
Account/Principal repositories (e.g. activity-feed logging).

A single module-level, session-owning read -- like
``adapter_config.read_adapter_config`` -- rather than a class, because there is
exactly one query here and no CRUD to group it with.
"""

from __future__ import annotations

from sqlalchemy import select

from src.core.database.models import Principal


def read_principal_name(tenant_id: str, principal_id: str) -> str | None:
    """The persisted display name for a principal, or ``None`` if not found.

    The ONE session-owning read for this lookup.
    ``src/core/helpers/activity_helpers.log_tool_activity`` (a cross-cutting
    concern called from every ``_impl``) previously opened its own
    ``get_db_session()`` -- the same D2 disease ``adapter_helpers.py`` had
    (#1721 M2). Returns the plain string, not the ORM row, so
    there is nothing to detach.
    """
    from src.core.database.database_session import get_db_session

    with get_db_session() as session:
        principal = session.scalars(select(Principal).filter_by(principal_id=principal_id, tenant_id=tenant_id)).first()
        return principal.name if principal else None
