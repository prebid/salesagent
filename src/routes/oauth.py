"""OAuth metadata and token endpoints for MCP authentication."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata

from src.core.database.database_session import execute_with_retry
from src.core.database.repositories.oauth_client import OAuthClientRepository
from src.core.oauth_service import (
    DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS,
    DEFAULT_AUTHORIZATION_CODE_LIFETIME_SECONDS,
    DEFAULT_MCP_OAUTH_SCOPE,
    generate_authorization_code,
    get_mcp_oauth_audience,
    get_mcp_oauth_issuer,
    hash_authorization_code,
    issue_mcp_access_token,
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
async def oauth_protected_resource_metadata() -> JSONResponse:
    issuer = get_mcp_oauth_issuer()
    payload = {
        "resource": get_mcp_oauth_audience(),
        "authorization_servers": [issuer],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [DEFAULT_MCP_OAUTH_SCOPE],
    }
    ProtectedResourceMetadata.model_validate(payload)
    return JSONResponse(payload)


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
    if str(params.get("response_type") or "") != "code":
        return _oauth_error("unsupported_response_type", "Only response_type=code is supported", 400)

    client_id = str(params.get("client_id") or "")
    redirect_uri = str(params.get("redirect_uri") or "")
    code_challenge = str(params.get("code_challenge") or "")
    code_challenge_method = str(params.get("code_challenge_method") or "")
    resource = str(params.get("resource") or get_mcp_oauth_audience()).rstrip("/")
    state = params.get("state")

    oauth_client = _load_oauth_client(client_id)
    if not oauth_client:
        return _oauth_error("invalid_request", "Unknown OAuth client", 400)
    if redirect_uri not in oauth_client.redirect_uris:
        return _oauth_error("invalid_request", "Redirect URI is not registered for this OAuth client", 400)
    if not code_challenge or code_challenge_method != "S256":
        return _oauth_error("invalid_request", "S256 PKCE code_challenge is required", 400)
    if resource != get_mcp_oauth_audience():
        return _oauth_error("invalid_target", "The requested resource is not this MCP server", 400)

    requested_scopes = _parse_requested_scopes(str(params.get("scope") or ""), oauth_client.scopes)
    if requested_scopes is None:
        return _oauth_error("invalid_scope", "Requested scope is not allowed", 400)

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

    redirect_params = {"code": authorization_code}
    if state is not None:
        redirect_params["state"] = str(state)
    return RedirectResponse(_append_query_params(redirect_uri, redirect_params), status_code=302)


@router.post("/oauth/token")
async def oauth_token(request: Request) -> JSONResponse:
    form = await request.form()
    grant_type = str(form.get("grant_type") or "")
    if grant_type == "client_credentials":
        return _oauth_client_credentials_token(request, form)
    if grant_type == "authorization_code":
        return _oauth_authorization_code_token(request, form)
    return _oauth_error("unsupported_grant_type", "Only authorization_code and client_credentials are supported", 400)


def _oauth_client_credentials_token(request: Request, form) -> JSONResponse:

    resource = str(form.get("resource") or get_mcp_oauth_audience()).rstrip("/")
    if resource != get_mcp_oauth_audience():
        return _oauth_error("invalid_target", "The requested resource is not this MCP server", 400)

    authenticated_client = _authenticate_confidential_client(request, form)
    if isinstance(authenticated_client, JSONResponse):
        return authenticated_client
    oauth_client = authenticated_client

    requested_scopes = _parse_requested_scopes(str(form.get("scope") or ""), oauth_client.scopes)
    if requested_scopes is None:
        return _oauth_error("invalid_scope", "Requested scope is not allowed", 400)

    now = datetime.now(UTC)
    access_token = issue_mcp_access_token(
        tenant_id=oauth_client.tenant_id,
        principal_id=oauth_client.principal_id,
        client_id=oauth_client.client_id,
        issuer=get_mcp_oauth_issuer(),
        audience=get_mcp_oauth_audience(),
        scopes=requested_scopes,
        now=now,
    )

    return _oauth_token_response(access_token, requested_scopes)


def _oauth_authorization_code_token(request: Request, form) -> JSONResponse:
    resource = str(form.get("resource") or get_mcp_oauth_audience()).rstrip("/")
    if resource != get_mcp_oauth_audience():
        return _oauth_error("invalid_target", "The requested resource is not this MCP server", 400)

    authenticated_client = _authenticate_confidential_client(request, form)
    if isinstance(authenticated_client, JSONResponse):
        return authenticated_client
    oauth_client = authenticated_client

    authorization_code = _load_authorization_code(str(form.get("code") or ""))
    grant_result = _validate_authorization_code_grant(
        authorization_code,
        oauth_client=oauth_client,
        redirect_uri=str(form.get("redirect_uri") or ""),
        resource=resource,
        code_verifier=str(form.get("code_verifier") or ""),
    )
    if isinstance(grant_result, JSONResponse):
        return grant_result
    authorization_code = grant_result

    if not _mark_authorization_code_used(hash_authorization_code(str(form.get("code") or ""))):
        return _oauth_error("invalid_grant", "Authorization code is expired or already used", 400)
    access_token = issue_mcp_access_token(
        tenant_id=authorization_code.tenant_id,
        principal_id=authorization_code.principal_id,
        client_id=authorization_code.client_id,
        issuer=get_mcp_oauth_issuer(),
        audience=get_mcp_oauth_audience(),
        scopes=authorization_code.scopes,
        now=datetime.now(UTC),
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


def _load_oauth_client(client_id: str) -> _OAuthClientAuthResult | None:
    def _lookup(session):
        oauth_client = OAuthClientRepository(session).find_active_by_client_id_across_tenants(client_id)
        if not oauth_client:
            return None
        return _OAuthClientAuthResult(
            tenant_id=oauth_client.tenant_id,
            principal_id=oauth_client.principal_id,
            client_id=oauth_client.client_id,
            client_secret_hash=oauth_client.client_secret_hash,
            scopes=list(oauth_client.scopes or []),
            redirect_uris=list(oauth_client.redirect_uris or []),
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
            decoded = base64.b64decode(authorization[6:].strip()).decode("utf-8")
            client_id, client_secret = decoded.split(":", 1)
            return client_id, client_secret
        except (ValueError, UnicodeDecodeError):
            return None, None

    client_id = form.get("client_id")
    client_secret = form.get("client_secret")
    return (str(client_id) if client_id else None, str(client_secret) if client_secret else None)


def _parse_requested_scopes(scope_text: str, allowed_scopes: list[str]) -> list[str] | None:
    if not scope_text:
        return allowed_scopes or [DEFAULT_MCP_OAUTH_SCOPE]
    requested = scope_text.split()
    allowed = set(allowed_scopes)
    if not set(requested).issubset(allowed):
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
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.extend(params.items())
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
