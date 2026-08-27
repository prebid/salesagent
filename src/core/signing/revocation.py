"""Checklist step 9 — revocation — decided by exactly one callable.

#1291 A5 (salesagent-z6nr.11).

Core invariant
--------------
Step 9 is decided by one :class:`CounterpartyRevocationChecker` handed to
``VerifyOptions.revocation_checker``. It owns BOTH halves of the step
(membership and staleness), translates every failure it can raise into a typed
:class:`~adcp.signing.errors.SignatureVerificationError` before it leaves our
code, and is reached only through the SDK checklist — so revocation keeps
running before crypto verify without anything in salesagent reordering,
re-deciding or second-sourcing it.

Spec grounding (reproducible):
``git -C ~/projects/adcp show v3.1.1:docs/building/by-layer/L1/security.mdx``
— :1238 (step 9), :1328 (one combined list at the brand.json origin), :1333
(fetch-failure safe default, grace = 4x the previous polling interval),
:1686-1689 (the checklist pseudo-code). Graded at the wire by compliance vector
``negative/017-key-revoked`` (B3, salesagent-z6nr.14).

Why a wrapper at all, and not the SDK checker straight through
--------------------------------------------------------------
``verifier.py:294`` calls the revocation checker with no ``try``, and
``CachingRevocationChecker`` raises bare ``Exception`` subclasses. The verifier
middleware's only catch is ``except SignatureVerificationError``
(``request_verifier_middleware.py:341``), so every one of those escapes to
``ServerErrorMiddleware`` and becomes a 500 instead of the graded 401. The SDK's
own docstring (``revocation_fetcher.py:600-602``) claims the verifier maps
``RevocationListFreshnessError`` to ``request_signature_revocation_stale``; it
does not. This module is that missing translation, and it is a SINGLE site —
:meth:`CounterpartyRevocationChecker.__call__`'s one ``except`` tuple — rather
than a per-exception ladder, because it is one decision.

Ordering inside step 9, and the spec's own contradiction
--------------------------------------------------------
**The locally-configured revoked set is checked FIRST and answers immediately;
only then is the fetched list consulted, with staleness decided inside it.**

The spec contradicts itself on this ordering and no vector grades the
combination: the checklist prose at :1238 lists membership first ("Reject if
``keyid`` in ``revoked_kids`` ... Reject with
``request_signature_revocation_stale`` if ..."), while the pseudo-code at
:1686-1689 checks ``isRevocationStale()`` before ``isKeyRevoked()``. Both
orderings are equally fail-closed. We follow :1238 for the LOCAL set because a
locally-configured revocation is a permanent known fact with no staleness
question attached to it, and because the alternative couples an unrelated
config flag to a graded outcome: under fetched-first, a deployment that set
``require_revocation_list=True`` would answer ``_revocation_stale`` for the
seeded ``test-revoked-2026`` (the conformance runner's origin publishes no
list), turning vector 017 into a FAIL. The pseudo-code's stale-before-membership
ordering is preserved where it actually applies — inside the fetched checker,
where ``_ensure_fresh`` runs before ``is_revoked``.

Do not "fix" this to match :1686-1689 without re-reading both citations.

Concurrency
-----------
``_run_verifier`` runs under ``asyncio.to_thread``
(``request_verifier_middleware.py:329``), which is exactly the thread-pool case
``revocation_fetcher.py:609-618`` says needs an external lock. The lock is
therefore PER ISSUER ORIGIN and held only around that origin's check — never one
process-wide lock, which would be held across the 10s-default HTTPS fetch inside
``_ensure_fresh`` and let one dead counterparty stall every signed request in
the process.
"""

from __future__ import annotations

import logging
import threading
from typing import Any
from urllib.parse import urlsplit

from adcp.signing import SSRFValidationError
from adcp.signing.agent_resolver import AgentResolution
from adcp.signing.errors import REQUEST_SIGNATURE_REVOCATION_STALE, SignatureVerificationError
from adcp.signing.jwks import StaticJwksResolver
from adcp.signing.revocation import RevocationChecker
from adcp.signing.revocation_fetcher import (
    CachingRevocationChecker,
    RevocationListFetchError,
    RevocationListFreshnessError,
    RevocationListParseError,
    RevocationListSignatureError,
)

from src.core.config import SigningConfig
from src.core.metrics import record_signature_revocation_unavailable

logger = logging.getLogger(__name__)

