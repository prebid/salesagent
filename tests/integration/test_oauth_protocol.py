"""OAuth HTTP contracts outside the AdCP transport harness (RFC 6749 and RFC 7636)."""

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth_middleware import AuthChallengeResponder
from src.core.auth_utils import get_principal_from_token
from src.core.config import load_settings
from src.core.credentials import hash_token
from src.core.database.repositories.oauth_access_token import OAuthAccessTokenRepository
from src.core.database.repositories.oauth_client import OAuthClientRepository
from src.core.database.repositories.tenant import TenantRepository
from src.core.oauth_service import generate_access_token, generate_oauth_client_credentials
from src.routes.oauth import router
from tests.factories import PrincipalFactory, TenantFactory
from tests.harness._base import BareIntegrationEnv
from tests.helpers.credentials import credential_headers

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

ORIGIN = "https://oauth.example.com"
RESOURCE = f"{ORIGIN}/mcp"
CALLBACK = "https://client.example.com/callback"
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"


def _challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


@pytest.fixture
def oauth_seller(integration_db, monkeypatch):
    monkeypatch.setenv("MCP_OAUTH_ISSUER", ORIGIN)
    monkeypatch.setenv("MCP_OAUTH_AUDIENCE", RESOURCE)
    load_settings()
    with BareIntegrationEnv(tenant_id="oauth_protocol", principal_id="oauth_buyer") as env:
        tenant = TenantFactory(tenant_id=env.tenant_id, virtual_host="oauth.example.com")
        TenantFactory(tenant_id="oauth_other", virtual_host="other.example.com")
        PrincipalFactory(tenant=tenant, principal_id="oauth_buyer")
        credentials = generate_oauth_client_credentials()
        session = env.get_session()
        OAuthClientRepository(session).create_for_principal(
            tenant_id=env.tenant_id,
            principal_id="oauth_buyer",
            credentials=credentials,
            redirect_uris=[CALLBACK, CALLBACK + "?"],
        )
        session.commit()
        app = FastAPI()
        app.include_router(router)
        app.add_middleware(AuthChallengeResponder)
        with TestClient(app, base_url=ORIGIN) as client:
            yield client, credentials, env
    monkeypatch.undo()
    load_settings()


def _authorization(credentials, **overrides):
    return {
        "response_type": "code",
        "client_id": credentials.client_id,
        "redirect_uri": CALLBACK,
        "resource": RESOURCE,
        "code_challenge": _challenge(VERIFIER),
        "code_challenge_method": "S256",
        "state": "buyer-state",
        **overrides,
    }


def _credentials_grant(credentials, **overrides):
    return {
        "grant_type": "client_credentials",
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "resource": RESOURCE,
        **overrides,
    }


@pytest.mark.parametrize(
    "overrides,error",
    [
        ({"scope": "unknown"}, "invalid_scope"),
        ({"code_challenge": ""}, "invalid_request"),
        ({"code_challenge": "short"}, "invalid_request"),
        ({"response_type": "token"}, "unsupported_response_type"),
    ],
)
def test_authorization_errors_return_to_registered_callback(oauth_seller, overrides, error):
    client, credentials, _env = oauth_seller
    response = client.get("/authorize", params=_authorization(credentials, **overrides), follow_redirects=False)

    assert response.status_code == 302
    callback = urlsplit(response.headers["location"])
    assert f"{callback.scheme}://{callback.netloc}{callback.path}" == CALLBACK
    assert parse_qs(callback.query)["error"] == [error]
    assert parse_qs(callback.query)["state"] == ["buyer-state"]


def test_callback_with_empty_query_preserves_code_parameter(oauth_seller):
    client, credentials, _env = oauth_seller
    response = client.get(
        "/authorize", params=_authorization(credentials, redirect_uri=CALLBACK + "?"), follow_redirects=False
    )

    assert response.status_code == 302
    assert parse_qs(urlsplit(response.headers["location"]).query)["code"]


