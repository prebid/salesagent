"""Cross-tenant principal reads outside the tenant-scoped ``PrincipalRepository``: the
CI seed's token check and the bulk setup checklist.

Module-level reads -- like ``adapter_config.read_adapter_config`` -- rather than a
class, because each is one query with no CRUD to group it with. The activity-feed
name lookup that used to live here is gone: the identity carries the principal the
resolver loaded, name included.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.database.models import Principal


def find_principal_by_token_hash(session: Session, token_hash: str) -> Principal | None:
    """The principal holding ``token_hash``, whichever tenant it is in, or ``None``.

    Not the credential path: a request's token is resolved INSIDE the tenant the request
    addressed (``PrincipalRepository.find_by_token_hash``). This is seed maintenance --
    ``scripts/setup/init_database_ci.py`` asks whether its documented token already exists
    anywhere, because ``token_hash`` is unique across tenants and a stale row in another
    tenant has to be moved, not duplicated.
    """
    return session.scalars(select(Principal).filter_by(token_hash=token_hash)).first()


def count_principals_by_tenant(session: Session, tenant_ids: Iterable[str]) -> dict[str, int]:
    """How many principals each of *tenant_ids* holds, keyed by tenant_id.

    Cross-tenant by design: the bulk setup checklist grades many tenants in one query.
    A tenant with no principals is absent from the result. Takes the caller's session
    because it runs beside the sibling per-tenant counts in the same transaction.
    """
    stmt = (
        select(Principal.tenant_id, func.count())
        .where(Principal.tenant_id.in_(list(tenant_ids)))
        .group_by(Principal.tenant_id)
    )
    return dict(session.execute(stmt).tuples().all())
