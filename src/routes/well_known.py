"""The four trust-root/governance documents, served per Host (#1291 A3/A5).

``/.well-known/brand.json``, ``/.well-known/adagents.json``,
``/.well-known/jwks.json`` (A3, salesagent-z6nr.9) and
``/.well-known/governance-revocations.json`` (A5 follow-up,
salesagent-z6nr.27). Plain GETs, no auth, no signature (the first three
carry no signature of their own; the last one is ITSELF a JWS, signed, but
still served unauthenticated): these documents BOOTSTRAP the trust chain,
so requiring the signature they exist to let a verifier check would
deadlock every counterparty. (The inbound signature verifier — B1,
salesagent-z6nr.12 — must exempt these paths for the same reason.)

Each request resolves its tenant from the Host the way every other
host-routed surface does (:func:`route_landing_page`), then RE-READS that
tenant through the repository layer inside ONE :class:`TrustRootUoW` — the
routing result is a dict, and the identity helpers take the ORM model.

**The builder seam is NARROWED, not widened, by the fourth document.** Only
the revocation-list handler ever resolves signing MATERIAL (a private-key
decrypt under the deployment KEK) — a structural guard
(``tests/unit/test_architecture_well_known_material_isolation.py``) pins
that exactly one named function in this module calls
``resolve_signing_material``/``_resolve_signing_provider``. The other three
handlers read only what they already read: ``publishable_at()`` and (for
adagents.json) the authorized-property records. Each handler is its own
closure over the shared ``(uow, tenant, now)`` — none receives a parameter
it discards, unlike the three-argument union shape this module used before
the fourth document.

The anti-drift guarantee this module provides is that all four handlers
call the SAME ``publishable_at()`` / ``all_revoked()`` methods with the SAME
``grace_seconds`` — not that one shared read backs all four documents in one
request. Each document is built in its own request, its own UoW, and its own
``now``; brand.json and jwks.json are never the same HTTP response.

Every DB read is dispatched with ``asyncio.to_thread`` — these are
synchronous calls on an async route, and blocking the event loop on an
unauthenticated endpoint is a denial-of-service surface. The three
secret-free documents serve unconditionally at any Host, including
``http://localhost`` — this module never consults ``origin_is_publishable``,
exactly as before the fourth document existed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from src.core.agent_identity import (
    ADAGENTS_JSON_PATH,
    BRAND_JSON_PATH,
    GOVERNANCE_REVOCATIONS_PATH,
    JWKS_PATH,
    agent_origin_host,
)
from src.core.config import get_config
from src.core.database.repositories.uow import TrustRootUoW
from src.core.domain_routing import route_landing_page
from src.core.signing import (
    CACHE_MAX_AGE_SECONDS,
    build_adagents_json,
    build_brand_json,
    build_jwks,
    publishable_revocation_list,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.core.database.models import SigningKey, Tenant
    from src.core.database.repositories.uow import TrustRootUoW as TrustRootUoWType

logger = logging.getLogger(__name__)

router = APIRouter()

#: A document body plus the ``Cache-Control: max-age`` seconds to serve it
#: with. Per-document, not a module constant: the revocation list's max-age
#: is derived from its own ``next_update``, not ``CACHE_MAX_AGE_SECONDS``.
Document = tuple[dict[str, Any], int]

#: A handler owns exactly its own reads inside the shared UoW/tenant/now —
#: nothing is loaded on its behalf that it might discard. Returns None when
#: the document cannot be built at all (e.g. no active signing key), which
#: the seam maps to 404, same as a vanished tenant.
DocumentBuilder = Callable[["TrustRootUoWType", "Tenant", datetime], "Document | None"]


def _build_document(tenant_id: str, build: DocumentBuilder) -> Document | None:
    """Open one session, re-read the tenant, and hand off to *build*.

    Returns None (-> 404) when the tenant cannot be re-read — a deletion
    between routing and this read. This is the ONLY place that logs a
    vanished-tenant warning; a handler that returns None for its OWN reason
    (e.g. no active signing key) logs its own message instead, so the two
    causes are never conflated in production logs. Synchronous by design;
    the caller hands it to a thread.
    """
    with TrustRootUoW(tenant_id) as uow:
        assert uow.tenant_config is not None

        tenant = uow.tenant_config.get_tenant()
        if tenant is None:
            logger.warning("[TRUST-ROOT] tenant %s vanished between routing and read", tenant_id)
            return None

        return build(uow, tenant, datetime.now(UTC))


async def _serve(request: Request, build: DocumentBuilder) -> JSONResponse:
    """Resolve the Host's tenant, build its document, and answer with its cache TTL."""
    routing = await asyncio.to_thread(route_landing_page, dict(request.headers))
    if not routing.tenant:
        logger.info("[TRUST-ROOT] no tenant for host %s", routing.effective_host)
        raise HTTPException(status_code=404, detail="Not found")

    tenant_id = routing.tenant["tenant_id"]
    result = await asyncio.to_thread(_build_document, tenant_id, build)
    if result is None:
        # No trust root at this host — either the tenant vanished (logged in
        # _build_document) or the handler itself declined (logged there).
        raise HTTPException(status_code=404, detail="Not found")

    document, max_age = result
    return JSONResponse(document, headers={"Cache-Control": f"public, max-age={max_age}"})


