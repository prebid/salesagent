"""TMP Provider discovery endpoint.

Exposes:
    GET /tenant/{tenant_id}/tmp-providers/discovery

This endpoint is polled by the TMP Router every 30 s to discover which
provider endpoints to fan out context and identity match requests to.

Authentication reuses the codebase's one credential gate, resolved **inside the
path's tenant**: :func:`src.core.auth_utils.get_principal_from_token` is called
with the ``{tenant_id}`` from the URL, and its docstring is the guarantee this
route needs — "If tenant_id specified, ONLY look in that tenant."  A credential
issued to tenant A therefore resolves to nothing on tenant B's path, so a
cross-tenant read is *inexpressible* rather than merely rejected: there is no
``resolved_tenant == tenant_id`` comparison to get wrong, and no second
authentication scheme (no ``TMP_DISCOVERY_API_KEYS``, no "OPEN" mode, no
per-route header list) to keep in step with the first (#1197 review).

That also satisfies the pinned spec's authentication MUST for this surface —
AdCP 3.1.1 ``trusted-match/specification.mdx`` §"Provider registration
security": routers exposing dynamic registration MUST authenticate callers, and
static API keys are conformant only alongside IP allow-listing.  A per-tenant Sales Agent
credential is not a static process-global key.

Token extraction is ``UnifiedAuthMiddleware``'s job (``x-adcp-auth``, else
``Authorization: Bearer``), the same for this route as for every other REST
endpoint.

Response schema — ``TMPDiscoveryResponse``.  Each provider entry is the closed
key set of the pinned ``provider-registration.json``
(:data:`PROVIDER_REGISTRATION_SCHEMA`):
{
  "tenant_id": "si-host",
  "providers": [
    {
      "provider_id": "5f1c0e3a9b7d4e8fa1c2b3d4e5f60718",
      "endpoint": "https://si-agent.localhost:3003",
      "context_match": true,
      "identity_match": true,
      "countries": ["US"],
      "uid_types": ["publisher_first_party", "uid2", "hashed_email"],
      "timeout_ms": 200,
      "priority": 0,
      "status": "active"
    }
  ]
}

``countries`` / ``uid_types`` / ``properties`` are omitted — not ``null`` — when
the provider restricts nothing: the schema types all three ``array`` with
``minItems: 1``.  ``name`` is not on this wire at all (it is not in the closed
schema); it lives on the admin serialization.  See
``TMPProviderDiscoveryEntry.from_row``.

Only providers whose status is 'active' or 'draining' are returned.
Providers with status 'inactive' are excluded entirely.

Denials, per the pinned ``enums/error-code.json`` @3.1.1: no credential →
``AUTH_MISSING`` (correctable, 401); a credential that does not resolve in the
tenant → ``AUTH_INVALID`` (terminal, 401).  An unknown tenant is therefore a 401
too, not a 404 — the credential is resolved first, and a tenant that does not
exist cannot resolve one.  The 404 (``ACCOUNT_NOT_FOUND``) is reachable only for
an authenticated caller whose tenant row disappeared between the two reads.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import ValidationError

from src.core.auth_context import AuthContext, get_auth_context
from src.core.auth_utils import get_principal_from_token
from src.core.database.repositories.uow import TMPProviderUoW
from src.core.exceptions import AdCPAccountNotFoundError, AdCPAuthInvalidError, AdCPAuthMissingError
from src.core.logging_config import log_safe
from src.core.schemas.tmp_provider import TMPDiscoveryResponse, TMPProviderDiscoveryEntry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tmp-providers"])

#: The discovery wire's authority, as a path that resolves in this tree.
#:
#: The single declaration of the pinned schema this endpoint's provider entries
#: conform to.  Every other site that needs to name it — the ORM model, the
#: migration, the sync service's operator log line, the tests — references this
#: constant instead of re-typing a path, so the citation cannot drift out of
#: step with the file it points at (#1197 review).  ``tests/helpers/pinned_schema``
#: reads exactly this relative path out of the installed SDK, and
#: ``test_tmp_providers_discovery_route.py`` loads it, so a citation pointing at
#: a non-existent file fails a test rather than misleading a router operator.
#:
#: Upstream: https://github.com/adcontextprotocol/adcp/blob/main/static/schemas/v1/trusted-match/provider-registration.json
PROVIDER_REGISTRATION_SCHEMA = "trusted-match/provider-registration.json"

#: The one cross-service path this feature publishes, declared once.
#: The route decorator below is registered *from* this constant, and the model
#: and the admin blueprint reference it rather than restating a path in prose,
#: so there is no second copy for an edit to leave behind (#1197 review).
DISCOVERY_ROUTE = "/tenant/{tenant_id}/tmp-providers/discovery"


def _require_tenant_credential(tenant_id: str, auth_ctx: AuthContext = get_auth_context) -> str:
    """Resolve the caller's credential **within** *tenant_id*, or raise 401.

    Declared ``def``, not ``async def``, for the same reason ``_require_auth_dep``
    is: the credential lookup is blocking DB I/O, and FastAPI runs a sync
    dependency in its threadpool instead of on the event loop.

    Tenant isolation is a property of the resolution, not a check layered on top
    of it: ``get_principal_from_token(token, tenant_id)`` only ever searches the
    named tenant (its own docstring: "If tenant_id specified, ONLY look in that
    tenant"), so there is no state in which a credential from another tenant
    yields a principal here.  Accepted credentials are a tenant's principal
    access tokens and its admin token — the same set every other Sales Agent
    surface accepts, which is why this route no longer owns an authentication
    scheme of its own (#1197 review).

    Returns the resolved principal id so the route can log *who* polled;
    unauthenticated callers never reach the route body.
    """
    token = (auth_ctx.auth_token or "").strip()
    if not token:
        # Nothing was presented: correctable — send a credential and retry.
        raise AdCPAuthMissingError(
            "Authentication required.",
            suggestion=(
                f"Provide an access token for tenant '{tenant_id}' via x-adcp-auth or Authorization: Bearer <token>."
            ),
        )

    principal_id = get_principal_from_token(token, tenant_id)[0]
    if not principal_id:
        # A credential WAS presented and did not resolve in this tenant: terminal.
        # The router polls every 30 s, so calling this correctable would have it
        # retry a credential that cannot start working — the pinned enum's
        # AUTH_INVALID metadata says "do NOT auto-retry … rotate keys … or escalate"
        # (adcp/_schemas/3.1/enums/error-code.json @3.1.1). Deliberately does not
        # distinguish "unknown token" from "token belonging to another tenant": that
        # difference is what a cross-tenant prober would enumerate.
        raise AdCPAuthInvalidError(
            "Authentication failed.",
            suggestion=(
                f"The presented credential is not valid for tenant '{tenant_id}'. "
                "Issue the router a principal access token for that tenant "
                "(Admin UI → Advertisers → API Token) rather than retrying."
            ),
        )
    return principal_id


# Module-level singleton, matching require_auth/raw_json_body (ruff B008 forbids
# Depends() in a parameter default), and the object `app.dependency_overrides`
# keys on is the private function above.
require_tenant_credential: Any = Depends(_require_tenant_credential)


@router.get(DISCOVERY_ROUTE, response_model=TMPDiscoveryResponse, response_model_exclude_none=True)
async def tmp_providers_discovery(
    tenant_id: str, principal_id: str = require_tenant_credential
) -> TMPDiscoveryResponse:
    """Return the active TMP provider set for a tenant.

    Polled by the TMP Router every 30 s.  Requires a credential issued by the
    tenant in the path — see :func:`_require_tenant_credential`, where the
    tenant scoping lives.

    Returns the typed :class:`TMPDiscoveryResponse` rather than a hand-built
    ``JSONResponse``: FastAPI then publishes an OpenAPI schema for this
    versioned contract, and the response model IS the pinned schema's codegen, so
    the outgoing key set is the schema's by construction rather than by a
    hand-maintained key list (#1197 review).

    Lifecycle filtering:
      active   → included
      draining → included (router stops sending new requests but in-flight complete)
      inactive → excluded
    """
    # Single TMPProviderUoW block: it already exposes both tmp_providers and
    # tenant_config repositories, so the tenant-existence check and the
    # provider read run as ONE transaction rather than two separate ones.
    #
    # from_row() is also called INSIDE this block — TMPProvider
    # attributes expire on commit (default expire_on_commit=True), so
    # serializing after the `with` block closes hits a detached session and
    # raises DetachedInstanceError.
    with TMPProviderUoW(tenant_id) as uow:
        # No repository narrowing here: TMPProviderUoW exposes its repositories
        # through RepositoryAccessor, which hands back the concrete repository
        # inside the `with` block and raises the same typed
        # AdCPServiceUnavailableError outside it. An `assert` would have been
        # wrong twice — `python -O` strips it, and an AssertionError escapes as
        # an un-enveloped 500 instead of the typed AdCP envelope this endpoint's
        # contract promises (#1197 review).
        if uow.tenant_config.get_tenant() is None:
            raise AdCPAccountNotFoundError(
                f"Tenant '{tenant_id}' not found.",
                suggestion="Provide a valid tenant ID.",
            )

        providers = uow.tmp_providers.list_syncable()

        # Construction, not serialization: TMPProviderDiscoveryEntry extends the
        # pinned SDK type, so a row that cannot be represented conformantly fails
        # HERE rather than reaching a strict router as an invalid entry (#1197
        # review). ``response_model_exclude_none=True`` on the decorator is what
        # keeps absent conditional arrays omitted rather than nulled — the schema
        # types all three as arrays with minItems: 1.
        #
        # Per row, not per response. The only rows that can fail conversion are
        # legacy ones predating TMPProviderRegistration (e.g. identity_match with
        # no countries — a state the schema's if/then makes unrepresentable at any
        # serialization). Raising for the whole tenant would let one such row take
        # discovery down for every other provider, which is the per-batch failure
        # mode this feature removed from the sync path and the health scheduler.
        # The row is dropped and named at ERROR so an operator can repair it.
        provider_list = []
        for provider in providers:
            try:
                provider_list.append(TMPProviderDiscoveryEntry.from_row(provider))
            except ValidationError:
                logger.error(
                    "[TMP discovery] Skipping provider %s for tenant=%s — the row cannot be "
                    "represented conformantly (%s). Re-save it through the admin form to repair it; "
                    "the remaining providers are unaffected.",
                    log_safe(provider.provider_id),
                    log_safe(tenant_id),
                    PROVIDER_REGISTRATION_SCHEMA,
                    exc_info=True,
                )

    logger.debug(
        "[TMP discovery] tenant=%s principal=%s returned %d provider(s)",
        log_safe(tenant_id),
        log_safe(principal_id),
        len(provider_list),
    )

    return TMPDiscoveryResponse(tenant_id=tenant_id, providers=provider_list)
