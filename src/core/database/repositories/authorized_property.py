"""Tenant-scoped access to the properties this agent is authorized to represent.

Introduced for the trust root (#1291 A3, salesagent-z6nr.9): the adagents.json
we publish may only claim authorizations that a stored record backs, so the
claim needs a typed read rather than an inline query at the route.

The domain filter lives here rather than at the caller because it is a
CORRECTNESS rule, not a convenience: an adagents.json served at our own host
speaks for the properties on THAT host and no others.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from src.core.database.models import AuthorizedProperty


class AuthorizedPropertyRepository:
    """Tenant-scoped reads over ``authorized_properties``.

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

    def _scope_prefix(self) -> tuple[ColumnElement[bool], ...]:
        """The tenant isolation term EVERY query composes."""
        return (AuthorizedProperty.tenant_id == self._tenant_id,)

    def list_for_publisher_domain(self, publisher_domain: str) -> list[AuthorizedProperty]:
        """This tenant's properties on *publisher_domain*, oldest-registered first.

        Used to build the adagents.json served at that domain. A tenant whose
        agent host is not itself a publisher property domain gets an empty list
        — and therefore a document claiming no authorization, which is the
        honest answer rather than a self-attested one.
        """
        stmt = (
            select(AuthorizedProperty)
            .where(*self._scope_prefix(), AuthorizedProperty.publisher_domain == publisher_domain)
            .order_by(AuthorizedProperty.created_at.asc(), AuthorizedProperty.property_id.asc())
        )
        return list(self._session.scalars(stmt).all())