def _publishable_keys(uow: TrustRootUoWType, now: datetime) -> Sequence[SigningKey]:
    """The published key set every secret-free document is a projection of.

    ONE call site for ``publishable_at`` (rather than one per handler) is what
    keeps the module docstring's anti-drift claim ("all documents call the
    SAME method with the SAME grace_seconds") true by construction instead of
    by three copies happening to agree.
    """
    assert uow.signing_keys is not None
    grace_seconds = get_config().signing.grace_seconds
    return uow.signing_keys.publishable_at(now=now, grace_seconds=grace_seconds)


def _brand_json_handler(uow: TrustRootUoWType, tenant: Tenant, now: datetime) -> Document | None:
    """The brand.json a verifier reaches from ``identity.brand_json_url``.

    Hop 2 of the discovery algorithm: it carries the ``agents[]`` entry whose
    ``url`` byte-equals the endpoint the counterparty invoked, and that entry's
    ``jwks_uri``.
    """
    keys = _publishable_keys(uow, now)
    return build_brand_json(tenant, keys), CACHE_MAX_AGE_SECONDS


def _adagents_json_handler(uow: TrustRootUoWType, tenant: Tenant, now: datetime) -> Document | None:
    """The publisher-pin document for properties on this host.

    Its ``signing_keys[]`` is authoritative for SELL-SIDE webhook delivery only
    — request signatures are governed by brand.json's ``jwks_uri``. Both are
    built from the same key set so the two cannot drift.
    """
    assert uow.authorized_properties is not None
    keys = _publishable_keys(uow, now)
    # An adagents.json served at OUR host speaks for the properties on that
    # host — never for properties a publisher hosts elsewhere, whose own
    # adagents.json is the document a verifier consults. A tenant whose
    # agent host is not a property domain therefore claims nothing here.
    properties = uow.authorized_properties.list_for_publisher_domain(agent_origin_host(tenant))
    return build_adagents_json(tenant, keys, properties), CACHE_MAX_AGE_SECONDS


def _jwks_json_handler(uow: TrustRootUoWType, tenant: Tenant, now: datetime) -> Document | None:
    """The RFC 7517 key set that governs request-signature verification."""
    keys = _publishable_keys(uow, now)
    return build_jwks(keys), CACHE_MAX_AGE_SECONDS


def _governance_revocations_handler(uow: TrustRootUoWType, tenant: Tenant, now: datetime) -> Document | None:
    """The combined revocation list this agent PUBLISHES as a signer (security.mdx :1543).

    HTTP CONCERNS ONLY. The document is one layer operation —
    :func:`~src.core.signing.publishable_revocation_list` — which resolves the signing
    material, reads the revoked rows, applies the configured interval, builds and signs.
    This handler used to assemble those five steps itself (#1757); a route that composes a
    pipeline out of layer primitives is a second place the pipeline can be composed
    differently.

    ``None`` -> 404 with its own log message, never the vanished-tenant one: revoking a
    tenant's only key WITHDRAWS this document rather than serving one unsigned or signed
    by a dead key. Fail-closed, and "rotate before revoke" is the operational invariant it
    asserts.

    The max-age is derived HERE and not in the layer, deliberately. When the document goes
    stale is a domain fact (the revocation interval), so the layer returns ``next_update``
    as an INSTANT; turning an instant into a cache lifetime is HTTP shaping. It is also why
    this document alone does not use ``CACHE_MAX_AGE_SECONDS`` like its three siblings —
    their freshness is a published TTL, this one's is when the next list is due.
    """
    assert uow.signing_keys is not None
    published = publishable_revocation_list(uow.signing_keys, tenant, now=now)
    if published is None:
        return None

    document, next_update = published
    return document, max(1, int((next_update - now).total_seconds()))


@router.get(BRAND_JSON_PATH)
async def brand_json(request: Request) -> JSONResponse:
    return await _serve(request, _brand_json_handler)


@router.get(ADAGENTS_JSON_PATH)
async def adagents_json(request: Request) -> JSONResponse:
    return await _serve(request, _adagents_json_handler)


@router.get(JWKS_PATH)
async def jwks_json(request: Request) -> JSONResponse:
    return await _serve(request, _jwks_json_handler)


@router.get(GOVERNANCE_REVOCATIONS_PATH)
async def governance_revocations_json(request: Request) -> JSONResponse:
    return await _serve(request, _governance_revocations_handler)
