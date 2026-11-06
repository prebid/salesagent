"""Tenant-scoped access to ``oauth_access_tokens`` rows: the one definition of a live token.

Every statement against the table lives here. The table is the access token
(:class:`~src.core.database.models.OAuthAccessToken`): the mint path writes the hash of a
string it returns once, and the verify path asks this repository whether a presented hash
names a usable token. Nothing else may query it — a stray ``select()`` would be a second
spelling of "usable", and the two would drift the way the replaced JWT's claims drifted
from the rows they were checked against.

Scoping is constructor-bound, as in :class:`~src.core.database.repositories.principal.PrincipalRepository`:
``tenant_id`` is required at construction and applied by every ``select()`` in the class,
so there is no spelling of a lookup here that reaches another tenant's row
(``tests/unit/test_cross_tenant_query_isolation.py``). That is what lets the verifier drop
the JWT's ``tenant`` claim entirely rather than re-checking it: a token minted for one
tenant cannot resolve inside another, because the resolver builds this repository with the
tenant the request named and the hash is only ever matched inside it.

The caller commits. Like the other repositories hung off a Unit of Work, nothing here
opens or closes a transaction; the mint path commits the row beside the response it
returns the plaintext in.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from src.core.database.models import OAuthAccessToken


class OAuthAccessTokenRepository:
    """Mint, verify, revoke and prune one tenant's MCP OAuth access tokens.

    Args:
        session: SQLAlchemy session (caller manages lifecycle and commits).
        tenant_id: Every query is scoped to this tenant. Constructor-bound and required,
            for the reason in the module docstring: the scope is the reason a verified
            hash needs no tenant claim beside it.
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def mint(
        self,
        *,
        token_hash: str,
        client_id: str,
        principal_id: str,
        scopes: list[str],
        resource: str,
        expires_at: datetime,
    ) -> OAuthAccessToken:
        """Record an issued token, returning the row. The caller commits.

        ``token_hash`` is ``src.core.credentials.hash_token`` of the string the token
        endpoint returns; the plaintext does not arrive here and is stored nowhere. Every
        other argument is a fact the verifier would otherwise have had to trust a claim
        for — the client and principal the grant named, the scopes it carried, and the
        RFC 8707 ``resource`` the token is bound to.
        """
        access_token = OAuthAccessToken(
            token_hash=token_hash,
            tenant_id=self._tenant_id,
            client_id=client_id,
            principal_id=principal_id,
            scopes=scopes,
            resource=resource,
            expires_at=expires_at,
        )
        self._session.add(access_token)
        return access_token

    def find_live(self, token_hash: str, *, now: datetime) -> OAuthAccessToken | None:
        """The usable token of this tenant with ``token_hash`` at ``now``, or ``None``.

        The single verification query, and the ONE definition of usable: the hash matches
        within this tenant, ``expires_at`` is still ahead of ``now``, and ``revoked_at``
        is NULL. All three are decided in SQL rather than returned to the caller to test,
        so an expired or revoked token is indistinguishable from an absent one at every
        call site and no caller can forget one of the conditions.

        The caller hashes what the request presented; the plaintext never arrives.
        """
        return self._session.scalars(
            select(OAuthAccessToken).where(
                OAuthAccessToken.tenant_id == self._tenant_id,
                OAuthAccessToken.token_hash == token_hash,
                OAuthAccessToken.expires_at > now,
                OAuthAccessToken.revoked_at.is_(None),
            )
        ).first()

    def revoke(self, token_hash: str, *, at: datetime) -> bool:
        """Revoke this tenant's token with ``token_hash`` at ``at``; True iff one turned.

        Exact, because the row IS the token: once committed, the next ``find_live`` for
        that hash answers ``None``. Returns False when the hash names nothing in this
        tenant or was already revoked, so a double revocation is reported rather than
        silently succeeding — ``revoked_at`` keeps the time of the FIRST revocation.
        """
        result = self._session.execute(
            update(OAuthAccessToken)
            .where(
                OAuthAccessToken.tenant_id == self._tenant_id,
                OAuthAccessToken.token_hash == token_hash,
                OAuthAccessToken.revoked_at.is_(None),
            )
            .values(revoked_at=at)
        )
        # Result.rowcount is provided by DBAPI cursors but typed loosely on SQLAlchemy's
        # base Result protocol; the concrete CursorResult an UPDATE returns always carries
        # it. Same accessor as IdempotencyAttemptRepository.expire_old.
        return int(getattr(result, "rowcount", 0) or 0) == 1

    def delete_expired(self, *, before: datetime) -> int:
        """Delete this tenant's tokens that expired before ``before``; return the count.

        For a periodic pruning job, served by ``idx_oauth_access_tokens_expires_at``.
        Correctness never depends on it running: ``find_live`` filters on expiry, so an
        expired row is already indistinguishable from an absent one and only storage
        growth is at stake. Scoped to ``tenant_id`` like every read here, so cross-tenant
        cleanup is impossible from a single repository.

        Revoked-but-unexpired rows are deliberately left: they are what makes a revoked
        token stay refused until its own expiry, and deleting one re-opens nothing only
        because nothing can re-mint the same hash — the entropy, not the row, is what
        prevents that, so the row stays until expiry says it is dead for everyone.
        """
        result = self._session.execute(
            delete(OAuthAccessToken).where(
                OAuthAccessToken.tenant_id == self._tenant_id,
                OAuthAccessToken.expires_at < before,
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)
