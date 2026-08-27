"""Tenant-scoped access to this agent's own RFC 9421 signing keys.

Backs #1291 A2 (salesagent-z6nr.8). Every ``select(SigningKey)`` in the codebase
lives here — the structural guard forbids raw model queries outside the
repository layer, and the tenant scope is exactly what a stray query would drop.

Two selectors, and the difference matters:

* :meth:`active_at` — which key we SIGN with at an instant. Governed by the
  ``[not_before, not_after)`` window plus ``revoked_at``.
* :meth:`publishable_at` — which keys we PUBLISH at an instant. Governed by
  ``revoked_at`` plus the ``agent-signing-key`` schema's grace period, and by
  NEITHER window bound. Publication is window-blind by design.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from src.core.database.models import SigningKey
from src.core.signing_contract import REQUEST_SIGNING


class SigningKeyRepository:
    """Tenant-scoped CRUD for ``signing_keys``.

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

    def canonical_origin(self) -> str | None:
        """This tenant's canonical agent origin, read in THIS repository's transaction.

        The outbound webhook boundary (#1291 C1/D1) must resolve the origin — a
        ``tenants`` read — inside the transaction that already produced the key row it is
        about to sign with. Opening a second session there could observe a rotation from
        one side and the host from the other.

        THIS METHOD REPLACED A ``session`` PROPERTY (#1757). That property handed out the
        raw session and asked callers, in fourteen lines of docstring, to use it only for
        a sibling repository and to remember that the no-raw-select guards still applied
        to whoever borrowed it. It was a PRIMITIVE PLUS PROSE, and the prose was the only
        thing holding the invariant up: nothing stopped a caller opening a second session,
        which is the exact race the docstring warned about. Three call sites — the RFC
        9421 sender, the adapter client builder, and the test harness — each re-derived
        the same origin from it, identically, differing only in how they handled ``None``.
        One operation, written out three times, guarded by a request.

        Now the invariant is enforced BY CONSTRUCTION: a caller cannot open a second
        session because it never receives the first one. Returns ``None`` when the tenant
        row is absent, which is what every one of those call sites did by hand.
        """
        from src.core.agent_identity import agent_identity_for_tenant
        from src.core.database.repositories.tenant_config import TenantConfigRepository

        tenant = TenantConfigRepository(self._session, self._tenant_id).get_tenant()
        # The PURE half, on THIS session. agent_identity_for_tenant_id() would open a
        # TrustRootUoW of its own and read committed state — breaking the same-transaction
        # property the flush-visibility test grades.
        return agent_identity_for_tenant(tenant).origin if tenant is not None else None

    def _scope_prefix(self) -> tuple[ColumnElement[bool], ...]:
        """The tenant isolation term EVERY query composes.

        One home for the scope so an isolation fix lands once rather than once
        per query — a missed copy would silently publish or sign with another
        tenant's key material.
        """
        return (SigningKey.tenant_id == self._tenant_id,)

    def get_by_kid(self, kid: str) -> SigningKey | None:
        """Return this tenant's key with *kid*, or None.

        ``UNIQUE(tenant_id, kid)`` makes this at most one row. This is the
        resolution path when a caller designates a specific key — the mechanism
        that lets one signing surface use a different key from another
        (security.mdx: "isolation comes from the kid").
        """
        stmt = select(SigningKey).where(*self._scope_prefix(), SigningKey.kid == kid).limit(1)
        return self._session.scalars(stmt).first()

    def active_at(self, *, now: datetime, purpose: str = REQUEST_SIGNING) -> SigningKey | None:
        """Return the key to sign *purpose* with at *now*, or None.

        The window is half-open ``[not_before, not_after)`` — inclusive lower
        bound, exclusive upper — and ``not_after IS NULL`` reads as +infinity.
        The current key is always open-ended, so a NULL-blind predicate would
        leave every tenant with no signable key at all.

        ``revoked_at`` beats the window: a revoked open-ended key would otherwise
        resolve forever, since its window never closes.

        When rotation leaves two keys active, the newest signs. The tie-break is
        total (``created_at DESC, kid ASC``) so two rows created in one
        transaction cannot produce a nondeterministic signer.
        """
        stmt = (
            select(SigningKey)
            .where(
                *self._scope_prefix(),
                SigningKey.purpose == purpose,
                SigningKey.revoked_at.is_(None),
                SigningKey.not_before <= now,
                or_(SigningKey.not_after.is_(None), SigningKey.not_after > now),
            )
            .order_by(SigningKey.created_at.desc(), SigningKey.kid.asc())
            .limit(1)
        )
        return self._session.scalars(stmt).first()

    def publishable_at(
        self, *, now: datetime, grace_seconds: float, purpose: str = REQUEST_SIGNING
    ) -> list[SigningKey]:
        """Return every key to PUBLISH for *purpose* at *now*.

        Mirrors :meth:`active_at`'s scope and ordering, minus the window — and
        the omission is the whole point:

        * **Never ``not_after``.** Un-publishing a key whose window has closed
          strands every signature it made that is still inside its verification
          window — the exact gap rotation overlap exists to prevent.
        * **Never ``not_before``.** Rotation must publish the incoming key
          BEFORE it signs, so that every verifier's cache already holds it when
          the first signature arrives.

        Consequence, and it is a REQUIREMENT on callers rather than a caveat:
        **retirement MUST set ``revoked_at``** (:meth:`revoke`). Closing the
        window retires a SIGNER; it never retires a PUBLICATION, so a key
        retired only by ``not_after`` would be published forever.

        ``revoked_at`` is the sole exit, and it is delayed by *grace_seconds*:
        ``core/agent-signing-key.json`` says a revoked key "MAY continue to
        appear in the trust anchor during a grace period so caches that have not
        yet refreshed still find the key and can evaluate the revocation
        marker", and may be removed "once the cache TTL ... has elapsed across
        all verifiers". The published documents carry the marker for exactly
        that reason.

        *purpose* is filtered because ``security.mdx``:1079 forbids co-tenanting
        purposes in one JWKS ("governance signing keys MUST be served from a
        separate origin"). Latent today — ``MINTABLE_PURPOSES`` holds one value
        — and a latent violation of a MUST is still one. The SAME :1079
        divergence is knowingly repeated by
        :mod:`src.core.signing.revocation_list` — the combined revocation list
        is signed with this tenant's request-signing key rather than a
        separate governance key, for the same reason (none is mintable yet).
        Follow-up: ``salesagent-z6nr.37``.
        """
        cutoff = now - timedelta(seconds=grace_seconds)
        stmt = (
            select(SigningKey)
            .where(
                *self._scope_prefix(),
                SigningKey.purpose == purpose,
                or_(SigningKey.revoked_at.is_(None), SigningKey.revoked_at > cutoff),
            )
            .order_by(SigningKey.created_at.desc(), SigningKey.kid.asc())
        )
        return list(self._session.scalars(stmt).all())

    def all_revoked(self, *, purpose: str = REQUEST_SIGNING) -> list[SigningKey]:
        """Return every revoked key for *purpose* — the PERMANENT revocation record.

        A third selector, deliberately unlike the two above:

        * :meth:`active_at` picks a SIGNER (one row, window-bound).
        * :meth:`publishable_at` picks an expiring PUBLICATION SET (many rows,
          window-blind, but time-bound by ``grace_seconds``).
        * This one picks the PERMANENT RECORD the combined revocation list is
          (``security.mdx``:1328): every row ever revoked, with NO grace cutoff
          — a kid dropped from this list is a kid UN-REVOKED, which the schema
          never permits. Unlike :meth:`publishable_at`, an elapsed grace window
          changes nothing here; that is the entire reason this method exists
          rather than reusing ``publishable_at`` with ``grace_seconds=0``.

        *purpose* filters (unlike the combined list's own scope, which per
        :1328 covers "governance, request-signing, and any other agent signing
        keys") because this deployment mints exactly one purpose today
        (``MINTABLE_PURPOSES``) — the asymmetry with :meth:`publishable_at`'s
        *purpose* filter is therefore coincidental, not a contradiction: both
        filter on the one purpose that exists. Widening to "every purpose"
        needs no code change here when a second purpose is minted, only the
        caller passing it (or omitting the filter entirely at that point).
        """
        stmt = (
            select(SigningKey)
            .where(*self._scope_prefix(), SigningKey.purpose == purpose, SigningKey.revoked_at.is_not(None))
            .order_by(SigningKey.revoked_at.desc(), SigningKey.kid.asc())
        )
        return list(self._session.scalars(stmt).all())

    def create_from_keypair(
        self,
        *,
        kid: str,
        alg: str,
        purpose: str,
        public_jwk: dict[str, Any],
        private_key_ref: str,
        not_before: datetime,
        not_after: datetime | None = None,
        private_key_pem_encrypted: bytes | None = None,
    ) -> SigningKey:
        """Persist a freshly minted keypair's public half and private-key reference.

        The SOLE construction site for ``SigningKey`` — no ORM kwargs are
        assembled at a call site. ``private_key_ref`` is a scheme-prefixed
        reference (``db:<kid>`` / ``env:NAME`` / ``file:/abs/path``), never key
        material itself.

        ``private_key_pem_encrypted`` accompanies a ``db:`` ref and is the ONE
        thing this repository does accept in cipher form: the PKCS#8
        ``BEGIN ENCRYPTED PRIVATE KEY`` PEM the SDK emitted under the deployment
        KEK. NULL for every other scheme, whose material this process never sees.
        The gate that keeps it from ever holding plaintext lives at the single
        mint site (``src.core.signing.keys.provision_signing_key``), which
        refuses a ``db:`` mint outright when no KEK is configured.

        ``not_after`` defaults to NULL: a newly provisioned key is the current
        key and the current key is open-ended. Retirement is a later ``revoke``
        or an explicit window close, never a birth-time expiry.
        """
        key = SigningKey(
            id=f"sk_{uuid4().hex[:16]}",
            tenant_id=self._tenant_id,
            kid=kid,
            alg=alg,
            purpose=purpose,
            public_jwk=public_jwk,
            private_key_ref=private_key_ref,
            private_key_pem_encrypted=private_key_pem_encrypted,
            not_before=not_before,
            not_after=not_after,
        )
        self._session.add(key)
        self._session.flush()
        return key

    def revoke(self, kid: str, *, at: datetime) -> SigningKey | None:
        """Stamp ``revoked_at`` on this tenant's key *kid* and return it.

        The transition, so no caller hand-sets the column. Revocation is what
        retires an open-ended key — closing the window cannot, because the
        current key has no upper bound.
        """
        key = self.get_by_kid(kid)
        if key is None:
            return None
        key.revoked_at = at
        self._session.flush()
        return key
