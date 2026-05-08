"""Webhook authentication header builder.

Slice 3 of the per-buyer-agent signing refactor. Translates a
:class:`PushNotificationConfig` row's ``signing_mode`` into the actual
``headers`` dict the delivery transport should attach to the outgoing
POST. Callers stay transport-shaped (``httpx``, ``requests``, etc.) —
this module only knows about hash + RFC 9421.

Modes:

* ``hmac`` — legacy ``X-ADCP-Signature`` HMAC-SHA256 header (existing
  behavior).
* ``rfc9421`` — :rfc:`9421` ``Signature`` + ``Signature-Input`` headers
  (and ``Content-Digest`` when the signing call covers it). HMAC is
  NOT attached.
* ``both`` — **transition only.** Emits both flavors so a buyer can
  cut over to RFC 9421 verification while keeping their HMAC verifier
  as a fallback. The two flavors cover non-equivalent payloads (HMAC
  hashes ``timestamp.body``; RFC 9421 covers method + target-uri +
  content-digest + content-type), so leaving ``both`` enabled forever
  means buyers maintain two non-equivalent integrity proofs in
  perpetuity. Sunset: 2026-12-31. After that date, operators should
  switch buyers to ``rfc9421`` and the mode will be removed in a
  follow-up release. (Tracked in issue #44.)

The body is taken as ``bytes`` — the caller is responsible for
serializing the JSON payload exactly once and using the same bytes for
the wire send and the signature input. Re-encoding (sort_keys, etc.)
between sign and send is a known footgun: the signed signature base
won't match the body the verifier hashes.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adcp.signing import sign_request
from adcp.signing.constants import WEBHOOK_TAG
from adcp.signing.crypto import ALG_ED25519, ALG_ES256, load_private_key_pem
from cachetools import TTLCache

logger = logging.getLogger(__name__)


def _resolve_signing_keys_dir() -> Path:
    """Return the directory under which all ``local_pem`` keys MUST live.

    Configurable via the ``WEBHOOK_SIGNING_KEYS_DIR`` env var; defaults
    to ``./signing_keys`` relative to CWD for dev. Operators in
    production should set this to a directory mounted with mode 0700
    and owned by the salesagent process user.

    Resolved with :meth:`Path.resolve` so a relative env var (or a
    symlinked directory) collapses to a canonical absolute path before
    membership checks. Re-resolved per call so a test that overrides
    the env var doesn't have to clear a module-level cache.
    """
    raw = os.environ.get("WEBHOOK_SIGNING_KEYS_DIR", "./signing_keys")
    return Path(raw).resolve()


WEBHOOK_SIGNING_PURPOSE = "webhook-signing"

SIGNING_MODE_HMAC = "hmac"
SIGNING_MODE_RFC9421 = "rfc9421"
SIGNING_MODE_BOTH = "both"

_JWK_KTY_TO_ALG = {
    ("OKP", "Ed25519"): ALG_ED25519,
    ("EC", "P-256"): ALG_ES256,
}

# Minimum acceptable HMAC secret length (matches existing service contract).
_MIN_HMAC_SECRET_LEN = 32

# Headers that travel on the wire but MUST NOT enter the RFC 9421
# signer's view of the request. ``Authorization`` is the load-bearing
# entry: today the SDK doesn't auto-cover it, but a future SDK change
# that broadens auto-coverage would silently bind the bearer token into
# the signature base — and into any verifier debug log that echoes the
# signed components. Keep it out of the signer's input dict entirely;
# the caller re-attaches it to the wire-bound headers afterwards.
_HEADERS_HIDDEN_FROM_SIGNER: frozenset[str] = frozenset({"authorization"})

# Tenant-scoped LRU+TTL cache over ``LoadedSigningCredential`` snapshots.
# Hits skip the DB roundtrip + the disk PEM parse (ASN.1 is not free
# under sustained webhook rates). TTL is intentionally generous so that
# during a rotation window the salesagent keeps signing with the
# previously-active kid — buyers' published JWKS lag DB updates while
# the operator republishes ``brand.json``, and signing with a kid the
# buyer doesn't yet know about would cause verification failures.
# Operators that need to invalidate sooner can call :func:`invalidate`.
#
# Bounded at 256 tenants — multi-tenant deployments can have many
# active credentials but the working set per process is small.
_CACHE_TTL_SECONDS = 300
_CACHE_MAXSIZE = 256
_credential_cache: TTLCache[str, LoadedSigningCredential | None] = TTLCache(
    maxsize=_CACHE_MAXSIZE, ttl=_CACHE_TTL_SECONDS
)
_credential_cache_lock = threading.Lock()


def invalidate_credential_cache(tenant_id: str | None = None) -> None:
    """Drop a tenant's cached snapshot (or the whole cache).

    Normally fired automatically by the SQLAlchemy session listener
    registered below — when a ``TenantSigningCredential`` row is
    inserted, updated, or deleted with ``purpose='webhook-signing'``,
    the affected tenant's snapshot is evicted on ``after_commit``.
    Call directly only when bypassing the ORM (raw SQL writes) or in
    tests. ``tenant_id=None`` clears the whole cache.

    Per-process: only invalidates the local-worker cache. Other
    workers / replicas converge within :data:`_CACHE_TTL_SECONDS`
    (5 min). Cross-process invalidation is out of scope for this
    slice — see issue #46.
    """
    with _credential_cache_lock:
        if tenant_id is None:
            _credential_cache.clear()
        else:
            _credential_cache.pop(tenant_id, None)


_LISTENER_REGISTERED = False


def _register_session_invalidator() -> None:
    """Wire SQLAlchemy session events that evict the cache after commit.

    Captures affected tenant_ids during ``before_flush`` (when ORM
    objects are still attached) and evicts on ``after_commit`` (when
    the new state is durable). ``after_rollback`` drops the captured
    set so a rolled-back rotation doesn't evict the still-active
    cached kid.

    Filters to ``purpose='webhook-signing'`` so unrelated
    ``request-signing-as-buyer`` writes don't burn webhook cache
    entries. Idempotent — guards against duplicate registration when
    the module is reloaded under pytest's import-fixup or during
    development reloads.
    """
    global _LISTENER_REGISTERED
    if _LISTENER_REGISTERED:
        return

    from sqlalchemy import event
    from sqlalchemy.orm import Session

    # Importing the model here keeps the layering clean: webhook_signing
    # depends on database.models, never the other way round.
    from src.core.database.models import TenantSigningCredential

    _PENDING_KEY = "_webhook_signing_invalidate"

    def _capture(session: Session, *_args: Any) -> None:
        pending: set[str] = session.info.setdefault(_PENDING_KEY, set())
        for obj in (*session.new, *session.dirty, *session.deleted):
            if isinstance(obj, TenantSigningCredential) and obj.purpose == WEBHOOK_SIGNING_PURPOSE:
                pending.add(obj.tenant_id)

    def _flush(session: Session) -> None:
        pending: set[str] | None = session.info.pop(_PENDING_KEY, None)
        if not pending:
            return
        for tenant_id in pending:
            invalidate_credential_cache(tenant_id)

    def _drop(session: Session) -> None:
        session.info.pop(_PENDING_KEY, None)

    event.listen(Session, "before_flush", _capture)
    event.listen(Session, "after_commit", _flush)
    event.listen(Session, "after_rollback", _drop)
    _LISTENER_REGISTERED = True


_register_session_invalidator()


class SigningConfigurationError(RuntimeError):
    """Raised when ``signing_mode`` requires RFC 9421 but the tenant has
    no usable active credential (no row, KMS-only backend, missing PEM,
    etc.). Fail-closed: the caller drops the webhook rather than silently
    sending an unauthenticated body to a buyer that asked for signed."""


@dataclass(frozen=True)
class LoadedSigningCredential:
    """Snapshot of a tenant's active webhook-signing credential.

    The DB row, the PEM bytes, and the algorithm derivation are
    captured *atomically* by :func:`load_active_signing_credential` so
    the caller cannot accidentally produce a signature with the new
    ``key_id`` and the old PEM bytes (or vice versa) during a rotation
    race. Pass this dataclass into :func:`build_auth_headers` —
    transports never see the ORM row.

    Frozen so a misbehaving caller can't mutate the snapshot mid-sign.
    """

    key_id: str
    alg: str
    pem_bytes: bytes


def load_active_signing_credential(*, tenant_id: str | None, signing_mode: str) -> LoadedSigningCredential | None:
    """Return the active webhook-signing credential snapshot for a tenant.

    Atomicity matters: the DB row read AND the PEM read happen inside
    the same call so the resulting :class:`LoadedSigningCredential`
    cannot mix a freshly-rotated ``key_id`` with stale PEM bytes (or
    the reverse). The snapshot also strips the ORM dependency from the
    signing helper, so detached-instance / session-lifetime hazards
    can't reach the sign path.

    Returns ``None`` immediately for HMAC-only mode (the legacy path
    never reads our outbound key). For RFC 9421 modes, raises
    :class:`SigningConfigurationError` at the source of the problem
    (missing row, KMS backend, missing/unreadable PEM, unsupported
    JWK shape) so the caller can surface a precise error rather than
    "credential is None for some reason".

    Successful loads are cached per-tenant for
    :data:`_CACHE_TTL_SECONDS`. Failures are NOT cached — operators in
    the middle of fixing config want their next attempt to see the new
    state, not a cached error. Call :func:`invalidate_credential_cache`
    after rotation to force the next delivery to re-read.
    """
    if signing_mode == SIGNING_MODE_HMAC:
        return None
    if not tenant_id:
        raise SigningConfigurationError(f"signing_mode={signing_mode!r} requires a tenant_id; got None")

    with _credential_cache_lock:
        cached = _credential_cache.get(tenant_id)
    if cached is not None:
        return cached

    snapshot = _load_active_signing_credential_uncached(tenant_id=tenant_id, signing_mode=signing_mode)
    with _credential_cache_lock:
        _credential_cache[tenant_id] = snapshot
    return snapshot


def _load_active_signing_credential_uncached(*, tenant_id: str, signing_mode: str) -> LoadedSigningCredential:
    """Uncached read path — exposed only for testability and the cache wrapper."""
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories import TenantSigningCredentialRepository

    with get_db_session() as session:
        repo = TenantSigningCredentialRepository(session, tenant_id=tenant_id)
        row = repo.get_active(WEBHOOK_SIGNING_PURPOSE)
        if row is None:
            raise SigningConfigurationError(
                f"signing_mode={signing_mode!r} requires an active "
                f"webhook-signing credential; none configured for tenant {tenant_id!r}"
            )
        if row.backend != "local_pem":
            raise SigningConfigurationError(
                f"backend={row.backend!r} cannot sign synchronously; "
                f"only local_pem is supported by WebhookDeliveryService today"
            )
        # Path-traversal guard: a hostile or buggy admin-write path
        # could set ``backend_ref="/etc/shadow"``. We can't read that
        # (different file owner) but the cryptography library's parse
        # error message may echo the first bytes back through our log,
        # creating a thin info-leak channel. Constrain backend_ref to
        # a configured directory and reject anything that escapes it.
        keys_dir = _resolve_signing_keys_dir()
        try:
            pem_path = Path(row.backend_ref).resolve()
        except (OSError, RuntimeError) as exc:
            # ``resolve()`` raises on symlink loops; ``OSError`` on some
            # platforms when the path can't be canonicalized. Either is
            # fatal for the same reason — we can't prove containment.
            raise SigningConfigurationError(f"failed to canonicalize backend_ref {row.backend_ref!r}: {exc}") from exc
        if not pem_path.is_relative_to(keys_dir):
            raise SigningConfigurationError(
                f"backend_ref {row.backend_ref!r} resolves outside the signing-keys "
                f"directory {keys_dir!r}; reject to prevent path traversal"
            )
        try:
            pem_bytes = pem_path.read_bytes()
        except OSError as exc:
            raise SigningConfigurationError(f"failed to read PEM at {pem_path}: {exc}") from exc
        # Read alg + key_id INSIDE the session — once we exit, the row
        # is detached and any further attribute access is fragile.
        alg = _alg_for_jwk(row.public_jwk)
        return LoadedSigningCredential(key_id=row.key_id, alg=alg, pem_bytes=pem_bytes)


def build_auth_headers(
    *,
    signing_mode: str,
    method: str,
    url: str,
    body: bytes,
    timestamp: str,
    base_headers: Mapping[str, str],
    webhook_secret: str | None,
    active_credential: LoadedSigningCredential | None,
) -> dict[str, str]:
    """Return the auth headers to merge into the outgoing request.

    The result is *additive* — base headers (``Content-Type``, etc.)
    are combined with HMAC and/or RFC 9421 headers per ``signing_mode``.

    :param signing_mode: ``hmac`` | ``rfc9421`` | ``both`` (DB CHECK
        constraint guarantees one of these; an unknown value raises
        :class:`SigningConfigurationError` rather than silently falling
        back to ``hmac``).
    :param method: HTTP method (``POST`` for webhooks). Embedded in the
        signature base for RFC 9421.
    :param url: Full request URL. Embedded in the signature base.
    :param body: Bytes the transport will send on the wire. RFC 9421
        ``Content-Digest`` (when covered) and the signature base both
        depend on these exact bytes.
    :param timestamp: ISO-8601 timestamp emitted as ``X-ADCP-Timestamp``
        and used as the HMAC prefix.
    :param base_headers: Headers the transport will already attach
        (``Content-Type``, ``User-Agent``, etc.). Used as the
        ``headers`` input to RFC 9421's signature base — derived
        ``@method`` / ``@target-uri`` are computed from ``method`` /
        ``url`` so this dict only needs message headers. The webhook
        profile requires ``Content-Type`` coverage on every signed
        delivery, so a missing ``Content-Type`` is a programming error
        and we fail loudly.
    :param webhook_secret: Buyer-side shared secret (legacy HMAC mode).
        ``None`` skips the HMAC header in ``hmac`` mode (back-compat:
        TLS-only delivery is fine for buyers that never set a secret).
        In ``both`` mode, missing/weak secret is a fatal config error
        — the buyer explicitly asked for belt-and-suspenders.
    :param active_credential: Pre-loaded snapshot from
        :func:`load_active_signing_credential`. ``None`` is allowed only
        for ``signing_mode='hmac'``; other modes raise.
    """
    if signing_mode not in (SIGNING_MODE_HMAC, SIGNING_MODE_RFC9421, SIGNING_MODE_BOTH):
        raise SigningConfigurationError(f"unknown signing_mode {signing_mode!r}")

    out: dict[str, str] = dict(base_headers)

    if signing_mode in (SIGNING_MODE_HMAC, SIGNING_MODE_BOTH):
        hmac_header = _build_hmac_header(body=body, timestamp=timestamp, webhook_secret=webhook_secret, url=url)
        if hmac_header is not None:
            out["X-ADCP-Signature"] = hmac_header
        elif signing_mode == SIGNING_MODE_BOTH:
            # ``both`` is the migration window — buyer asked for HMAC AND
            # RFC 9421. Silently dropping the HMAC half (because the
            # secret is missing/weak) downgrades to RFC-9421-only without
            # the buyer or operator noticing. Fail loudly instead.
            raise SigningConfigurationError(
                "signing_mode='both' requires a webhook_secret of at least "
                f"{_MIN_HMAC_SECRET_LEN} characters; HMAC half cannot be produced"
            )

    if signing_mode in (SIGNING_MODE_RFC9421, SIGNING_MODE_BOTH):
        if active_credential is None:
            raise SigningConfigurationError(
                f"signing_mode={signing_mode!r} requires an active "
                f"webhook-signing credential; none configured for this tenant"
            )
        # Webhook profile requires content-type coverage on every signed
        # delivery (adcp.signing.webhook_verifier:_precheck...). The SDK
        # only auto-covers content-type if it's present in ``headers``;
        # if a future caller drops it from base_headers, we want to fail
        # at sign time, not silently produce signatures buyers reject.
        if not _lookup_header(out, "content-type"):
            raise SigningConfigurationError(
                "RFC 9421 webhook signing requires Content-Type to be set "
                "in base_headers; the webhook profile pins it as a covered "
                "component"
            )
        signed = _build_rfc9421_headers(
            method=method,
            url=url,
            headers=out,
            body=body,
            credential=active_credential,
        )
        out.update(signed)

    return out


def _lookup_header(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive header lookup. RFC 9421 component names are
    lowercase but ``base_headers`` follows HTTP convention (Title-Case)."""
    target = name.lower()
    for k, v in headers.items():
        if k.lower() == target:
            return v
    return None


