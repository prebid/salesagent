"""Tenant-scoped access to ``principals`` rows: the one place the model is named.

The ORM ``Principal`` is repository-private: ``ruff-boundary.toml`` bans importing it
anywhere outside this package. The resolver's primitives (``src/core/auth_utils``) come
here for the row a token or a stored id names, and the identity carries what they loaded;
the admin views and the seed scripts come here to list, count, create and delete rows.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from src.core.database.models import MediaBuy, Principal, PrincipalSigningKeyid


class PrincipalRepository:
    """Read access to one tenant's principals.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Every query is scoped to this tenant.
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def get(self, principal_id: str) -> Principal | None:
        """The principal with ``principal_id`` in this tenant, or ``None``."""
        return self._session.scalars(
            select(Principal).filter_by(tenant_id=self._tenant_id, principal_id=principal_id)
        ).first()

    def find_by_token_hash(self, token_hash: str) -> Principal | None:
        """The principal of this tenant holding ``token_hash``, or ``None``.

        Scoped to the tenant like every other read here: a token minted for one tenant
        never resolves inside another. The caller hashes; the plaintext never arrives.
        """
        return self._session.scalars(
            select(Principal).filter_by(tenant_id=self._tenant_id, token_hash=token_hash)
        ).first()

    def find_by_agent_url(self, agent_url: str) -> Principal | None:
        """The principal of this tenant onboarded at ``agent_url``, or ``None``.

        For seeds that find or create a counterparty by its agent URL. Unique within the
        tenant (``uq_principals_tenant_agent_url``), so the answer is one row.
        """
        return self._session.scalars(
            select(Principal).filter_by(tenant_id=self._tenant_id, agent_url=agent_url)
        ).first()

    def find_by_signing_keyid(self, keyid: str) -> Principal | None:
        """The principal of this tenant that ``keyid`` is onboarded for, or ``None``.

        security.mdx @ v3.1.1 L1088-1094, checklist step 1: a keyid resolves to one
        ``agents[]`` entry. ``principal_signing_keyids`` maps it to one principal, and its
        primary key ``(tenant_id, keyid)`` makes this one row. The keyid is unverified when
        this runs; the verifier establishes the principal.
        """
        return self._session.scalars(
            select(Principal)
            .join(
                PrincipalSigningKeyid,
                (PrincipalSigningKeyid.tenant_id == Principal.tenant_id)
                & (PrincipalSigningKeyid.principal_id == Principal.principal_id),
            )
            .filter(PrincipalSigningKeyid.tenant_id == self._tenant_id, PrincipalSigningKeyid.keyid == keyid)
        ).first()

    def onboard_signing_keyids(self, principal_id: str, keyids: Iterable[str]) -> None:
        """Onboard *keyids* for *principal_id* in this tenant: an idempotent add.

        Keyids already mapped to *principal_id* are left as they are; the missing ones are
        added. The principal must exist (the foreign key refuses otherwise), and a keyid
        already onboarded at another counterparty of this tenant is refused by the primary
        key. The caller commits.
        """
        mapped = set(
            self._session.scalars(
                select(PrincipalSigningKeyid.keyid).filter_by(tenant_id=self._tenant_id, principal_id=principal_id)
            )
        )
        for keyid in sorted(set(keyids) - mapped):
            self._session.add(PrincipalSigningKeyid(tenant_id=self._tenant_id, keyid=keyid, principal_id=principal_id))

    def list_all(self) -> list[Principal]:
        """Every principal in this tenant, ordered by display name."""
        return list(
            self._session.scalars(select(Principal).filter_by(tenant_id=self._tenant_id).order_by(Principal.name)).all()
        )

    def count(self) -> int:
        """How many principals this tenant holds."""
        return (
            self._session.scalar(select(func.count()).select_from(Principal).filter_by(tenant_id=self._tenant_id)) or 0
        )

    def issue(self, **fields: Any) -> tuple[Principal, str]:
        """A new principal of this tenant with a freshly minted token, added to the session.

        Returns the row and the plaintext token, which is shown once and stored nowhere;
        the row carries only its hash (``Principal.issue``). The caller commits.
        """
        row, token = Principal.issue(tenant_id=self._tenant_id, **fields)
        self._session.add(row)
        return row, token

    def create_with_token(self, token: str, **fields: Any) -> Principal:
        """A new principal of this tenant whose token is *token*, added to the session.

        For seeds and CI fixtures whose token is documented in advance; the row still
        stores only the hash (``Principal.with_token``). The caller commits.
        """
        row = Principal.with_token(token, tenant_id=self._tenant_id, **fields)
        self._session.add(row)
        return row

    def delete_all(self) -> None:
        """Delete every principal of this tenant (a tenant hard-delete)."""
        self._session.execute(delete(Principal).where(Principal.tenant_id == self._tenant_id))

    def revenue_by_name(
        self, *, since: datetime, statuses: list[str], limit: int
    ) -> list[tuple[str | None, Decimal | None]]:
        """``(principal name, summed media-buy budget)`` for the top *limit* principals.

        Counts the buys created at or after *since* whose status is in *statuses*;
        highest revenue first. The admin dashboard's revenue chart.
        """
        stmt = (
            select(Principal.name, func.sum(MediaBuy.budget).label("revenue"))
            .join(
                MediaBuy,
                (MediaBuy.principal_id == Principal.principal_id) & (MediaBuy.tenant_id == Principal.tenant_id),
            )
            .filter(
                MediaBuy.tenant_id == self._tenant_id,
                MediaBuy.created_at >= since,
                MediaBuy.status.in_(statuses),
            )
            .group_by(Principal.name)
            .order_by(func.sum(MediaBuy.budget).desc())
            .limit(limit)
        )
        return [(name, revenue) for name, revenue in self._session.execute(stmt).all()]
