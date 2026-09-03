"""First-party OAuth helpers for MCP client-credentials access."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import jwt

from src.core.domain_config import (
    get_mcp_server_url,
    get_protocol_for_domain,
    get_sales_agent_domain,
    get_sales_agent_url,
)

DEFAULT_MCP_OAUTH_SCOPE = "mcp:principal"
DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS = 3600
DEFAULT_AUTHORIZATION_CODE_LIFETIME_SECONDS = 300
_SECRET_HASH_ITERATIONS = 390_000
_JWT_ALGORITHM = "HS256"
_JWT_LEEWAY_SECONDS = 30
_REQUIRED_ACCESS_TOKEN_CLAIMS = [
    "iss",
    "aud",
    "sub",
    "tenant_id",
    "principal_id",
    "client_id",
    "iat",
    "exp",
    "jti",
]


class OAuthTokenValidationError(ValueError):
    """Raised when a first-party MCP OAuth access token is invalid."""


@dataclass(frozen=True)
class OAuthClientCredentials:
    """Generated OAuth client credentials.

    The plaintext client_secret is returned once to the admin UI. Only
    client_secret_hash should be persisted.
    """

    client_id: str
    client_secret: str
    client_secret_hash: str


@dataclass(frozen=True)
class MCPAccessTokenClaims:
    """Validated claims from a first-party MCP OAuth access token."""

    tenant_id: str
    principal_id: str
    client_id: str
    scopes: list[str]
    subject: str
    issuer: str
    audience: str
    expires_at: datetime
    issued_at: datetime
    jwt_id: str


def generate_oauth_client_credentials() -> OAuthClientCredentials:
    """Generate an OAuth client ID and one-time client secret."""
    client_id = f"mcp_client_{secrets.token_urlsafe(24)}"
    client_secret = f"mcp_secret_{secrets.token_urlsafe(48)}"
    return OAuthClientCredentials(
        client_id=client_id,
        client_secret=client_secret,
        client_secret_hash=hash_client_secret(client_secret),
    )


def generate_authorization_code() -> str:
    """Generate a one-time OAuth authorization code."""
    return f"mcp_code_{secrets.token_urlsafe(48)}"


def hash_authorization_code(code: str) -> str:
    """Hash an OAuth authorization code for lookup without storing plaintext."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def verify_pkce_s256(code_verifier: str, code_challenge: str) -> bool:
    """Verify an OAuth S256 PKCE code verifier against the stored challenge."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    actual = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return hmac.compare_digest(actual, code_challenge)


def validate_oauth_redirect_uri(redirect_uri: str) -> str | None:
    """Validate a registered OAuth redirect URI.

    Registered redirect URIs must be absolute, fragment-free HTTPS URLs.
    Loopback HTTP is allowed for local MCP clients using Authorization Code + PKCE.
    """
    parsed = urlsplit(redirect_uri)
    if not parsed.scheme or not parsed.netloc:
        return f"OAuth redirect URI must be absolute: {redirect_uri}"
    if parsed.fragment:
        return f"OAuth redirect URI must not include a fragment: {redirect_uri}"
    hostname = parsed.hostname or ""
    if parsed.scheme == "https":
        return None
    if parsed.scheme == "http" and hostname in {"localhost", "127.0.0.1"}:
        return None
    return f"OAuth redirect URI must use HTTPS unless it is localhost: {redirect_uri}"


def hash_client_secret(client_secret: str) -> str:
    """Hash an OAuth client secret for database storage."""
    salt = secrets.token_bytes(32)
    derived_key = hashlib.pbkdf2_hmac(
        "sha256",
        client_secret.encode("utf-8"),
        salt,
        _SECRET_HASH_ITERATIONS,
    )
    return "$".join(
        (
            "pbkdf2_sha256",
            str(_SECRET_HASH_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(derived_key).decode("ascii"),
        )
    )


def verify_client_secret(client_secret: str, client_secret_hash: str) -> bool:
    """Verify an OAuth client secret against a stored hash."""
    try:
        algorithm, iterations_text, salt_text, expected_text = client_secret_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False

        iterations = int(iterations_text)
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(expected_text.encode("ascii"))
    except (ValueError, TypeError):
        return False

    actual = hashlib.pbkdf2_hmac(
        "sha256",
        client_secret.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual, expected)


def issue_mcp_access_token(
    *,
    tenant_id: str,
    principal_id: str,
    client_id: str,
    issuer: str,
    audience: str,
    scopes: list[str] | None = None,
    now: datetime | None = None,
    lifetime_seconds: int = DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS,
    signing_key: str | None = None,
) -> str:
    """Issue a short-lived first-party access token for the MCP resource."""
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(seconds=lifetime_seconds)
    granted_scopes = scopes or [DEFAULT_MCP_OAUTH_SCOPE]

    payload = {
        "iss": issuer,
        "aud": audience,
        "sub": principal_id,
        "tenant_id": tenant_id,
        "principal_id": principal_id,
        "client_id": client_id,
        "scope": " ".join(granted_scopes),
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, signing_key or _get_signing_key(), algorithm=_JWT_ALGORITHM)


def validate_mcp_access_token(
    token: str,
    *,
    issuer: str,
    audience: str,
    now: datetime | None = None,
    signing_key: str | None = None,
) -> MCPAccessTokenClaims:
    """Validate a first-party MCP access token and return typed claims."""
    try:
        payload = jwt.decode(
            token,
            signing_key or _get_signing_key(),
            algorithms=[_JWT_ALGORITHM],
            audience=audience,
            issuer=issuer,
            leeway=_JWT_LEEWAY_SECONDS,
            options={"require": _REQUIRED_ACCESS_TOKEN_CLAIMS},
        )
    except jwt.PyJWTError as exc:
        raise OAuthTokenValidationError("Invalid OAuth access token") from exc

    _validate_oauth_claims(payload)

    return MCPAccessTokenClaims(
        tenant_id=str(payload["tenant_id"]),
        principal_id=str(payload["principal_id"]),
        client_id=str(payload["client_id"]),
        scopes=str(payload.get("scope", "")).split(),
        subject=str(payload["sub"]),
        issuer=str(payload["iss"]),
        audience=str(payload["aud"]),
        expires_at=datetime.fromtimestamp(int(payload["exp"]), UTC),
        issued_at=datetime.fromtimestamp(int(payload["iat"]), UTC),
        jwt_id=str(payload["jti"]),
    )


def get_mcp_oauth_issuer() -> str:
    """Return the issuer URL for first-party MCP OAuth tokens."""
    if issuer := os.environ.get("MCP_OAUTH_ISSUER"):
        return issuer.rstrip("/")
    if url := get_sales_agent_url(_get_public_protocol()):
        return url.rstrip("/")
    return "http://localhost:8000"


def get_mcp_oauth_audience() -> str:
    """Return the canonical MCP resource audience for OAuth access tokens."""
    if audience := os.environ.get("MCP_OAUTH_AUDIENCE"):
        return audience.rstrip("/")
    if url := get_mcp_server_url(_get_public_protocol()):
        return url.rstrip("/")
    return f"{get_mcp_oauth_issuer()}/mcp"


def _validate_oauth_claims(payload: dict[str, Any]) -> None:
    if not all(str(payload.get(claim) or "") for claim in ("tenant_id", "principal_id", "client_id", "jti")):
        raise OAuthTokenValidationError("OAuth access token is missing required principal claims")


def _get_signing_key() -> str:
    signing_key = os.environ.get("MCP_OAUTH_JWT_SECRET")
    if signing_key:
        return signing_key
    raise RuntimeError("MCP_OAUTH_JWT_SECRET must be set before issuing or validating MCP OAuth tokens")


def _get_public_protocol() -> str:
    return get_protocol_for_domain(get_sales_agent_domain())