def _build_hmac_header(*, body: bytes, timestamp: str, webhook_secret: str | None, url: str) -> str | None:
    """Compute the legacy ``X-ADCP-Signature`` HMAC.

    The signed message is ``timestamp.encode() + b"." + body`` —
    operating on bytes directly so a body that isn't valid UTF-8
    (binary attachments, future envelope formats) doesn't crash the
    signer mid-delivery. Today the caller always passes JSON bytes,
    but the function signature is ``bytes`` and the contract honors it.

    Weak / missing secrets are tolerated for back-compat: we log and
    return ``None`` so the caller drops the header but still sends the
    request. (Buyers running mode=``hmac`` with no shared secret are
    relying on TLS + URL secrecy; fine, just not signed.) ``both`` mode
    promotes weak/missing secret to fatal in :func:`build_auth_headers`.
    """
    if not webhook_secret:
        return None
    if len(webhook_secret) < _MIN_HMAC_SECRET_LEN:
        logger.warning(
            "Webhook secret for %s is too weak (min %d chars required); skipping HMAC header",
            url,
            _MIN_HMAC_SECRET_LEN,
        )
        return None
    message = timestamp.encode("ascii") + b"." + body
    return hmac.new(webhook_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _build_rfc9421_headers(
    *,
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    credential: LoadedSigningCredential,
) -> dict[str, str]:
    """Sign the request with the credential snapshot's PEM bytes and
    return the headers RFC 9421 verifiers expect (``Signature``,
    ``Signature-Input``, and ``Content-Digest``).

    The snapshot was loaded atomically by
    :func:`load_active_signing_credential` — kid, alg, and PEM bytes
    were captured under the same DB session so a rotation race cannot
    desync them. Backend / path validation happens at load time; this
    function only does crypto.

    ``Authorization`` (and any other header in
    :data:`_HEADERS_HIDDEN_FROM_SIGNER`) is stripped from the dict the
    signer sees — defense-in-depth against a future SDK that broadens
    auto-coverage and would otherwise bind the bearer token into the
    signature base.
    """
    private_key = load_private_key_pem(credential.pem_bytes)
    signer_headers = {k: v for k, v in headers.items() if k.lower() not in _HEADERS_HIDDEN_FROM_SIGNER}

    signed = sign_request(
        method=method,
        url=url,
        headers=signer_headers,
        body=body,
        private_key=private_key,
        key_id=credential.key_id,
        alg=credential.alg,
        # Webhook profile pins tag to ``adcp/webhook-signing/v1`` so a
        # webhook signature can never replay against the request-signing
        # surface (and vice versa). The default tag is ``adcp/request-
        # signing/v1`` — pass it explicitly so a future SDK default
        # change can't silently downgrade the profile.
        tag=WEBHOOK_TAG,
        # Buyers verify with the webhook profile, which requires
        # content-digest coverage on every webhook delivery.
        cover_content_digest=True,
    )
    return signed.as_dict()


def _alg_for_jwk(jwk: Mapping[str, Any]) -> str:
    """Map a stored public JWK to the signing alg :func:`sign_request` expects.

    The JWK ``alg`` field is OPTIONAL at storage time — operators that
    minted via ``adcp-keygen`` get a JWK with ``kty``/``crv`` but may
    not get ``alg``. Compute it from ``(kty, crv)`` so we don't depend
    on the operator having set ``alg`` explicitly.
    """
    kty_raw = jwk.get("kty")
    crv_raw = jwk.get("crv")
    # Coerce to str | None for the lookup — kty/crv arrive from JSONType
    # so the static type is ``Any``, but RFC 7517 requires both to be
    # strings when present. Any non-string falls into the unsupported
    # branch with the same friendly error.
    kty: str | None = kty_raw if isinstance(kty_raw, str) else None
    crv: str | None = crv_raw if isinstance(crv_raw, str) else None
    alg = _JWK_KTY_TO_ALG.get((kty, crv)) if kty and crv else None
    if alg is None:
        raise SigningConfigurationError(
            f"unsupported JWK (kty={kty_raw!r}, crv={crv_raw!r}); expected Ed25519 or P-256"
        )
    return alg
