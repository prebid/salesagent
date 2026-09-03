import base64
import hashlib
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.oauth_service import DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS, validate_mcp_access_token
from src.routes.oauth import _OAuthAuthorizationCodeResult, _OAuthClientAuthResult, router


def test_oauth_metadata_routes_advertise_authorization_code_and_client_credentials(monkeypatch):
    monkeypatch.setattr("src.routes.oauth.get_mcp_oauth_issuer", lambda: "https://agent.example.com")
    monkeypatch.setattr("src.routes.oauth.get_mcp_oauth_audience", lambda: "https://agent.example.com/mcp")

    client = _test_client()

    protected_resource = client.get("/.well-known/oauth-protected-resource")
    scoped_protected_resource = client.get("/.well-known/oauth-protected-resource/mcp")
    authorization_server = client.get("/.well-known/oauth-authorization-server")

    assert protected_resource.status_code == 200
    assert protected_resource.json()["resource"] == "https://agent.example.com/mcp"
    assert protected_resource.json()["authorization_servers"] == ["https://agent.example.com"]
    assert scoped_protected_resource.status_code == 200
    assert scoped_protected_resource.json() == protected_resource.json()
    assert authorization_server.status_code == 200
    assert authorization_server.json()["authorization_endpoint"] == "https://agent.example.com/authorize"
    assert authorization_server.json()["grant_types_supported"] == ["authorization_code", "client_credentials"]
    assert authorization_server.json()["token_endpoint_auth_methods_supported"] == [
        "client_secret_basic",
        "client_secret_post",
    ]
    assert authorization_server.json()["response_types_supported"] == ["code"]
    assert authorization_server.json()["code_challenge_methods_supported"] == ["S256"]
    assert "registration_endpoint" not in authorization_server.json()


def test_oauth_token_endpoint_issues_access_token_for_valid_client(monkeypatch):
    monkeypatch.setattr("src.routes.oauth.get_mcp_oauth_issuer", lambda: "https://agent.example.com")
    monkeypatch.setattr("src.routes.oauth.get_mcp_oauth_audience", lambda: "https://agent.example.com/mcp")
    monkeypatch.setattr(
        "src.routes.oauth._load_oauth_client",
        lambda client_id: _OAuthClientAuthResult(
            tenant_id="tenant_123",
            principal_id="principal_456",
            client_id=client_id,
            client_secret_hash="stored_hash",
            scopes=["mcp:principal"],
            redirect_uris=[],
        ),
    )
    monkeypatch.setattr("src.routes.oauth.verify_client_secret", lambda secret, stored_hash: secret == "secret")

    with patch.dict(os.environ, {"MCP_OAUTH_JWT_SECRET": "test-signing-secret-with-at-least-32-bytes"}):
        response = _test_client().post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "mcp_client_abc",
                "client_secret": "secret",
                "resource": "https://agent.example.com/mcp",
            },
        )

    assert response.status_code == 200
    claims = validate_mcp_access_token(
        response.json()["access_token"],
        issuer="https://agent.example.com",
        audience="https://agent.example.com/mcp",
        signing_key="test-signing-secret-with-at-least-32-bytes",
    )
    assert claims.tenant_id == "tenant_123"
    assert claims.principal_id == "principal_456"
    assert claims.client_id == "mcp_client_abc"
    assert claims.audience == "https://agent.example.com/mcp"
    assert response.json()["token_type"] == "Bearer"
    assert response.json()["expires_in"] == DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS
    assert response.json()["scope"] == "mcp:principal"
    assert response.headers["Cache-Control"] == "no-store"


def test_oauth_token_endpoint_rejects_wrong_resource(monkeypatch):
    monkeypatch.setattr("src.routes.oauth.get_mcp_oauth_audience", lambda: "https://agent.example.com/mcp")

    response = _test_client().post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "mcp_client_abc",
            "client_secret": "secret",
            "resource": "https://other.example.com/mcp",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_target"


