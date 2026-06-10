"""AuthorizedProperty repository — tenant-scoped reads.

The faithful property-list intersection (see ``src/services/property_intersection.py``)
needs to resolve ``by_tag`` and ``by_id`` selectors to concrete properties at
query time — the legacy per-product filter in ``src/core/tools/products.py``
skipped ``by_tag`` entirely and silently dropped the product. This repository
encapsulates the lookups that drive the faithful resolution.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import AuthorizedProperty


class AuthorizedPropertyRepository:
    """Tenant-scoped read access for ``AuthorizedProperty`` rows.

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

    def list_by_domain(self, publisher_domain: str) -> list[AuthorizedProperty]:
        """Return every authorized property registered under ``publisher_domain``."""
        stmt = select(AuthorizedProperty).where(
            AuthorizedProperty.tenant_id == self._tenant_id,
            AuthorizedProperty.publisher_domain == publisher_domain,
        )
        return list(self._session.scalars(stmt).all())

    def list_by_ids(self, publisher_domain: str, property_ids: list[str]) -> list[AuthorizedProperty]:
        """Return ``publisher_domain``'s properties whose ``property_id`` is in ``property_ids``.

        Used by the faithful intersection to resolve a product's ``by_id``
        selectors — whose ``property_ids`` are AuthorizedProperty IDs (slugs),
        not identifier values. The lookup is publisher-scoped because the spec
        requires ``publisher_domain`` on the by_id selector: slugs like
        ``homepage`` are only unique per publisher, so an unscoped lookup could
        resolve a slug authored for one publisher against another's row.
        """
        if not property_ids:
            return []
        stmt = select(AuthorizedProperty).where(
            AuthorizedProperty.tenant_id == self._tenant_id,
            AuthorizedProperty.publisher_domain == publisher_domain,
            AuthorizedProperty.property_id.in_(property_ids),
        )
        return list(self._session.scalars(stmt).all())

    def list_by_tags(self, publisher_domain: str, tags: list[str]) -> list[AuthorizedProperty]:
        """Return properties under ``publisher_domain`` whose tags overlap ``tags``.

        Performs the overlap filter client-side after a single domain-scoped
        fetch — for a typical publisher with dozens of properties this is
        cheaper than a JSONB indexed query, and avoids dialect-specific
        operator handling at the SQLAlchemy layer. Switch to a JSONB
        ``?|`` query if a publisher's property count grows past a few hundred
        and the overhead shows up in profiles.
        """
        if not tags:
            return []
        tag_set = {t for t in tags if t}
        if not tag_set:
            return []
        properties = self.list_by_domain(publisher_domain)
        return [p for p in properties if p.tags and tag_set & set(p.tags)]
