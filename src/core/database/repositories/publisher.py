"""Tenant-scoped access to the publishers a seller sells for, and what their files authorize.

A publisher row holds the last valid adagents.json its domain served; the authorized
properties and the format catalog are derived from it (``src/services/publisher_ingest.py``)
and written here, in the same unit of work as the document, wholesale. Nothing else writes
them, so a property is authorized exactly when a publisher's stored file says so.

Authorization is read, never stored: a publisher authorizes this agent while its document
is fresh (``Publisher.is_fresh``) and an authorized property is in effect
(``PublisherProperty.in_effect``). That rule is evaluated in one place,
:func:`authorized_properties`, and every reader -- ``get_products``, ``create_media_buy``,
the capabilities portfolio, the admin pages, the setup checklist -- reaches it from here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from src.core.database.database_session import get_db_session
from src.core.database.integrity import resolve_or_write
from src.core.database.models import Publisher, PublisherFormatOption, PublisherProperty
from src.core.publisher_format_catalog import PublisherCatalog, authorized_properties


def _with_derived_rows() -> tuple:
    return (selectinload(Publisher.properties), selectinload(Publisher.format_options))


def count_authorized_by_tenant(
    session: Session, tenant_ids: Iterable[str], now: datetime
) -> dict[str, tuple[int, int]]:
    """``(authorizing publishers, authorized properties)`` at *now* for each of *tenant_ids*.

    Cross-tenant by design: the bulk setup checklist grades many tenants in one read. A
    tenant with no publisher is absent from the result.
    """
    by_tenant: dict[str, list[Publisher]] = {}
    stmt = select(Publisher).options(*_with_derived_rows()).where(Publisher.tenant_id.in_(list(tenant_ids)))
    for publisher in session.scalars(stmt):
        by_tenant.setdefault(publisher.tenant_id, []).append(publisher)
    return {tenant_id: _counts(publishers, now) for tenant_id, publishers in by_tenant.items()}


def _counts(publishers: Iterable[Publisher], now: datetime) -> tuple[int, int]:
    authorized = [authorized_properties(publisher, now) for publisher in publishers]
    return sum(1 for rows in authorized if rows), sum(len(rows) for rows in authorized)


def every_publisher() -> list[tuple[str, str]]:
    """``(tenant_id, domain)`` of every publisher of every tenant, for the scheduled refresh.

    Cross-tenant by design: the refresh walks all of them and ingests each in a unit of work
    of its own.
    """
    with get_db_session() as session:
        stmt = select(Publisher.tenant_id, Publisher.domain).order_by(Publisher.tenant_id, Publisher.domain)
        return [(tenant_id, domain) for tenant_id, domain in session.execute(stmt).tuples()]


class PublisherRepository:
    """Tenant-scoped access to ``publishers`` and the rows derived from their documents.

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

    # ── publishers ─────────────────────────────────────────────────────────

    def list_all(self) -> list[Publisher]:
        """This tenant's publishers by domain, with their derived properties and format options loaded."""
        stmt = (
            select(Publisher)
            .options(*_with_derived_rows())
            .where(Publisher.tenant_id == self._tenant_id)
            .order_by(Publisher.domain)
        )
        return list(self._session.scalars(stmt).all())

    def get(self, domain: str) -> Publisher | None:
        """This tenant's publisher at canonical *domain*, or ``None``."""
        return self._session.get(Publisher, (self._tenant_id, domain))

    def add_publisher(self, domain: str, display_name: str | None = None) -> bool:
        """Add a publisher at canonical *domain*, never fetched; ``False`` when the tenant already holds it.

        The primary key is the authority, so the loser of a concurrent add gets the same
        answer as a pre-check would have given (``resolve_or_write``). The unit of work commits.
        """
        publisher = Publisher(tenant_id=self._tenant_id, domain=domain, display_name=display_name or domain)
        taken = resolve_or_write(
            self._session,
            conflict=lambda: self.get(domain),
            write=lambda: self._session.add(publisher),
            constraint="publishers_pkey",
        )
        return taken is None

    def delete(self, domain: str) -> bool:
        """Remove this tenant's publisher at *domain*, and everything derived from or selecting it."""
        publisher = self.get(domain)
        if publisher is None:
            return False
        self._session.delete(publisher)
        return True

    # ── what ingest writes ─────────────────────────────────────────────────

    def store_document(
        self,
        publisher: Publisher,
        document: dict[str, Any],
        *,
        fetched_at: datetime,
        last_updated: datetime | None,
        catalog_etag: str | None,
        properties: Sequence[Mapping[str, Any]],
        format_options: Sequence[Mapping[str, Any]],
    ) -> None:
        """Store *document* as *publisher*'s last valid adagents.json and replace what is derived from it.

        *properties* and *format_options* are the column values of the new
        :class:`PublisherProperty` and :class:`PublisherFormatOption` rows; the old rows go.
        """
        for model in (PublisherProperty, PublisherFormatOption):
            self._session.execute(
                delete(model).where(model.tenant_id == self._tenant_id, model.publisher_domain == publisher.domain)
            )
        self._session.expire(publisher, ["properties", "format_options"])
        self._session.add_all(
            PublisherProperty(tenant_id=self._tenant_id, publisher_domain=publisher.domain, **values)
            for values in properties
        )
        self._session.add_all(
            PublisherFormatOption(tenant_id=self._tenant_id, publisher_domain=publisher.domain, **values)
            for values in format_options
        )
        publisher.adagents = document
        publisher.last_updated = last_updated
        publisher.catalog_etag = catalog_etag
        publisher.fetched_at = fetched_at
        publisher.fetch_attempted_at = fetched_at
        publisher.fetch_status = "ok"
        publisher.fetch_error = None
        self._session.flush()

    def record_failed_fetch(self, publisher: Publisher, *, status: str, error: str, attempted_at: datetime) -> None:
        """Record a failed fetch of *publisher*: its stored document and ``fetched_at`` stay as they were."""
        publisher.fetch_status = status
        publisher.fetch_error = error
        publisher.fetch_attempted_at = attempted_at
        self._session.flush()

    # ── what readers see ───────────────────────────────────────────────────

    def catalog(self, now: datetime) -> PublisherCatalog:
        """What this tenant's publishers authorize at *now* (:meth:`PublisherCatalog.of`)."""
        return PublisherCatalog.of(self.list_all(), now)

    def authorizing_domains(self, now: datetime) -> list[str]:
        """The domains of this tenant's publishers that authorize this agent at *now*, as stored.

        Unlike :meth:`catalog`, a domain no wire spelling exists for (a port) is returned: the
        capabilities portfolio reports it as a degradation rather than dropping it unseen.
        """
        return [publisher.domain for publisher in self.list_all() if authorized_properties(publisher, now)]

    def count_authorized(self, now: datetime) -> tuple[int, int]:
        """``(authorizing publishers, authorized properties)`` of this tenant at *now*."""
        return _counts(self.list_all(), now)

    def list_for_publisher_domain(self, publisher_domain: str) -> list[PublisherProperty]:
        """The properties the stored file at *publisher_domain* authorizes this agent for, in file order.

        Used to build the adagents.json served at the tenant's own host, where the tenant IS
        the publisher. That is the one read that does not ask whether the document is fresh:
        refreshing a publisher at this host fetches the very document built from this list,
        so requiring a fresh document here would mean it could never become fresh.
        """
        stmt = (
            select(PublisherProperty)
            .where(
                PublisherProperty.tenant_id == self._tenant_id, PublisherProperty.publisher_domain == publisher_domain
            )
            .order_by(PublisherProperty.id)
        )
        return list(self._session.scalars(stmt).all())