def test_oauth_authorize_rejects_unregistered_redirect_uri(monkeypatch):
    monkeypatch.setattr("src.routes.oauth.get_mcp_oauth_audience", lambda: "https://agent.example.com/mcp")
    monkeypatch.setattr(
        "src.routes.oauth._load_oauth_client",
        lambda client_id: _OAuthClientAuthResult(
            tenant_id="tenant_123",
            principal_id="principal_456",
            client_id=client_id,
            client_secret_hash="stored_hash",
            scopes=["mcp:principal"],
            redirect_uris=["https://client.example.com/oauth/callback"],
        ),
    )

    response = _test_client().get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": "mcp_client_abc",
            "redirect_uri": "https://evil.example.com/callback",
            "code_challenge": "challenge",
            "code_challenge_method": "S256",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_oauth_authorization_code_pkce_flow_issues_access_token_and_rejects_reuse(monkeypatch):
    redirect_uri = "https://client.example.com/oauth/callback"
    verifier = "verifier-for-remote-mcp-client"
    challenge = _pkce_challenge(verifier)
    stored_codes = {}

    monkeypatch.setattr("src.routes.oauth.get_mcp_oauth_issuer", lambda: "https://agent.example.com")
    monkeypatch.setattr("src.routes.oauth.get_mcp_oauth_audience", lambda: "https://agent.example.com/mcp")
    monkeypatch.setattr("src.routes.oauth.generate_authorization_code", lambda: "mcp_code_test")
    monkeypatch.setattr("src.routes.oauth.issue_mcp_access_token", lambda **kwargs: "access-token")
    monkeypatch.setattr("src.routes.oauth.verify_client_secret", lambda secret, stored_hash: secret == "secret")
    monkeypatch.setattr(
        "src.routes.oauth._load_oauth_client",
        lambda client_id: _OAuthClientAuthResult(
            tenant_id="tenant_123",
            principal_id="principal_456",
            client_id=client_id,
            client_secret_hash="stored_hash",
            scopes=["mcp:principal"],
            redirect_uris=[redirect_uri],
        ),
    )

    def store_code(**kwargs):
        stored_codes[kwargs["code_hash"]] = kwargs

    def load_code(code):
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        stored = stored_codes.get(code_hash)
        if not stored:
            return None
        return _OAuthAuthorizationCodeResult(
            tenant_id=stored["tenant_id"],
            principal_id=stored["principal_id"],
            client_id=stored["client_id"],
            redirect_uri=stored["redirect_uri"],
            code_challenge=stored["code_challenge"],
            code_challenge_method=stored["code_challenge_method"],
            scopes=stored["scopes"],
            resource=stored["resource"],
            expires_at=stored["expires_at"],
            used_at=stored.get("used_at"),
        )

    def mark_used(code_hash):
        if stored_codes[code_hash].get("used_at") is not None:
            return False
        stored_codes[code_hash]["used_at"] = datetime.now(UTC)
        return True

    monkeypatch.setattr("src.routes.oauth._store_authorization_code", store_code)
    monkeypatch.setattr("src.routes.oauth._load_authorization_code", load_code)
    monkeypatch.setattr("src.routes.oauth._mark_authorization_code_used", mark_used)

    client = _test_client()
    authorize_response = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": "mcp_client_abc",
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "client-state",
            "resource": "https://agent.example.com/mcp",
        },
        follow_redirects=False,
    )

    assert authorize_response.status_code == 302
    callback = urlsplit(authorize_response.headers["location"])
    callback_params = parse_qs(callback.query)
    assert f"{callback.scheme}://{callback.netloc}{callback.path}" == redirect_uri
    assert callback_params["code"] == ["mcp_code_test"]
    assert callback_params["state"] == ["client-state"]

    token_response = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": "mcp_client_abc",
            "client_secret": "secret",
            "code": "mcp_code_test",
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "resource": "https://agent.example.com/mcp",
        },
    )

    assert token_response.status_code == 200
    assert token_response.json()["access_token"] == "access-token"
    assert token_response.json()["token_type"] == "Bearer"
    assert token_response.json()["scope"] == "mcp:principal"

    reuse_response = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": "mcp_client_abc",
            "client_secret": "secret",
            "code": "mcp_code_test",
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "resource": "https://agent.example.com/mcp",
        },
    )

    assert reuse_response.status_code == 400
    assert reuse_response.json()["error"] == "invalid_grant"


def test_oauth_authorization_code_token_requires_client_secret(monkeypatch):
    monkeypatch.setattr("src.routes.oauth.get_mcp_oauth_audience", lambda: "https://agent.example.com/mcp")
    monkeypatch.setattr(
        "src.routes.oauth._load_oauth_client",
        lambda client_id: _OAuthClientAuthResult(
            tenant_id="tenant_123",
            principal_id="principal_456",
            client_id=client_id,
            client_secret_hash="stored_hash",
            scopes=["mcp:principal"],
            redirect_uris=["https://client.example.com/oauth/callback"],
        ),
    )

    response = _test_client().post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": "mcp_client_abc",
            "code": "mcp_code_test",
            "redirect_uri": "https://client.example.com/oauth/callback",
            "code_verifier": "verifier",
            "resource": "https://agent.example.com/mcp",
        },
    )

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


def test_oauth_authorization_code_token_rejects_wrong_pkce_verifier(monkeypatch):
    monkeypatch.setattr("src.routes.oauth.get_mcp_oauth_audience", lambda: "https://agent.example.com/mcp")
    monkeypatch.setattr("src.routes.oauth.verify_client_secret", lambda secret, stored_hash: secret == "secret")
    monkeypatch.setattr(
        "src.routes.oauth._load_oauth_client",
        lambda client_id: _OAuthClientAuthResult(
            tenant_id="tenant_123",
            principal_id="principal_456",
            client_id=client_id,
            client_secret_hash="stored_hash",
            scopes=["mcp:principal"],
            redirect_uris=["https://client.example.com/oauth/callback"],
        ),
    )
    monkeypatch.setattr(
        "src.routes.oauth._load_authorization_code",
        lambda code: _OAuthAuthorizationCodeResult(
            tenant_id="tenant_123",
            principal_id="principal_456",
            client_id="mcp_client_abc",
            redirect_uri="https://client.example.com/oauth/callback",
            code_challenge=_pkce_challenge("expected-verifier"),
            code_challenge_method="S256",
            scopes=["mcp:principal"],
            resource="https://agent.example.com/mcp",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            used_at=None,
        ),
    )

    response = _test_client().post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": "mcp_client_abc",
            "client_secret": "secret",
            "code": "mcp_code_test",
            "redirect_uri": "https://client.example.com/oauth/callback",
            "code_verifier": "wrong-verifier",
            "resource": "https://agent.example.com/mcp",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _test_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)
