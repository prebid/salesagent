import base64
import hashlib
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import jwt
import pytest

from src.core.oauth_service import (
    OAuthTokenValidationError,
    generate_oauth_client_credentials,
    get_mcp_oauth_audience,
    get_mcp_oauth_issuer,
    hash_authorization_code,
    issue_mcp_access_token,
    validate_mcp_access_token,
    validate_oauth_redirect_uri,
    verify_client_secret,
    verify_pkce_s256,
)


def test_generate_oauth_client_credentials_returns_one_time_secret_and_hash():
    credentials = generate_oauth_client_credentials()

    assert credentials.client_id.startswith("mcp_client_")
    assert credentials.client_secret.startswith("mcp_secret_")
    assert credentials.client_secret_hash != credentials.client_secret
    assert verify_client_secret(credentials.client_secret, credentials.client_secret_hash)
    assert not verify_client_secret("mcp_secret_wrong", credentials.client_secret_hash)


def test_mcp_oauth_public_urls_use_http_for_loopback_domains():
    with patch.dict(os.environ, {"SALES_AGENT_DOMAIN": "[::1]:8080"}, clear=False):
        assert get_mcp_oauth_issuer() == "http://[::1]:8080"
        assert get_mcp_oauth_audience() == "http://[::1]:8080/mcp"


def test_issue_and_validate_mcp_access_token_returns_principal_claims():
    issued_at = datetime.now(UTC)
    token = issue_mcp_access_token(
        tenant_id="tenant_123",
        principal_id="principal_456",
        client_id="mcp_client_abc",
        issuer="https://agent.example.com",
        audience="https://agent.example.com/mcp",
        scopes=["mcp:principal"],
        now=issued_at,
        signing_key="test-signing-secret-with-at-least-32-bytes",
    )

    claims = validate_mcp_access_token(
        token,
        issuer="https://agent.example.com",
        audience="https://agent.example.com/mcp",
        now=issued_at + timedelta(minutes=1),
        signing_key="test-signing-secret-with-at-least-32-bytes",
    )

    assert claims.tenant_id == "tenant_123"
    assert claims.principal_id == "principal_456"
    assert claims.client_id == "mcp_client_abc"
    assert claims.scopes == ["mcp:principal"]
    assert claims.subject == "principal_456"


def test_validate_mcp_access_token_rejects_wrong_audience():
    token = issue_mcp_access_token(
        tenant_id="tenant_123",
        principal_id="principal_456",
        client_id="mcp_client_abc",
        issuer="https://agent.example.com",
        audience="https://agent.example.com/mcp",
        scopes=["mcp:principal"],
        now=datetime.now(UTC),
        signing_key="test-signing-secret-with-at-least-32-bytes",
    )

    with pytest.raises(OAuthTokenValidationError):
        validate_mcp_access_token(
            token,
            issuer="https://agent.example.com",
            audience="https://other.example.com/mcp",
            now=datetime.now(UTC),
            signing_key="test-signing-secret-with-at-least-32-bytes",
        )


def test_authorization_code_hash_is_stable_and_not_plaintext():
    code = "mcp_code_test"

    digest = hash_authorization_code(code)

    assert digest == hashlib.sha256(code.encode("utf-8")).hexdigest()
    assert digest != code


def test_verify_pkce_s256_accepts_matching_verifier_and_rejects_wrong_value():
    verifier = "verifier-for-remote-mcp-client"
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")

    assert verify_pkce_s256(verifier, challenge) is True
    assert verify_pkce_s256("wrong-verifier", challenge) is False


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "https://client.example.com/oauth/callback",
        "http://localhost:3456/callback",
        "http://127.0.0.1:3456/callback",
    ],
)
def test_validate_oauth_redirect_uri_accepts_https_and_loopback_http(redirect_uri):
    assert validate_oauth_redirect_uri(redirect_uri) is None


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "client.example.com/oauth/callback",
        "https://client.example.com/oauth/callback#fragment",
        "http://client.example.com/oauth/callback",
    ],
)
def test_validate_oauth_redirect_uri_rejects_unsafe_registrations(redirect_uri):
    assert validate_oauth_redirect_uri(redirect_uri) is not None


def test_validate_mcp_access_token_rejects_expired_token():
    token = issue_mcp_access_token(
        tenant_id="tenant_123",
        principal_id="principal_456",
        client_id="mcp_client_abc",
        issuer="https://agent.example.com",
        audience="https://agent.example.com/mcp",
        scopes=["mcp:principal"],
        now=datetime.now(UTC) - timedelta(minutes=2),
        lifetime_seconds=60,
        signing_key="test-signing-secret-with-at-least-32-bytes",
    )

    with pytest.raises(OAuthTokenValidationError):
        validate_mcp_access_token(
            token,
            issuer="https://agent.example.com",
            audience="https://agent.example.com/mcp",
            now=datetime.now(UTC),
            signing_key="test-signing-secret-with-at-least-32-bytes",
        )


def test_issue_mcp_access_token_requires_configured_signing_secret():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(RuntimeError, match="MCP_OAUTH_JWT_SECRET"):
            issue_mcp_access_token(
                tenant_id="tenant_123",
                principal_id="principal_456",
                client_id="mcp_client_abc",
                issuer="https://agent.example.com",
                audience="https://agent.example.com/mcp",
            )


def test_validate_mcp_access_token_uses_pyjwt_claim_validation():
    token = issue_mcp_access_token(
        tenant_id="tenant_123",
        principal_id="principal_456",
        client_id="mcp_client_abc",
        issuer="https://agent.example.com",
        audience="https://agent.example.com/mcp",
        scopes=["mcp:principal"],
        now=datetime.now(UTC),
        signing_key="test-signing-secret-with-at-least-32-bytes",
    )
    payload = jwt.decode(
        token,
        "test-signing-secret-with-at-least-32-bytes",
        algorithms=["HS256"],
        options={"verify_signature": False},
    )
    payload.pop("jti")
    token_without_jti = jwt.encode(payload, "test-signing-secret-with-at-least-32-bytes", algorithm="HS256")

    with pytest.raises(OAuthTokenValidationError):
        validate_mcp_access_token(
            token_without_jti,
            issuer="https://agent.example.com",
            audience="https://agent.example.com/mcp",
            now=datetime.now(UTC),
            signing_key="test-signing-secret-with-at-least-32-bytes",
        )