@pytest.mark.parametrize("parameter", ["grant_type", "client_id", "scope"])
def test_token_request_rejects_duplicate_parameters(oauth_seller, parameter):
    client, credentials, _env = oauth_seller
    fields = _credentials_grant(credentials, scope="mcp:principal")
    response = client.post(
        "/oauth/token",
        content=urlencode([(parameter, fields[parameter]), *fields.items()]),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_token_request_rejects_two_client_authentication_methods(oauth_seller):
    client, credentials, _env = oauth_seller
    basic = base64.b64encode(f"{credentials.client_id}:{credentials.client_secret}".encode()).decode()
    response = client.post(
        "/oauth/token", data=_credentials_grant(credentials), headers=credential_headers(token=basic, scheme="Basic")
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


@pytest.mark.parametrize("scope", ["   ", "mcp:principal\t", "mcp:principal\n", "unknown"])
def test_token_request_rejects_malformed_or_unknown_scope(oauth_seller, scope):
    client, credentials, _env = oauth_seller
    response = client.post("/oauth/token", data=_credentials_grant(credentials, scope=scope))

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_scope"


@pytest.mark.parametrize("verifier", [VERIFIER, "", "x", "!" * 43, "a" * 129])
def test_real_authorization_code_exchange_enforces_pkce(oauth_seller, verifier):
    client, credentials, _env = oauth_seller
    response = client.get(
        "/authorize", params=_authorization(credentials, code_challenge=_challenge(verifier)), follow_redirects=False
    )
    code = parse_qs(urlsplit(response.headers["location"]).query)["code"][0]
    response = client.post(
        "/oauth/token",
        data=_credentials_grant(
            credentials, grant_type="authorization_code", code=code, redirect_uri=CALLBACK, code_verifier=verifier
        ),
    )

    assert response.status_code == (200 if verifier == VERIFIER else 400)
    if verifier == VERIFIER:
        assert response.json()["scope"] == "mcp:principal"
        assert response.headers["cache-control"] == "no-store"
    else:
        assert response.json()["error"] == "invalid_grant"


@pytest.mark.parametrize("host", ["oauth.example.com", "other.example.com"])
def test_resource_discovery_uses_the_registered_tenant_host(oauth_seller, host):
    client, _credentials, _env = oauth_seller
    response = client.get("/.well-known/oauth-protected-resource/mcp", headers={"Host": host})

    assert response.status_code == 200
    assert response.json()["resource"] == f"https://{host}/mcp"
    assert response.json()["authorization_servers"] == [ORIGIN]


def test_discovery_does_not_publish_an_unregistered_host(oauth_seller):
    client, _credentials, _env = oauth_seller
    response = client.get("/.well-known/oauth-protected-resource", headers={"Host": "unknown.example.com"})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_target"


@pytest.mark.parametrize("resource,scopes", [(RESOURCE, []), ("https://other.example.com/mcp", ["mcp:principal"])])
def test_token_lookup_enforces_resource_and_scope(oauth_seller, resource, scopes):
    _client, credentials, env = oauth_seller
    token = generate_access_token()
    OAuthAccessTokenRepository(env.get_session(), env.tenant_id).mint(
        token_hash=hash_token(token),
        client_id=credentials.client_id,
        principal_id="oauth_buyer",
        scopes=scopes,
        resource=resource,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    env.get_session().commit()

    assert get_principal_from_token(token, env.tenant_id) is None


def test_tenant_token_authenticates_only_its_own_tenant(oauth_seller):
    client, credentials, env = oauth_seller
    response = client.post("/oauth/token", data=_credentials_grant(credentials))

    assert response.status_code == 200
    token = response.json()["access_token"]
    assert get_principal_from_token(token, env.tenant_id).principal_id == "oauth_buyer"
    assert get_principal_from_token(token, "oauth_other") is None


def test_invalid_basic_credentials_keep_the_oauth_challenge(oauth_seller):
    client, credentials, _env = oauth_seller
    basic = base64.b64encode(f"{credentials.client_id}:wrong".encode()).decode()
    response = client.post(
        "/oauth/token",
        data={"grant_type": "client_credentials", "resource": RESOURCE},
        headers=credential_headers(token=basic, scheme="Basic"),
    )

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"
    assert response.headers["www-authenticate"] == 'Basic realm="mcp-oauth"'


def test_metadata_advertises_the_supported_oauth_contract(oauth_seller):
    client, _credentials, _env = oauth_seller
    protected_resource = client.get("/.well-known/oauth-protected-resource")
    scoped_resource = client.get("/.well-known/oauth-protected-resource/mcp")
    authorization_server = client.get("/.well-known/oauth-authorization-server")

    assert protected_resource.status_code == scoped_resource.status_code == authorization_server.status_code == 200
    assert protected_resource.json()["resource"] == RESOURCE
    assert protected_resource.json()["authorization_servers"] == [ORIGIN]
    assert scoped_resource.json() == protected_resource.json()
    metadata = authorization_server.json()
    assert metadata["issuer"] == ORIGIN
    assert metadata["authorization_endpoint"] == f"{ORIGIN}/authorize"
    assert metadata["token_endpoint"] == f"{ORIGIN}/oauth/token"
    assert metadata["grant_types_supported"] == ["authorization_code", "client_credentials"]
    assert metadata["token_endpoint_auth_methods_supported"] == ["client_secret_basic", "client_secret_post"]
    assert metadata["response_types_supported"] == ["code"]
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    assert "registration_endpoint" not in metadata


@pytest.fixture
def other_oauth_credentials(oauth_seller):
    _client, _credentials, env = oauth_seller
    tenant = TenantRepository(env.get_session()).get_active("oauth_other")
    PrincipalFactory(tenant=tenant, principal_id="other_buyer")
    credentials = generate_oauth_client_credentials()
    OAuthClientRepository(env.get_session()).create_for_principal(
        tenant_id="oauth_other",
        principal_id="other_buyer",
        credentials=credentials,
        redirect_uris=[CALLBACK],
    )
    env.get_session().commit()
    return credentials


@pytest.mark.parametrize("grant_type", ["client_credentials", "authorization_code"])
def test_shared_issuer_mints_only_for_the_clients_tenant(oauth_seller, other_oauth_credentials, grant_type):
    client, _credentials, _env = oauth_seller
    resource = "https://other.example.com/mcp"
    form = _credentials_grant(other_oauth_credentials, grant_type=grant_type, resource=resource)
    if grant_type == "authorization_code":
        authorization = client.get(
            "/authorize", params=_authorization(other_oauth_credentials, resource=resource), follow_redirects=False
        )
        assert authorization.status_code == 302
        form.update(
            code=parse_qs(urlsplit(authorization.headers["location"]).query)["code"][0],
            redirect_uri=CALLBACK,
            code_verifier=VERIFIER,
        )
    refused = client.post("/oauth/token", data={**form, "resource": RESOURCE})
    assert refused.status_code == 400
    assert refused.json()["error"] == "invalid_target"

    response = client.post("/oauth/token", data=form)
    assert response.status_code == 200
    token = response.json()["access_token"]
    assert get_principal_from_token(token, "oauth_other").principal_id == "other_buyer"
    assert get_principal_from_token(token, "oauth_protocol") is None


def test_token_request_rejects_multiple_resources(oauth_seller):
    client, credentials, _env = oauth_seller
    response = client.post(
        "/oauth/token",
        content=urlencode([("resource", "https://other.example.com/mcp"), *_credentials_grant(credentials).items()]),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_basic_credentials_decode_oauth_form_encoding(oauth_seller):
    client, credentials, _env = oauth_seller
    encoded_id = "%6D" + credentials.client_id[1:]
    basic = base64.b64encode(f"{encoded_id}:{credentials.client_secret}".encode()).decode()
    response = client.post(
        "/oauth/token",
        data={"grant_type": "client_credentials", "resource": RESOURCE},
        headers=credential_headers(token=basic, scheme="Basic"),
    )

    assert response.status_code == 200
    assert response.json()["token_type"] == "Bearer"


@pytest.mark.parametrize("parameter", ["client_id", "redirect_uri"])
def test_ambiguous_authorization_identity_never_redirects(oauth_seller, parameter):
    client, credentials, _env = oauth_seller
    fields = _authorization(credentials)
    response = client.get(
        "/authorize?" + urlencode([(parameter, fields[parameter]), *fields.items()]), follow_redirects=False
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert "location" not in response.headers
