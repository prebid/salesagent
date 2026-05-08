"""TenantSigningCredential repository — outbound signing key references.

PR 1 of [signing-non-embedded](../../../../docs/design/signing-non-embedded.md).
Stores KMS references + cached public JWKs for the salesagent's own outbound
signing. Private bytes never live here for KMS-backed credentials; ``backend_ref``
is the lookup key the SigningProvider uses to talk to the backend.

Cache coherence: the per-process snapshot cache in
``src.services.webhook_signing`` is evicted automatically on commit via a
SQLAlchemy session listener registered at module load there. The repo
itself does not couple to the cache — invalidation is the listener's
job, fired post-commit so a concurrent reader can't re-cache the
about-to-be-rotated kid.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import TenantSigningCredential

VALID_BACKENDS = ("local_pem", "gcp_kms", "aws_kms", "hashicorp_vault")


class TenantSigningCredentialRepository:
    """Tenant-scoped CRUD against ``tenant_signing_credentials``."""

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def list_for_purpose(self, purpose: str, *, include_inactive: bool = False) -> list[TenantSigningCredential]:
        stmt = (
            select(TenantSigningCredential)
            .where(
                TenantSigningCredential.tenant_id == self._tenant_id,
                TenantSigningCredential.purpose == purpose,
            )
            .order_by(TenantSigningCredential.created_at)
        )
        if not include_inactive:
            stmt = stmt.where(TenantSigningCredential.is_active.is_(True))
        return list(self._session.scalars(stmt).all())

    def get_active(self, purpose: str) -> TenantSigningCredential | None:
        """Return the (single) active credential for a purpose, if one exists.

        Caller is responsible for ensuring at most one is active per
        (tenant, purpose) at a time — rotation should set ``rotated_out_at``
        and ``is_active=False`` on the outgoing row before activating the new one.
        """
        rows = self.list_for_purpose(purpose, include_inactive=False)
        return rows[0] if rows else None

    def get_by_kid(self, purpose: str, key_id: str) -> TenantSigningCredential | None:
        stmt = select(TenantSigningCredential).where(
            TenantSigningCredential.tenant_id == self._tenant_id,
            TenantSigningCredential.purpose == purpose,
            TenantSigningCredential.key_id == key_id,
        )
        return self._session.scalars(stmt).first()

    def create(
        self,
        *,
        purpose: str,
        backend: str,
        backend_ref: str,
        public_jwk: dict,
        key_id: str,
    ) -> TenantSigningCredential:
        if backend not in VALID_BACKENDS:
            raise ValueError(f"backend must be one of {VALID_BACKENDS}; got {backend!r}")
        row = TenantSigningCredential(
            tenant_id=self._tenant_id,
            purpose=purpose,
            backend=backend,
            backend_ref=backend_ref,
            public_jwk=public_jwk,
            key_id=key_id,
            is_active=True,
        )
        self._session.add(row)
        return row

    def rotate_out(self, purpose: str, key_id: str, *, now: datetime | None = None) -> bool:
        row = self.get_by_kid(purpose, key_id)
        if row is None:
            return False
        row.is_active = False
        row.rotated_out_at = now or datetime.now(UTC)
        return True
