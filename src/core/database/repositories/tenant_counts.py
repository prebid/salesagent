"""Row counts grouped by tenant: the one spelling of the bulk per-tenant count.

Cross-tenant by design, like ``tenant_lookup``: the bulk setup checklist and the
admin tenant list grade a page of tenants with one grouped query per metric
rather than one query per tenant. A model whose rows are repository-private
(``Principal``) or whose predicate belongs to its repository
(``PublisherPartner``'s verified rule) keeps a named wrapper in its own module
that calls this.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session


def count_by_tenant(
    session: Session,
    tenant_column: InstrumentedAttribute[str],
    tenant_ids: Iterable[str],
    *where: ColumnElement[bool],
) -> dict[str, int]:
    """How many rows each of *tenant_ids* holds in *tenant_column*'s table, keyed by tenant_id.

    *where* narrows the rows counted. A tenant with no matching rows is absent from the
    result. Takes the caller's session because the counts run beside each other in one
    transaction.
    """
    stmt = (
        select(tenant_column, func.count()).where(tenant_column.in_(list(tenant_ids)), *where).group_by(tenant_column)
    )
    return dict(session.execute(stmt).tuples().all())
