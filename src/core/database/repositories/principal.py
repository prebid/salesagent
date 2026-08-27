"""Principal repository — token lookup for the auth and signing boundaries.

The bearer-token lookup is the one Principal query the request path runs before any
business logic: ``src.core.auth_utils.get_principal_from_token`` needs it to name the
caller, and the inbound RFC 9421 verifier (#1291 B1) needs the SAME row for its
``agent_url`` (the counterparty's key-resolution key) and to answer the spec's
composition rule — "does this bearer resolve to a principal the verifier accepts?"
(``security.mdx`` :1268-1270).

One method, one statement, both callers. Two copies of "find the principal for this
token" is exactly the duplication that lets a tenant-scoping fix land in one of them.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import Principal


class PrincipalRepository:
    """Access to principals by their API credential.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_token(self, token: str, tenant_id: str | None = None) -> Principal | None:
        """The principal holding *token*, or None.

        With *tenant_id* the search is confined to that tenant — a token is unique
        deployment-wide, but a caller that already resolved a tenant from the Host must
        not be able to authenticate against a different one. Without it the search is
        global and the caller discovers the tenant from the row.
        """
        stmt = select(Principal).filter_by(access_token=token)
        if tenant_id:
            stmt = stmt.filter_by(tenant_id=tenant_id)
        return self._session.scalars(stmt).first()
