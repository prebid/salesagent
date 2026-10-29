"""The principal lookups: the resolver's database primitives.

Three readers, one per way a principal is identified. A request identifies its principal
by the credential it presents — the bearer it sent (``get_principal_from_token``) or the
RFC 9421 signature it signed with (``get_principal_by_agent_url``); server-initiated work
identifies the owner of a stored row by id (``get_principal_by_id``). All three are scoped
to a tenant, because a principal is a row in exactly one tenant, and all three hand back
the model built inside the session so the resolver keeps what was loaded.
"""

import logging
from datetime import UTC, datetime

from src.core.credentials import hash_token
from src.core.database.database_session import execute_with_retry
from src.core.database.repositories.oauth_access_token import OAuthAccessTokenRepository
from src.core.database.repositories.oauth_client import OAuthClientRepository
from src.core.database.repositories.principal import PrincipalRepository
from src.core.database.repositories.tenant import TenantRepository
from src.core.oauth_service import DEFAULT_MCP_OAUTH_SCOPE, mcp_resource_for_host
from src.core.schemas import Principal

logger = logging.getLogger(__name__)


def get_principal_from_token(token: str, tenant_id: str) -> Principal | None:
    """The principal *token* authenticates inside *tenant_id*, or ``None``.

    TWO kinds of bearer resolve here, because the ``Authorization`` header carries exactly
    one value and the seller accepts either form of it. Both are now opaque values the
    database holds the ``sha256`` of, so the hash is computed ONCE here and offered to two
    tables:

    1. A first-party MCP OAuth access token — a high-entropy string the token endpoint
       returned once, whose ``oauth_access_tokens`` row carries the tenant, principal,
       client, scopes and RFC 8707 ``resource`` the grant named.
    2. A seller-minted buyer token (``tok_``) — an opaque value whose ``sha256`` the
       ``principals`` row stores.

    The OAuth form is tried first and the opaque form is the fallback, which is the order
    the two were added in. Order cannot change WHICH principal answers: each form is 256
    bits of randomness drawn by a different mint path, so one plaintext hashing into both
    tables is a collision nobody can construct, and at most one of the two ever resolves.

    Whichever form it took, the answer is a ``Principal`` row and nothing else, and both
    lookups are scoped to the tenant the request addressed, so a credential minted for one
    tenant never acts on another. Neither plaintext is written anywhere.

    ``None`` means the bearer resolved to nobody. The resolver reads that as
    ``bearer_rejected`` and raises AUTH_INVALID (``_resolve_identity`` step 4), so a bearer
    that is neither a live OAuth token nor a known buyer token is terminal — a request
    signature cannot launder it.
    """

    token_hash = hash_token(token)

    def _lookup_principal(session):
        oauth_principal = _oauth_principal(session, token_hash, tenant_id)
        if oauth_principal is not None:
            return oauth_principal

        row = PrincipalRepository(session, tenant_id).find_by_token_hash(token_hash)
        return Principal.from_row(row) if row else None

    return execute_with_retry(_lookup_principal)


def _oauth_principal(session, token_hash: str, tenant_id: str) -> Principal | None:
    """The principal a stored MCP OAuth access token names in *tenant_id*, or ``None``.

    A hash lookup and the two identity rows it points at. ``None`` is returned rather than
    raised at every step, so a bearer that is not an OAuth token at all falls through to
    the opaque-token lookup and one that IS but no longer resolves is refused by the
    caller's single AUTH_INVALID.

    1. ``find_live`` answers the row, or nothing. It is the ONE definition of usable and it
       decides all of it in SQL — the hash matches inside this tenant, ``expires_at`` is
       still ahead of now, ``revoked_at`` is NULL — so an expired, revoked, foreign-tenant
       or never-issued token are indistinguishable here and none of the three conditions is
       re-tested in Python where it could be forgotten or spelled differently.

       This is what the JWT's four claim checks collapsed into. ``exp`` became the SQL
       predicate above. The ``tenant_id`` claim match became STRUCTURAL: the repository's
       tenant scoping is constructor-bound, so a token minted for one seller is not visible
       to another seller's query and there is no spelling of this lookup that reaches
       across (the invariant ``tests/unit/test_cross_tenant_query_isolation.py`` grades).
    ``aud`` became the stored ``resource`` column. The grant binds it to the client's
    tenant host; this lookup compares it with that tenant's registered MCP resource.
    The required ``mcp:principal`` scope must also be present. Signature verification is absent,
       because the authorization server and this resource server are the same process on
       the same session factory: there is no verifier that cannot read the authoritative
       rows. Consequently this path reads no environment and holds no secret.

    2. The OAuth client the token was issued to is still registered and active for that
       same principal. The row's composite FK cascades on client DELETION, not on
       deactivation, so this stays the revocation point for a whole client: deactivating
       one must stop every token already issued under it, in the same request.

    3. The tenant row is active, and the principal the row names is still a row of that
       tenant. The token row names a principal; the ``principals`` row is the fact.
    """
    access_token = OAuthAccessTokenRepository(session, tenant_id).find_live(token_hash, now=datetime.now(UTC))
    if access_token is None:
        return None

    oauth_client = OAuthClientRepository(session).get_active(
        tenant_id=tenant_id,
        client_id=access_token.client_id,
        principal_id=access_token.principal_id,
    )
    if oauth_client is None:
        logger.warning(
            "A live OAuth access token names client %r, which is no longer active for principal %r, "
            "so it resolves to nobody",
            access_token.client_id,
            access_token.principal_id,
        )
        return None

    tenant = TenantRepository(session).get_active(tenant_id)
    if tenant is None:
        return None
    if access_token.resource != mcp_resource_for_host(tenant.virtual_host):
        return None
    if DEFAULT_MCP_OAUTH_SCOPE not in access_token.scopes:
        return None

    row = PrincipalRepository(session, tenant_id).get(access_token.principal_id)
    return Principal.from_row(row) if row else None


def get_principal_by_agent_url(agent_url: str, tenant_id: str) -> Principal | None:
    """The principal onboarded at *agent_url* inside *tenant_id*, or ``None``.

    The other way a request identifies its principal, and the newest: a valid RFC 9421
    signature. Verification establishes exactly one fact — "the request was issued by the
    agent whose ``jwks_uri`` contains the ``keyid``" (security.mdx @ v3.1.1 § Agent
    identity) — and names that agent by the ``agents[]`` entry whose ``jwks_uri`` resolved
    the key. So a verified signature IS a credential, and this is how it resolves to a
    principal when the caller presented no bearer at all — neither an OAuth access token
    nor an opaque buyer token, since both are answered above and both are terminal.

    Scoped to the tenant like the bearer lookups, and unique within it
    (``uq_principals_tenant_agent_url``), so the answer is one row rather than whichever
    sorted first.
    """

    def _lookup_principal(session):
        row = PrincipalRepository(session, tenant_id).find_by_agent_url(agent_url)
        return Principal.from_row(row) if row else None

    return execute_with_retry(_lookup_principal)


def get_principal_by_id(tenant_id: str, principal_id: str) -> Principal | None:
    """The principal *principal_id* names inside *tenant_id*, or ``None``.

    For resolution from stored ids (``resolved_identity.identity_of``): the owner of a
    media buy or creative a server-initiated job acts on.
    """

    def _lookup_principal(session):
        row = PrincipalRepository(session, tenant_id).get(principal_id)
        return Principal.from_row(row) if row else None

    return execute_with_retry(_lookup_principal)
