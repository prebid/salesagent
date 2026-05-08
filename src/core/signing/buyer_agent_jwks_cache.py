"""Per-buyer-agent brand.json resolver cache.

Each :class:`Principal` carries a ``brand_domain`` (operator-typed buyer
domain — the trust anchor) that points at
``https://<brand_domain>/.well-known/brand.json``. The verifier looks up
JWKS by walking that brand.json via :class:`adcp.signing.BrandJsonJwksResolver`,
which handles:

* Cooldown-respecting brand.json re-walks (default 1h, honors counterparty
  ``Cache-Control``)
* Unknown-kid cascade: when the verifier asks for a kid the resolver hasn't
  seen, the resolver re-fetches brand.json + JWKS rather than returning
  ``None`` and forcing operator intervention
* IP-pinned transport (DNS-rebinding-safe)
* Same-origin guard on the implicit ``jwks_uri`` fallback

We cache one resolver instance per ``(tenant_id, principal_id)`` so concurrent
verifies on the same principal share the cooldown window + JWK set without
re-walking brand.json from scratch.

This replaces the prior model where we stored ``jwks_uri`` as a column and
constructed a :class:`CachingJwksResolver` directly — that bypassed the
library's brand.json-walk semantics, so a buyer rotating ``jwks_uri`` in
their brand.json would never propagate without manual operator re-resolve.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

from adcp.signing.brand_jwks import BrandJsonJwksResolver
from cachetools import LRUCache

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Hard cap on the per-process resolver cache. Each entry holds an httpx
# async client + brand.json snapshot — bound the count to prevent a malicious
# (or just large) tenant_admin from allocating unbounded resolvers via
# 10k principals × N brand_domains. 1024 is comfortably above realistic
# active-buyer counts; LRU eviction handles the long tail.
DEFAULT_MAX_CACHED_RESOLVERS = 1024


def _allow_private_destinations() -> bool:
    """Permit private/loopback URLs for dev/test fixtures."""
    if os.getenv("ADCP_AUTH_TEST_MODE", "").lower() in ("1", "true", "yes"):
        return True
    if os.getenv("WEBHOOK_ALLOW_PRIVATE_IPS", "").lower() in ("1", "true", "yes"):
        return True
    return False


def brand_json_url_for(brand_domain: str) -> str:
    """Convention: each buyer publishes brand.json at the well-known path on
    their typed domain. This is the trust-root URL the resolver walks."""
    return f"https://{brand_domain.strip('/')}/.well-known/brand.json"


class BuyerAgentJwksCache:
    """Process-singleton LRU cache of :class:`BrandJsonJwksResolver` per
    ``(tenant_id, principal_id)``.

    Library resolvers are async + designed to be long-lived: their internal
    snapshot/cooldown/unknown-kid-cascade only works correctly when the same
    instance is reused across requests. Memoize per-principal.

    Bounded at :data:`DEFAULT_MAX_CACHED_RESOLVERS`; eviction logs a warning
    and best-effort closes the resolver's underlying httpx client. Eviction
    is benign — the next verify constructs a fresh resolver and pays one
    brand.json walk to re-prime the cooldown.
    """

    def __init__(self, max_cached_resolvers: int = DEFAULT_MAX_CACHED_RESOLVERS) -> None:
        self._resolvers: LRUCache[tuple[str, str], BrandJsonJwksResolver] = LRUCache(maxsize=max_cached_resolvers)
        self._lock = asyncio.Lock()

    def resolver_for(self, tenant_id: str, principal_id: str, brand_domain: str) -> BrandJsonJwksResolver:
        """Return the cached resolver. Lazy-construct on first use.

        ``brand_domain`` is the operator-typed buyer domain. The library's
        :class:`BrandJsonJwksResolver` walks ``https://<brand_domain>/.well-known/brand.json``
        and selects the buyer-protocol agent (``agent_type="buying"``).
        """
        key = (tenant_id, principal_id)
        cached = self._resolvers.get(key)
        if cached is not None:
            return cached
        resolver = BrandJsonJwksResolver(
            brand_json_url=brand_json_url_for(brand_domain),
            agent_type="buying",
            allow_private_destinations=_allow_private_destinations(),
        )
        # LRUCache handles eviction internally on overflow. Capture the
        # evicted resolver (if any) so we can fire-and-forget close its
        # httpx client — without this, evicted-but-not-GC'd resolvers
        # keep their connection pools open.
        evicted_key = None
        evicted_resolver = None
        if len(self._resolvers) >= self._resolvers.maxsize:
            try:
                evicted_key, evicted_resolver = self._resolvers.popitem()
            except KeyError:
                pass
        self._resolvers[key] = resolver
        if evicted_resolver is not None:
            logger.info(
                "buyer_agent jwks cache evicted resolver for %s (cap=%d)",
                evicted_key,
                self._resolvers.maxsize,
            )
            self._schedule_close(evicted_resolver)
        return resolver

    def invalidate(self, tenant_id: str, principal_id: str) -> None:
        """Drop the cached resolver — call when the operator changes
        ``brand_domain`` so the next verify picks up the new trust root.
        Best-effort closes the resolver's httpx client."""
        evicted = self._resolvers.pop((tenant_id, principal_id), None)
        if evicted is not None:
            self._schedule_close(evicted)

    def _schedule_close(self, resolver: BrandJsonJwksResolver) -> None:
        """Fire-and-forget close of an evicted resolver's httpx client.

        ``BrandJsonJwksResolver.aclose()`` is async; we may not be inside an
        event loop when eviction happens (e.g. during a sync admin edit
        handler call to ``invalidate()``). Try the running-loop path first;
        fall back to a fresh loop. Failure here is logged-and-ignored — the
        resolver becomes GC-eligible regardless, and httpx clients close
        cleanly during garbage collection.
        """
        if not hasattr(resolver, "aclose"):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — schedule via asyncio.run in a background thread.
            try:
                asyncio.run(resolver.aclose())
            except Exception:
                logger.debug("evicted resolver aclose failed", exc_info=True)
            return
        loop.create_task(resolver.aclose())

    def clear(self) -> None:
        """Drop all cached resolvers — primarily for tests."""
        for resolver in list(self._resolvers.values()):
            self._schedule_close(resolver)
        self._resolvers.clear()


_singleton: BuyerAgentJwksCache | None = None


def get_buyer_agent_jwks_cache() -> BuyerAgentJwksCache:
    """Return the process-wide cache singleton."""
    global _singleton
    if _singleton is None:
        _singleton = BuyerAgentJwksCache()
    return _singleton
