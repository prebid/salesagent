"""OAuth metadata and token endpoints for MCP authentication."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote_plus, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata
from starlette.datastructures import FormData, QueryParams

from src.core.credentials import hash_token
from src.core.database.database_session import execute_with_retry
from src.core.database.repositories.oauth_access_token import OAuthAccessTokenRepository
from src.core.database.repositories.oauth_client import OAuthClientRepository
from src.core.database.repositories.tenant import TenantRepository
from src.core.database.repositories.tenant_lookup import TenantLookupRepository
from src.core.oauth_service import (
    DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS,
    DEFAULT_AUTHORIZATION_CODE_LIFETIME_SECONDS,
    DEFAULT_MCP_OAUTH_SCOPE,
    generate_access_token,
    generate_authorization_code,
    get_mcp_oauth_issuer,
    hash_authorization_code,
    mcp_resource_for_host,
    valid_pkce_challenge,
    verify_client_secret,
    verify_pkce_s256,
)

router = APIRouter(tags=["oauth"])


@dataclass(frozen=True)
class _OAuthClientAuthResult:
    tenant_id: str
    principal_id: str
    client_id: str
    client_secret_hash: str
    scopes: list[str]
    redirect_uris: list[str]
    resource: str


@dataclass(frozen=True)
class _OAuthAuthorizationCodeResult:
    tenant_id: str
    principal_id: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    scopes: list[str]
    resource: str
    expires_at: datetime
    used_at: datetime | None


@router.get(
    "/.well-known/oauth-protected-resource",
    response_model=ProtectedResourceMetadata,
    response_model_exclude_none=True,
)
@router.get(
    "/.well-known/oauth-protected-resource/mcp",
    response_model=ProtectedResourceMetadata,
    response_model_exclude_none=True,
)
async def oauth_protected_resource_metadata(request: Request) -> JSONResponse:
    issuer = get_mcp_oauth_issuer()
    resource = _resource_for_request(request)
    if resource is None:
        return _oauth_error("invalid_target", "No active tenant is registered for this host", 400)
    payload = {
        "resource": resource,
        "authorization_servers": [issuer],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [DEFAULT_MCP_OAUTH_SCOPE],
    }
    ProtectedResourceMetadata.model_validate(payload)
    return JSONResponse(payload)


def _resource_for_request(request: Request) -> str | None:
    def _lookup(session):
        tenant = TenantLookupRepository(session).find_active_by_virtual_host(request.headers.get("host", ""))
        return mcp_resource_for_host(tenant.virtual_host) if tenant else None

    return execute_with_retry(_lookup)


@router.get(
    "/.well-known/oauth-authorization-server",
    response_model=OAuthMetadata,
    response_model_exclude_none=True,
)
async def oauth_authorization_server_metadata() -> JSONResponse:
    issuer = get_mcp_oauth_issuer()
    payload = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/oauth/token",
        "grant_types_supported": ["authorization_code", "client_credentials"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": [DEFAULT_MCP_OAUTH_SCOPE],
    }
    OAuthMetadata.model_validate(payload)
    return JSONResponse(payload)


@router.get("/authorize", response_model=None)
async def oauth_authorize(request: Request) -> JSONResponse | RedirectResponse:
    """Issue an authorization code for a provisioned MCP OAuth client.

    PSA provisions OAuth clients out-of-band for a specific buyer/operator principal.
    For this flow, that provisioned principal is treated as the resource owner.
    The `/authorize` endpoint does not perform an interactive end-user login or
    consent step.

    Authorization Code + PKCE is supported for MCP host compatibility, while
    client credentials remain available for direct machine-to-machine use.

    AdCP reference (pinned by this project to adcp==6.6.0 / AdCP 3.1.1):
    adcp/_schemas/3.1/bundled/protocol/get-adcp-capabilities-response.json
    (`account.require_operator_auth` and `account.authorization_endpoint`).
    """
    params = request.query_params
    if any(len(params.getlist(name)) > 1 for name in ("client_id", "redirect_uri")):
        return _oauth_error("invalid_request", "Client and redirect URI must be unambiguous", 400)
    client_id = str(params.get("client_id") or "")
    redirect_uri = str(params.get("redirect_uri") or "")
    code_challenge = str(params.get("code_challenge") or "")
    code_challenge_method = str(params.get("code_challenge_method") or "")
    state = params.get("state")

    oauth_client = _load_oauth_client(client_id)
    if not oauth_client:
        return _oauth_error("invalid_request", "Unknown OAuth client", 400)
    if redirect_uri not in oauth_client.redirect_uris:
        return _oauth_error("invalid_request", "Redirect URI is not registered for this OAuth client", 400)
    resource = str(params.get("resource") or oauth_client.resource).rstrip("/")
    if _has_duplicate_parameters(params):
        return _authorization_response(redirect_uri, state, error="invalid_request")
    if str(params.get("response_type") or "") != "code":
        error = "unsupported_response_type" if params.get("response_type") else "invalid_request"
        return _authorization_response(redirect_uri, state, error=error)
    if not valid_pkce_challenge(code_challenge) or code_challenge_method != "S256":
        return _authorization_response(redirect_uri, state, error="invalid_request")
    if resource != oauth_client.resource:
        return _authorization_response(redirect_uri, state, error="invalid_target")

    requested_scopes = _parse_requested_scopes(str(params.get("scope") or ""), oauth_client.scopes)
    if requested_scopes is None:
        return _authorization_response(redirect_uri, state, error="invalid_scope")

    authorization_code = generate_authorization_code()
    now = datetime.now(UTC)
    _store_authorization_code(
        code_hash=hash_authorization_code(authorization_code),
        tenant_id=oauth_client.tenant_id,
        client_id=oauth_client.client_id,
        principal_id=oauth_client.principal_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scopes=requested_scopes,
        resource=resource,
        expires_at=now + timedelta(seconds=DEFAULT_AUTHORIZATION_CODE_LIFETIME_SECONDS),
        created_at=now,
    )

    return _authorization_response(redirect_uri, state, code=authorization_code)


def _authorization_response(redirect_uri: str, state: str | None, **params: str) -> RedirectResponse:
    if state is not None:
        params["state"] = state
    return RedirectResponse(_append_query_params(redirect_uri, params), status_code=302)


def _has_duplicate_parameters(params: FormData | QueryParams) -> bool:
    return any(len(params.getlist(name)) > 1 for name in params)


@router.post("/oauth/token")
async def oauth_token(request: Request) -> JSONResponse:
    form = await request.form()
    if _has_duplicate_parameters(form) or (
        request.headers.get("authorization") and (form.get("client_secret") or form.get("client_assertion"))
    ):
        return _oauth_error("invalid_request", "Duplicate parameters or multiple client authentication methods", 400)
    grant_type = str(form.get("grant_type") or "")
    if grant_type == "client_credentials":
        return _oauth_client_credentials_token(request, form)
    if grant_type == "authorization_code":
        return _oauth_authorization_code_token(request, form)
    return _oauth_error("unsupported_grant_type", "Only authorization_code and client_credentials are supported", 400)


def _oauth_client_credentials_token(request: Request, form) -> JSONResponse:
    authenticated_client = _authenticate_confidential_client(request, form)
    if isinstance(authenticated_client, JSONResponse):
        return authenticated_client
    oauth_client = authenticated_client

    requested_scopes = _parse_requested_scopes(str(form.get("scope") or ""), oauth_client.scopes)
    if requested_scopes is None:
        return _oauth_error("invalid_scope", "Requested scope is not allowed", 400)

    access_token = _mint_access_token(
        tenant_id=oauth_client.tenant_id,
        principal_id=oauth_client.principal_id,
        client_id=oauth_client.client_id,
        scopes=requested_scopes,
        resource=oauth_client.resource,
    )

    return _oauth_token_response(access_token, requested_scopes)


def _oauth_authorization_code_token(request: Request, form) -> JSONResponse:
    authenticated_client = _authenticate_confidential_client(request, form)
    if isinstance(authenticated_client, JSONResponse):
        return authenticated_client
    oauth_client = authenticated_client

    authorization_code = _load_authorization_code(str(form.get("code") or ""))
    grant_result = _validate_authorization_code_grant(
        authorization_code,
        oauth_client=oauth_client,
        redirect_uri=str(form.get("redirect_uri") or ""),
        resource=oauth_client.resource,
        code_verifier=str(form.get("code_verifier") or ""),
    )
    if isinstance(grant_result, JSONResponse):
        return grant_result
    authorization_code = grant_result

    if not _mark_authorization_code_used(hash_authorization_code(str(form.get("code") or ""))):
        return _oauth_error("invalid_grant", "Authorization code is expired or already used", 400)
    access_token = _mint_access_token(
        tenant_id=authorization_code.tenant_id,
        principal_id=authorization_code.principal_id,
        client_id=authorization_code.client_id,
        scopes=authorization_code.scopes,
        resource=authorization_code.resource,
    )

    return _oauth_token_response(access_token, authorization_code.scopes)


def _validate_authorization_code_grant(
    authorization_code: _OAuthAuthorizationCodeResult | None,
    *,
    oauth_client: _OAuthClientAuthResult,
    redirect_uri: str,
    resource: str,
    code_verifier: str,
) -> _OAuthAuthorizationCodeResult | JSONResponse:
    if not authorization_code or authorization_code.client_id != oauth_client.client_id:
        return _oauth_error("invalid_grant", "Authorization code is invalid", 400)
    if authorization_code.redirect_uri != redirect_uri:
        return _oauth_error("invalid_grant", "Redirect URI does not match authorization request", 400)
    if authorization_code.resource != resource:
        return _oauth_error("invalid_target", "The requested resource is not this MCP server", 400)
    if authorization_code.used_at is not None or _as_utc(authorization_code.expires_at) <= datetime.now(UTC):
        return _oauth_error("invalid_grant", "Authorization code is expired or already used", 400)
    if authorization_code.code_challenge_method != "S256":
        return _oauth_error("invalid_grant", "Unsupported authorization code challenge method", 400)
    if not _verify_authorization_code_pkce(code_verifier, authorization_code.code_challenge):
        return _oauth_error("invalid_grant", "PKCE code verifier is invalid", 400)
    return authorization_code


def _verify_authorization_code_pkce(code_verifier: str, code_challenge: str) -> bool:
    try:
        return verify_pkce_s256(code_verifier, code_challenge)
    except UnicodeEncodeError:
        return False


def _authenticate_confidential_client(request: Request, form) -> _OAuthClientAuthResult | JSONResponse:
    client_id, client_secret = _extract_client_auth(request, form)
    if not client_id or not client_secret:
        return _oauth_error("invalid_client", "Client authentication is required", 401)

    oauth_client = _load_oauth_client(client_id)
    if not oauth_client or not verify_client_secret(client_secret, oauth_client.client_secret_hash):
        return _oauth_error("invalid_client", "Client authentication failed", 401)
    resource = str(form.get("resource") or oauth_client.resource).rstrip("/")
    if resource != oauth_client.resource:
        return _oauth_error("invalid_target", "The requested resource is not this client's MCP server", 400)
    return oauth_client


def _oauth_token_response(access_token: str, scopes: list[str]) -> JSONResponse:
    return JSONResponse(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS,
            "scope": " ".join(scopes),
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _mint_access_token(
    *,
    tenant_id: str,
    principal_id: str,
    client_id: str,
    scopes: list[str],
    resource: str,
) -> str:
    """Record an access token for this grant and return the plaintext, once.

    ONE mint for both grants. The two differ only in where the facts come from — the
    authenticated client for ``client_credentials``, the stored authorization code for
    ``authorization_code`` — and a second copy of the hash-and-store step is how one grant
    comes to store a different set of facts than the other.

    The plaintext is returned and nothing else: what is persisted is
    ``hash_token(access_token)``, so the string in the response exists in this process and
    in the client, and in no log and no column. Every other argument becomes a column the
    verifier reads instead of trusting a claim — including ``resource``, which is where the
    JWT's ``aud`` went: the RFC 8707 binding is now a stored fact compared at lookup,
    exactly as ``OAuthAuthorizationCode.resource`` already works.
    """
    access_token = generate_access_token()
    expires_at = datetime.now(UTC) + timedelta(seconds=DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS)

    def _mint(session):
        OAuthAccessTokenRepository(session, tenant_id).mint(
            token_hash=hash_token(access_token),
            client_id=client_id,
            principal_id=principal_id,
            scopes=scopes,
            resource=resource,
            expires_at=expires_at,
        )

    # ``execute_with_retry`` opens the session and commits it, the same way the
    # authorization code reached the database two calls earlier in this request.
    execute_with_retry(_mint)
    return access_token


def _load_oauth_client(client_id: str) -> _OAuthClientAuthResult | None:
    def _lookup(session):
        oauth_client = OAuthClientRepository(session).find_active_by_client_id_across_tenants(client_id)
        if not oauth_client:
            return None
        tenant = TenantRepository(session).get_active(oauth_client.tenant_id)
        if tenant is None:
            return None
        return _OAuthClientAuthResult(
            tenant_id=oauth_client.tenant_id,
            principal_id=oauth_client.principal_id,
            client_id=oauth_client.client_id,
            client_secret_hash=oauth_client.client_secret_hash,
            scopes=list(oauth_client.scopes or []),
            redirect_uris=list(oauth_client.redirect_uris or []),
            resource=mcp_resource_for_host(tenant.virtual_host),
        )

    return execute_with_retry(_lookup)


def _store_authorization_code(
    *,
    code_hash: str,
    tenant_id: str,
    client_id: str,
    principal_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    scopes: list[str],
    resource: str,
    expires_at: datetime,
    created_at: datetime,
) -> None:
    def _store(session):
        OAuthClientRepository(session).create_authorization_code(
            code_hash=code_hash,
            tenant_id=tenant_id,
            client_id=client_id,
            principal_id=principal_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            scopes=scopes,
            resource=resource,
            expires_at=expires_at,
            created_at=created_at,
        )

    execute_with_retry(_store)


def _load_authorization_code(code: str) -> _OAuthAuthorizationCodeResult | None:
    if not code:
        return None

    def _lookup(session):
        authorization_code = OAuthClientRepository(session).get_authorization_code(hash_authorization_code(code))
        if not authorization_code:
            return None
        return _OAuthAuthorizationCodeResult(
            tenant_id=authorization_code.tenant_id,
            principal_id=authorization_code.principal_id,
            client_id=authorization_code.client_id,
            redirect_uri=authorization_code.redirect_uri,
            code_challenge=authorization_code.code_challenge,
            code_challenge_method=authorization_code.code_challenge_method,
            scopes=list(authorization_code.scopes or []),
            resource=authorization_code.resource,
            expires_at=authorization_code.expires_at,
            used_at=authorization_code.used_at,
        )

    return execute_with_retry(_lookup)


def _mark_authorization_code_used(code_hash: str) -> bool:
    def _mark_used(session):
        return OAuthClientRepository(session).consume_authorization_code(code_hash, now=datetime.now(UTC))

    return bool(execute_with_retry(_mark_used))


def _extract_client_auth(request: Request, form) -> tuple[str | None, str | None]:
    authorization = request.headers.get("authorization") or ""
    if authorization.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(authorization[6:].strip(), validate=True).decode("utf-8")
            client_id, client_secret = decoded.split(":", 1)
            return unquote_plus(client_id), unquote_plus(client_secret)
        except (ValueError, UnicodeDecodeError):
            return None, None

    client_id = form.get("client_id")
    client_secret = form.get("client_secret")
    return (str(client_id) if client_id else None, str(client_secret) if client_secret else None)


def _parse_requested_scopes(scope_text: str, allowed_scopes: list[str]) -> list[str] | None:
    if not scope_text:
        return list(allowed_scopes) if DEFAULT_MCP_OAUTH_SCOPE in allowed_scopes else None
    if re.fullmatch(r"[\x21\x23-\x5b\x5d-\x7e]+(?: [\x21\x23-\x5b\x5d-\x7e]+)*", scope_text) is None:
        return None
    requested = scope_text.split(" ")
    allowed = set(allowed_scopes)
    if not set(requested).issubset(allowed) or DEFAULT_MCP_OAUTH_SCOPE not in requested:
        return None
    return requested


def _oauth_error(error: str, description: str, status_code: int) -> JSONResponse:
    headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    if status_code == 401:
        headers["WWW-Authenticate"] = 'Basic realm="mcp-oauth"'
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status_code,
        headers=headers,
    )


def _append_query_params(url: str, params: dict[str, str]) -> str:
    """Append *params* to *url*'s query, leaving every other byte of *url* untouched.

    APPENDED, never reassembled. The previous spelling decomposed the registered redirect
    URI with ``urlsplit`` and rebuilt it with ``urlunsplit``, which is the destination
    rewrite ``tests/unit/test_architecture_no_destination_rewrite.py`` refuses outright:
    "the URL a caller supplies must reach the egress seam byte-for-byte... There is no
    allowlist." A rebuild also normalizes — percent-encoding, an empty authority, a
    ``;``-bearing path — so what the client receives need not be what it registered.

    Appending is TOTAL here rather than merely usually right, because
    ``validate_oauth_redirect_uri`` already refused every shape that would break it before
    the URI was ever stored: absolute, HTTPS (or loopback HTTP), and FRAGMENT-FREE per RFC
    6749 §3.1.2. With no fragment there is nothing after the query for the appended
    parameters to land behind, so reading ``.query`` to pick the separator is the whole
    decision. Reading a component is fine; the guard objects to reassembly.
    """
    sep = "" if url.endswith(("?", "&")) else "&" if "?" in url else "?"
    return f"{url}{sep}{urlencode(params)}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
