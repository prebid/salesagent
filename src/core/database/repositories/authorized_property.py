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
from src.core.helpers.publisher_property_helpers import AuthorizedPropertyRef


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

    def _verified(self) -> tuple[ColumnElement[bool], ...]:
        """The tenant scope narrowed to VERIFIED properties.

        A pending or failed property is one whose publisher has not been seen to
        authorize this agent (``PropertyVerificationService`` fetches that publisher's
        adagents.json and finds this agent there, or does not). So every read that names
        a publisher to a buyer composes this predicate rather than the bare scope: every
        surface that tells a buyer which publishers this seller represents must give the
        same answer, and one clause is how they agree.
        """
        return (*self._scope_prefix(), AuthorizedProperty.verification_status == "verified")

    def list_for_publisher_domain(self, publisher_domain: str) -> list[AuthorizedProperty]:
        """This tenant's properties on *publisher_domain*, oldest-registered first, whatever their status.

        Used to build the adagents.json served at the tenant's own host, where the tenant
        IS the publisher. That is the one read that does not compose :meth:`_verified`:
        verifying a property fetches the adagents.json at its domain, so for a property on
        this host the verification reads the very document built from this list. Requiring
        ``verified`` here would mean no self-hosted property could ever become verified.

        A tenant whose agent host is not itself a publisher property domain gets an empty
        list, and the route then serves no document at all rather than a self-attested one.
        """
        stmt = (
            select(AuthorizedProperty)
            .where(*self._scope_prefix(), AuthorizedProperty.publisher_domain == publisher_domain)
            .order_by(AuthorizedProperty.created_at.asc(), AuthorizedProperty.property_id.asc())
        )
        return list(self._session.scalars(stmt).all())

    def list_refs(self) -> list[AuthorizedPropertyRef]:
        """This tenant's VERIFIED properties, as values, by publisher.

        What a product's selectors resolve against (#1845): a product names the publishers
        these rows belong to, never the tenant's own host. Composes :meth:`_verified`, so a
        product names exactly the publishers the capabilities portfolio names; a pending
        property from the add form or an upload is not sold until its publisher is seen to
        authorize this agent. Values rather than rows because ``get_products`` reads them
        again after its session has closed.
        """
        stmt = (
            select(AuthorizedProperty)
            .where(*self._verified())
            .order_by(AuthorizedProperty.publisher_domain.asc(), AuthorizedProperty.property_id.asc())
        )
        return [
            AuthorizedPropertyRef(
                property_id=row.property_id, publisher_domain=row.publisher_domain, tags=tuple(row.tags or ())
            )
            for row in self._session.scalars(stmt)
        ]

    def list_verified_publisher_domains(self) -> list[str]:
        """The distinct publisher domains of this tenant's verified properties, sorted."""
        stmt = (
            select(AuthorizedProperty.publisher_domain)
            .where(*self._verified())
            .distinct()
            .order_by(AuthorizedProperty.publisher_domain)
        )
        return list(self._session.scalars(stmt).all())
