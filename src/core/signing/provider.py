"""Resolve a ``SigningProvider`` for a tenant: row -> ref -> PEM -> tripwire -> provider.

We construct ``adcp.signing.InMemorySigningProvider``; we never subclass it and
never implement the Protocol ourselves. Its ``__init__`` already type- and
curve-checks the private key against the algorithm, so this module does not.

Contract reminder, because getting it wrong is silent: ``sign`` is ASYNC, and
``key_id()`` / ``algorithm()`` are METHODS. Attribute access yields a truthy
bound method, which is how ``<bound method ...>`` ends up in a ``keyid`` header.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import NamedTuple, Protocol

from adcp.signing import InMemorySigningProvider, load_private_key_pem, pem_to_adcp_jwk
from adcp.signing.autosign import SigningConfig
from adcp.signing.crypto import PrivateKey
from adcp.signing.provider import SigningAlgorithm, SigningProvider

from src.core.database.models import SigningKey
from src.core.database.repositories.signing_key import SigningKeyRepository
from src.core.exceptions import AdCPConfigurationError
from src.core.signing_contract import REQUEST_SIGNING, narrow_alg, narrow_purpose


class SigningMaterial(NamedTuple):
    """The loaded private half of one signing key, plus how to name it on the wire.

    Two consumers need the SAME three values and must not resolve them twice:
    :func:`_resolve_signing_provider` binds them into an
    ``InMemorySigningProvider`` for the RFC 9421 REQUEST signer, and C1's
    outbound webhook boundary (``src/core/signing/webhook_sender_factory``)
    hands them to ``adcp.webhooks.WebhookSender``, whose RFC 9421 constructor
    takes a raw ``PrivateKey`` rather than a provider.

    Splitting them out is what keeps the PEM read, the passphrase and the
    published-JWK tripwire in ONE place. The alternative — reaching into
    ``InMemorySigningProvider._private_key`` — reads private SDK state, and a
    second PEM loader beside this one is how the tripwire ends up bypassed on
    one of the two paths.
    """

    private_key: PrivateKey
    kid: str
    alg: SigningAlgorithm


class _CachedKey(NamedTuple):
    """One cache entry: when it expires, and both views of the same key."""

    expires_at: float
    material: SigningMaterial
    provider: SigningProvider


# The cache holds the expensive, PURELY DERIVED part: PEM read + key parse +
# tripwire. It deliberately does NOT cache the DB read — resolution keys on
# ``kid``, and learning the kid means querying Postgres, so the round-trip
# happens on every call regardless. Caching the row instead would be the thing
# that lets a revoked key keep signing.
#
# Revocation converges two ways: a revoked row is refused BEFORE the cache is
# consulted (immediate, because the row is always freshly read), and the TTL
# bounds the other drift this cache can hide — a PEM rotated behind an unchanged
# ``private_key_ref``, which the tripwire only sees on a cache miss.
_CACHE_TTL_SECONDS = 60.0
_provider_cache: dict[tuple[str, str], _CachedKey] = {}


class _RefResolver(Protocol):
    def __call__(self, locator: str) -> bytes: ...


def _read_file_ref(locator: str) -> bytes:
    path = Path(locator)
    if not path.is_file():
        raise AdCPConfigurationError(f"Signing key file {locator!r} does not exist or is not a file")
    return path.read_bytes()


def _read_env_ref(locator: str) -> bytes:
    value = os.getenv(locator)
    if not value:
        raise AdCPConfigurationError(f"Signing key env var {locator!r} is unset or empty")
    return value.encode()


# Every EXTERNAL scheme this process knows how to resolve — locator in, bytes
# out. ``db:`` is deliberately absent: its material lives on the row the caller
# already holds, so a resolver for it would need the row and widening this
# protocol for every scheme to carry one is the wrong trade. It is handled in
# :func:`_row_private_key_pem` instead, leaving this contract untouched.
#
# Which schemes a DEPLOYMENT will actually resolve is the agent-level
# ``SigningConfig.allowed_key_ref_schemes`` gate below, so production can forbid
# ``file:`` without touching tenant rows.
_REF_RESOLVERS: dict[str, _RefResolver] = {
    "file": _read_file_ref,
    "env": _read_env_ref,
}

#: The scheme this agent MINTS: the private half is the encrypted PEM on the row
#: itself, under a locator that is the row's own ``kid``.
DB_SCHEME = "db"


def parse_key_ref(private_key_ref: str) -> tuple[str, str]:
    """Split a scheme-prefixed reference into ``(scheme, locator)``."""
    scheme, separator, locator = private_key_ref.partition(":")
    if not separator or not locator:
        raise AdCPConfigurationError(
            f"private_key_ref {private_key_ref!r} is not scheme-prefixed "
            "(expected 'db:<kid>', 'env:NAME' or 'file:/path')"
        )
    return scheme, locator


def assert_ref_scheme_allowed(scheme: str) -> None:
    """Refuse a ``private_key_ref`` scheme this deployment will not resolve.

    Called on BOTH sides of the key's life — before minting
    (``src.core.signing.keys``) and before resolving (below). Read-time-only
    enforcement is what let a deployment persist and PUBLISH a row whose private
    half the resolver would later refuse to load: a published key nobody can sign
    with, detected only once counterparties start rejecting signatures.
    """
    from src.core.config import get_config

    allowed = get_config().signing.key_ref_scheme_list
    if scheme not in allowed:
        raise AdCPConfigurationError(
            f"private_key_ref scheme {scheme!r} is not permitted by this deployment (allowed: {allowed})"
        )


def _row_private_key_pem(row: SigningKey) -> bytes:
    """The PEM bytes behind *row*'s ``private_key_ref``.

    ``db:`` is served from the row's own ciphertext; every other scheme goes
    through :data:`_REF_RESOLVERS`. The locator of a ``db:`` ref is asserted to be
    the row's ``kid``: that is what makes the ref self-describing in a log line,
    and a mismatch means a ref was copied from one row onto another.
    """
    scheme, locator = parse_key_ref(row.private_key_ref)
    assert_ref_scheme_allowed(scheme)

    if scheme == DB_SCHEME:
        if locator != row.kid:
            raise AdCPConfigurationError(
                f"Signing key {row.kid!r} for tenant {row.tenant_id!r} carries private_key_ref "
                f"{row.private_key_ref!r}, which locates another row's key material"
            )
        if not row.private_key_pem_encrypted:
            raise AdCPConfigurationError(
                f"Signing key {row.kid!r} for tenant {row.tenant_id!r} references its own encrypted "
                "PEM but the row carries none — the private half of a published key is missing"
            )
        return bytes(row.private_key_pem_encrypted)

    resolver = _REF_RESOLVERS.get(scheme)
    if resolver is None:
        raise AdCPConfigurationError(f"private_key_ref scheme {scheme!r} has no resolver")
    return resolver(locator)


def assert_pem_publishes_jwk(
    pem: bytes,
    *,
    kid: str,
    purpose: str,
    public_jwk: dict[str, object],
    tenant_id: str,
    passphrase: bytes | None,
) -> PrivateKey:
    """Load *pem* and assert it re-derives *public_jwk*; return the private key.

    The tripwire security.mdx calls "assert public key at init", in ONE place so
    the mint path and the resolve path cannot check it differently — a second
    hand-rolled copy is how the canonical check ends up bypassed on one of them.

    Re-deriving the public JWK from the private half and comparing it to what the
    row publishes is what catches key material that changed behind an unchanged
    ``private_key_ref``: signatures every counterparty rejects, with nothing
    wrong locally to look at. At MINT time the same call proves the KEK
    round-trips before anything is published.

    The passphrase is threaded into both ``load_private_key_pem`` and
    ``pem_to_adcp_jwk``: omitting it from the second is a false alarm the moment a
    passphrase is configured, and it reads exactly like a real mismatch.

    ``load_private_key_pem`` raises a raw ``ValueError`` ("Incorrect password, could
    not decrypt key") on a wrong passphrase — not a project exception type. Every
    caller of this module (the capabilities read path's key-presence check, the
    admin setup checklist, C1's outbound webhook boundary) expects
    ``AdCPConfigurationError`` as the one signing-configuration error type, so the
    wrong-passphrase case is normalized here rather than leaking a bare
    ``ValueError`` past this module's boundary (salesagent-dn4i).
    """
    try:
        private_key = load_private_key_pem(pem, password=passphrase)
    except ValueError as exc:
        raise AdCPConfigurationError(
            f"Signing key {kid!r} for tenant {tenant_id!r} could not be decrypted — the configured "
            f"passphrase does not match the one this key's private half was encrypted under: {exc}"
        ) from exc
    derived_jwk = pem_to_adcp_jwk(
        pem,
        kid=kid,
        purpose=narrow_purpose(purpose),
        password=passphrase,
    )
    if derived_jwk != public_jwk:
        raise AdCPConfigurationError(
            f"Signing key {kid!r} for tenant {tenant_id!r} does not match the public JWK it "
            "publishes — the key material behind its private_key_ref has changed. Signatures made with "
            "it would be rejected by every counterparty."
        )
    return private_key


def _select_row(
    repo: SigningKeyRepository, *, tenant_id: str, purpose: str, now: datetime, kid: str | None
) -> SigningKey:
    """Pick the row to sign with: an explicit *kid* wins, else the active key.

    The ``kid`` axis is what expresses "this signing surface uses that key" —
    security.mdx's blast-radius isolation publishes a SECOND request-signing key
    under a distinct kid, so isolation comes from the kid, not from a distinct
    purpose. Without this axis both surfaces necessarily share one key.

    A revoked key never signs, however it was selected. ``active_at`` already
    excludes revoked rows; the explicit-kid path has to say so itself.
    """
    if kid is not None:
        row = repo.get_by_kid(kid)
        if row is None:
            raise AdCPConfigurationError(f"Tenant {tenant_id!r} has no signing key with kid {kid!r}")
        if row.revoked_at is not None:
            raise AdCPConfigurationError(
                f"Signing key {kid!r} for tenant {tenant_id!r} was revoked at {row.revoked_at.isoformat()}"
            )
        return row

    row = repo.active_at(now=now, purpose=purpose)
    if row is None:
        raise AdCPConfigurationError(f"Tenant {tenant_id!r} has no active {purpose} signing key at {now.isoformat()}")
    return row


def _build_material(row: SigningKey) -> SigningMaterial:
    """Load the private half behind *row* and bind it to the row's published JWK.

    The tripwire is :func:`assert_pem_publishes_jwk` — the same function
    provisioning runs before it commits a row, so a key can never be published
    unless it round-tripped on the way in and keeps round-tripping on the way out.
    """
    from src.core.config import get_config

    passphrase = get_config().signing.key_passphrase
    pem = _row_private_key_pem(row)

    private_key = assert_pem_publishes_jwk(
        pem,
        kid=row.kid,
        purpose=row.purpose,
        public_jwk=row.public_jwk,
        tenant_id=row.tenant_id,
        passphrase=passphrase,
    )

    return SigningMaterial(private_key=private_key, kid=row.kid, alg=narrow_alg(row.alg))


def _resolve_cached(
    repo: SigningKeyRepository,
    *,
    tenant_id: str,
    purpose: str,
    now: datetime,
    kid: str | None,
) -> _CachedKey:
    """Row -> ref -> PEM -> tripwire -> both views, memoized for the TTL.

    ONE resolution path for both public entry points below: a second one would
    read the PEM twice per delivery and — worse — could apply the tripwire
    differently on the two paths.
    """
    row = _select_row(repo, tenant_id=tenant_id, purpose=purpose, now=now, kid=kid)

    cache_key = (tenant_id, row.kid)
    cached = _provider_cache.get(cache_key)
    if cached is not None and time.monotonic() < cached.expires_at:
        return cached

    material = _build_material(row)
    entry = _CachedKey(
        expires_at=time.monotonic() + _CACHE_TTL_SECONDS,
        material=material,
        provider=InMemorySigningProvider(
            private_key=material.private_key,
            key_id=material.kid,
            algorithm=material.alg,
        ),
    )
    # Cache success, never errors — a transient resolution failure must not pin
    # itself for the TTL.
    _provider_cache[cache_key] = entry
    return entry


def _resolve_signing_provider(
    repo: SigningKeyRepository,
    *,
    tenant_id: str,
    purpose: str = REQUEST_SIGNING,
    now: datetime,
    kid: str | None = None,
) -> SigningProvider:
    """Return the ``SigningProvider`` *tenant_id* signs *purpose* with at *now*.

    Pass ``kid`` to designate a specific key; otherwise the active key wins.

    No session is opened here — the caller supplies the repository, so this stays
    callable from an ``_impl`` without reaching for ``get_db_session()``.

    Raises:
        AdCPConfigurationError: no key resolves, the key is revoked, its
            reference scheme is forbidden, or the tripwire fires.
    """
    return _resolve_cached(repo, tenant_id=tenant_id, purpose=purpose, now=now, kid=kid).provider


def resolve_signing_material(
    repo: SigningKeyRepository,
    *,
    tenant_id: str,
    purpose: str = REQUEST_SIGNING,
    now: datetime,
    kid: str | None = None,
) -> SigningMaterial:
    """Return the loaded key *tenant_id* signs *purpose* with at *now*.

    Same resolution, same cache and the same published-JWK tripwire as
    :func:`_resolve_signing_provider` — only the projection differs. This is the
    form ``adcp.webhooks.WebhookSender`` needs (``private_key`` / ``key_id`` /
    ``alg``), so C1's outbound webhook boundary calls it rather than unwrapping a
    provider's private attribute.

    Raises:
        AdCPConfigurationError: no key resolves, the key is revoked, its
            reference scheme is forbidden, or the tripwire fires.
    """
    return _resolve_cached(repo, tenant_id=tenant_id, purpose=purpose, now=now, kid=kid).material


def clear_signing_provider_cache() -> None:
    """Drop every cached provider. For rotation tooling and test isolation."""
    _provider_cache.clear()


def signing_config_from_material(material: SigningMaterial) -> SigningConfig:
    """Project :class:`SigningMaterial` into ``adcp.signing.autosign.SigningConfig``.

    The ``adcp.signing`` import stays inside the layer (rule A,
    ``tests/unit/test_architecture_signing_layer_boundary.py``) -- callers
    outside ``src/core/signing/`` that need a client-side auto-signing bundle
    (#1291 C3's ``build_adcp_multi_agent_client``) get the constructed object
    through this facade export rather than importing the SDK type themselves.
    """
    return SigningConfig(private_key=material.private_key, key_id=material.kid, alg=material.alg)