#: Process-level ``{issuer origin: CachingRevocationChecker}``. The SDK checker IS
#: the cache — a fresh instance per request would discard the loaded list and
#: re-fetch on every signed request — so it is built once per operator origin and
#: reused. Keyed by the ``scheme://netloc`` of the counterparty's
#: ``brand_json_url``, because :1328 puts ONE combined list at that origin.
REVOCATION_CHECKER_CACHE: dict[str, CachingRevocationChecker] = {}

#: ``{issuer origin: lock}``, obtained with ``setdefault`` so a registry entry that
#: appears without a paired lock (a concurrent builder, or a test seeding one) is
#: still usable.
_CHECKER_LOCKS: dict[str, threading.Lock] = {}

#: Guards the two dicts above and NOTHING else. Never held across a fetch.
_REGISTRY_LOCK = threading.Lock()

#: The ``reason`` label for a list we could not read, most specific first —
#: ``RevocationListSignatureError`` subclasses ``RevocationListParseError``, so
#: order is load-bearing. One member per member of the ``except`` tuple in
#: :meth:`CounterpartyRevocationChecker.__call__`; the two are the same decision
#: seen twice (what can go wrong, and what we call it), so they are kept adjacent
#: rather than derived apart.
_UNAVAILABLE_REASONS: tuple[tuple[type[Exception], str], ...] = (
    (RevocationListSignatureError, "signature"),
    (RevocationListParseError, "parse"),
    (RevocationListFetchError, "fetch"),
    (SSRFValidationError, "ssrf"),
)


def _origin_of(url: str | None) -> str | None:
    """The ``scheme://netloc`` of *url*, or None if it has neither."""
    if not url:
        return None
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def _issuer_origin(resolution: AgentResolution | None, config: SigningConfig) -> str | None:
    """Where this counterparty's combined revocation list is served.

    Per-counterparty by default, not one per process: :1328 puts the combined
    list at the counterparty's own brand.json origin, so a single agent-level
    issuer cannot answer "is counterparty C's kid revoked". The optional config
    pin is for a deployment fronted by one governance issuer.
    """
    if config.revocation_issuer_origin:
        return _origin_of(config.revocation_issuer_origin)
    if resolution is None:
        return None
    return _origin_of(resolution.brand_json_url)


class _ResolutionCacheJwksResolver:
    """Resolves the list-signing ``kid`` by READING the resolution cache per call.

    The revocation list's JWS is signed by a key in the operator's own JWKS,
    which for the transport list is the JWKS we already hold on each
    :class:`AgentResolution`. Handing the checker a construction-time
    ``StaticJwksResolver(resolution.jwks)`` instead would break two ways, both
    flowing from :1328's one-list-per-operator-origin model:

    * an operator rotating its list-signing key would break revocation for that
      origin until process restart, even though ``AgentResolution`` re-resolves
      every ``agent_resolution_ttl_seconds``;
    * two counterparties sharing an operator origin share the checker built from
      whichever resolution arrived first, so a list signed with the other's kid
      fails JWS verification and BOTH are rejected as revocation-stale.

    Reading through the cache heals both on the TTL B1 already runs.
    """

    def __init__(self, origin: str, *, match_all: bool) -> None:
        self._origin = origin
        #: True when the origin came from ``revocation_issuer_origin``: one pinned
        #: issuer serves every counterparty, so every resolution's keys are
        #: candidates for verifying its list.
        self._match_all = match_all

    def __call__(self, keyid: str) -> dict[str, Any] | None:
        # Imported here rather than at module scope: B1's middleware imports
        # `checker_for` from this module, so a top-level import back into it would
        # be a cycle. The name is bound to the same dict object either way.
        from src.core.signing.request_verifier_middleware import AGENT_RESOLUTION_CACHE

        for resolution in list(AGENT_RESOLUTION_CACHE.values()):
            if not self._match_all and _origin_of(resolution.brand_json_url) != self._origin:
                continue
            jwk = StaticJwksResolver(resolution.jwks)(keyid)
            if jwk is not None:
                return jwk
        return None


class CounterpartyRevocationChecker:
    """The one callable that decides step 9 for one counterparty.

    Implements the SDK's ``RevocationChecker`` Protocol — ``__call__(keyid) ->
    bool`` — and is the only place a revocation failure becomes a typed
    :class:`SignatureVerificationError`.
    """

    def __init__(
        self,
        *,
        static_revoked: frozenset[str],
        fetched: RevocationChecker | None,
        require_list: bool,
        lock: threading.Lock | None = None,
        issuer: str | None = None,
    ) -> None:
        self._static_revoked = static_revoked
        self._fetched = fetched
        self._require_list = require_list
        self._lock = lock if lock is not None else threading.Lock()
        self._issuer = issuer

    def __call__(self, keyid: str) -> bool:
        # The locally-configured set first, answering immediately — see the module
        # docstring on the :1238 / :1686-1689 ordering contradiction.
        if keyid in self._static_revoked:
            return True
        if self._fetched is None:
            return False

        with self._lock:
            try:
                return bool(self._fetched(keyid))
            except (
                # revocation_fetcher.py:122 — raised by `_ensure_fresh` once the
                # cached list is past `next_update + grace`. A bare Exception, and
                # `verifier.py:294` calls the checker with no `try`.
                RevocationListFreshnessError,
                # revocation_fetcher.py:_refresh — a malformed payload, and (via its
                # subclass RevocationListSignatureError) a list JWS we cannot verify.
                RevocationListParseError,
                # revocation_fetcher.py:779-781 — the COLD path calls `_refresh` with
                # no `except`, so a counterparty publishing NO list raises this on
                # every signed request. Nobody publishes one today.
                RevocationListFetchError,
                # `default_revocation_list_fetcher` calls `build_ip_pinned_transport`
                # OUTSIDE its `except httpx.HTTPError` (revocation_fetcher.py:262),
                # and ip_pinned_transport.py:78-79 turns an OSError/gaierror into
                # this. Without it a transient DNS failure on one counterparty's
                # revocation endpoint 500s every signed request from that
                # counterparty. Never widen this to a bare `except Exception`.
                SSRFValidationError,
            ) as exc:
                return self._unreadable(exc)

    def _unreadable(self, exc: Exception) -> bool:
        """Decide what an unreadable revocation list means for this request.

        A list that HAS loaded and then aged past ``next_update + grace`` rejects
        unconditionally — :1333's MUST — and is not configurable. Everything else
        means we never had a list at all: :1333 is conditioned on "have not
        refreshed within ``next_update`` + grace", and with no list ever loaded
        there is no ``next_update`` for it to bite on. That is served, but only as
        a DECIDED posture: announced at WARNING and metered, never silently.
        """
        if isinstance(exc, RevocationListFreshnessError) or self._require_list:
            raise SignatureVerificationError(
                REQUEST_SIGNATURE_REVOCATION_STALE,
                step=9,
                message=f"revocation list for {self._issuer or 'counterparty'} is not usable: {exc}",
            ) from exc

        reason = next((name for kind, name in _UNAVAILABLE_REASONS if isinstance(exc, kind)), "other")
        record_signature_revocation_unavailable(reason)
        logger.warning(
            "Revocation list unavailable for issuer %r (%s): %s — serving the request; "
            "set ADCP_SIGNING_REQUIRE_REVOCATION_LIST=true to reject instead",
            self._issuer,
            reason,
            exc,
        )
        return False


def checker_for(resolution: AgentResolution | None, config: SigningConfig) -> CounterpartyRevocationChecker:
    """Build the step-9 callable for one counterparty.

    The ONE construction site, which is what makes ``grace_multiplier=4.0``
    (:1333, against the SDK's 2.0 default) a single argument to keep right
    instead of one per call site. ``fetcher=`` is deliberately NOT passed, so the
    SDK's IP-pinned, redirect-refusing, ``trust_env=False`` default fetcher is
    what runs — the "revocation fetch uses the IP-pinned transport" requirement
    is satisfied by omission, and there is no ``allow_private`` argument anywhere
    near it.

    ``prime()`` is deliberately not called: it would block startup on a third
    party's endpoint. The first ``__call__`` triggers the load.
    """
    static_revoked = frozenset(config.revoked_keyid_list)
    origin = _issuer_origin(resolution, config)
    if origin is None:
        return CounterpartyRevocationChecker(
            static_revoked=static_revoked,
            fetched=None,
            require_list=config.require_revocation_list,
        )

    with _REGISTRY_LOCK:
        fetched = REVOCATION_CHECKER_CACHE.get(origin)
        if fetched is None:
            fetched = CachingRevocationChecker.from_issuer_origin(
                origin,
                jwks_resolver=_ResolutionCacheJwksResolver(
                    origin,
                    match_all=bool(config.revocation_issuer_origin),
                ),
                grace_multiplier=config.revocation_grace_multiplier,
            )
            REVOCATION_CHECKER_CACHE[origin] = fetched
        lock = _CHECKER_LOCKS.setdefault(origin, threading.Lock())

    return CounterpartyRevocationChecker(
        static_revoked=static_revoked,
        fetched=fetched,
        require_list=config.require_revocation_list,
        lock=lock,
        issuer=origin,
    )
