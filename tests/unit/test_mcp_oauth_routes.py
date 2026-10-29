"""The OAuth metadata, authorize and token endpoints, over a real ASGI client.

The token endpoint is graded on what it PRODUCES, not on a substituted mint. A successful
grant is checked three ways, and the first is the one that replaced the JWT assertions:

1. the row. ``hash_token`` of the string in the response is the primary key of the
   ``oauth_access_tokens`` row the real ``OAuthAccessTokenRepository`` built, and that row
   carries the grant's client, principal, scopes and RFC 8707 resource — the five facts the
   deleted JWT asserted in signed claims and the verifier now reads off a column.
2. the response. RFC 6749 §5.1's required members, with ``Cache-Control: no-store``.
3. the plaintext's blast radius. It appears in the response body and nowhere else.

ONE seam is substituted, and it is not the mint: ``execute_with_retry``, which is where a
session comes from. The unit suite runs with ``DATABASE_URL`` unset by construction
(``tox.ini``), so there is no session to open; the recording double below stands in for the
session alone, and ``OAuthAccessTokenRepository.mint`` — the production code that decides
which columns a minted token carries — runs for real against it. What cannot run here is
``find_live``, because it is SQL: this file therefore asserts the row satisfies each of the
three conditions that query decides in the database (right tenant, unexpired, unrevoked)
and leaves the query itself to be graded where there is a PostgreSQL.

``MCP_OAUTH_ISSUER`` is set through the settings loader rather than by patching
``get_mcp_oauth_issuer``, so the issuer and audience these routes publish and bind tokens
to are the ones the real resolution answers.
"""

import base64
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.config import load_settings
from src.core.credentials import hash_token
from src.core.database.models import OAuthAccessToken
from src.core.oauth_service import DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS
from src.routes.oauth import _OAuthAuthorizationCodeResult, _OAuthClientAuthResult, router

ISSUER = "https://agent.example.com"
AUDIENCE = f"{ISSUER}/mcp"
TENANT_ID = "tenant_123"
PRINCIPAL_ID = "principal_456"
CLIENT_ID = "mcp_client_abc"


@pytest.fixture(autouse=True)
def _deployment_issuer(monkeypatch):
    """This deployment's OAuth issuer, set the way a deployment sets it.

    ``get_settings()`` caches, so naming the variable is only half of it; ``load_settings()``
    is the half each composition root performs at startup. Restored afterwards so a cached
    object built here does not leak into the next file on the worker.
    """
    monkeypatch.setenv("MCP_OAUTH_ISSUER", ISSUER)
    monkeypatch.delenv("MCP_OAUTH_AUDIENCE", raising=False)
    monkeypatch.delenv("SALES_AGENT_DOMAIN", raising=False)
    load_settings()
    yield
    monkeypatch.undo()
    load_settings()


class _RecordingSession:
    """A stand-in for the SQLAlchemy session, and for nothing else.

    ``add`` is the only method the mint path reaches. Nothing here reimplements a query:
    the point is that the REAL repository decides what the row holds, and this only holds
    what it added.
    """

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, instance: object) -> None:
        self.added.append(instance)


@pytest.fixture
def minted_rows(monkeypatch) -> list[OAuthAccessToken]:
    """The ``oauth_access_tokens`` rows this request minted, in order."""
    rows: list[OAuthAccessToken] = []

    def _execute_with_retry(func, max_retries: int = 3):
        session = _RecordingSession()
        result = func(session)
        rows.extend(row for row in session.added if isinstance(row, OAuthAccessToken))
        return result

    monkeypatch.setattr("src.routes.oauth.execute_with_retry", _execute_with_retry)
    return rows


def _registered_client(redirect_uris: list[str] | None = None):
    def _load(client_id: str) -> _OAuthClientAuthResult:
        return _OAuthClientAuthResult(
            tenant_id=TENANT_ID,
            principal_id=PRINCIPAL_ID,
            client_id=client_id,
            client_secret_hash="stored_hash",
            scopes=["mcp:principal"],
            redirect_uris=redirect_uris or [],
            resource=AUDIENCE,
        )

    return _load


def _authenticating_client(monkeypatch, redirect_uris: list[str] | None = None) -> None:
    """A registered OAuth client whose secret is ``secret``.

    The client row and the PBKDF2 comparison are not this file's subject — the row is a
    database read and ``verify_client_secret`` is graded as a pure function in
    ``test_mcp_oauth_service.py`` — so both are substituted, leaving the grant itself.
    """
    monkeypatch.setattr("src.routes.oauth._load_oauth_client", _registered_client(redirect_uris))
    monkeypatch.setattr("src.routes.oauth.verify_client_secret", lambda secret, stored_hash: secret == "secret")


def test_client_credentials_grant_stores_the_token_it_returns(monkeypatch, minted_rows, caplog):
    """The token in the response is the hash of a row holding the grant's facts.

    This is what replaced "decode the JWT and read its claims". Nothing about the returned
    string says who holds it, so the assertion is that the ROW the verifier will read does:
    the tenant the client belongs to, the client itself, its principal, the scopes granted
    and the resource the token is bound to. The hash is the link between the two, and it is
    computed here from the response body with the same function production stored it under.
    """
    _authenticating_client(monkeypatch)

    with caplog.at_level(logging.DEBUG):
        response = _test_client().post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": CLIENT_ID,
                "client_secret": "secret",
                "resource": AUDIENCE,
            },
        )

    assert response.status_code == 200
    access_token = response.json()["access_token"]
    assert access_token.startswith("oat_")

    assert len(minted_rows) == 1, f"one grant must mint exactly one row, got {len(minted_rows)}"
    row = minted_rows[0]
    assert row.token_hash == hash_token(access_token)
    assert row.token_hash != access_token
    assert row.tenant_id == TENANT_ID
    assert row.client_id == CLIENT_ID
    assert row.principal_id == PRINCIPAL_ID
    assert row.scopes == ["mcp:principal"]
    assert row.resource == AUDIENCE

    # The three conditions find_live decides in SQL, asserted on the row that will answer
    # it: the token is usable now and nothing has withdrawn it.
    assert row.expires_at > datetime.now(UTC)
    assert row.expires_at <= datetime.now(UTC) + timedelta(seconds=DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS)
    assert row.revoked_at is None

    # The plaintext is returned once and goes nowhere else. It is not a column -- the row
    # above holds its hash -- and it must not reach a log either.
    assert access_token not in caplog.text


def test_client_credentials_token_response_is_rfc_6749_conformant(monkeypatch, minted_rows):
    """RFC 6749 §5.1: ``access_token`` and ``token_type`` are REQUIRED, and the response is
    not cacheable. ``expires_in`` and ``scope`` are what this seller additionally states.

    Graded apart from the row because they are a different contract with a different
    reader: an MCP host parses this body, and it does so whether or not the row behind the
    token is right.
    """
    _authenticating_client(monkeypatch)

    response = _test_client().post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": "secret",
            "resource": AUDIENCE,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_in", "scope"}
    assert hash_token(body["access_token"]) == minted_rows[0].token_hash
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS
    assert body["scope"] == "mcp:principal"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"


def test_oauth_token_endpoint_rejects_wrong_resource(monkeypatch):
    _authenticating_client(monkeypatch)
    response = _test_client().post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": "secret",
            "resource": "https://other.example.com/mcp",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_target"


def test_oauth_token_endpoint_rejects_unsupported_grant_type():
    response = _test_client().post("/oauth/token", data={"grant_type": "password"})

    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_grant_type"


def test_oauth_authorize_rejects_unregistered_redirect_uri(monkeypatch):
    monkeypatch.setattr(
        "src.routes.oauth._load_oauth_client",
        _registered_client(["https://client.example.com/oauth/callback"]),
    )

    response = _test_client().get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": "https://evil.example.com/callback",
            "code_challenge": "challenge",
            "code_challenge_method": "S256",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


@pytest.mark.parametrize(
    ("registered_redirect_uri", "expected_separator"),
    [
        ("https://client.example.com/oauth/callback", "?"),
        ("https://client.example.com/oauth/callback?tenant=acme", "&"),
    ],
)
def test_authorize_redirect_preserves_the_registered_uri_byte_for_byte(
    monkeypatch, registered_redirect_uri, expected_separator
):
    """The redirect carries the registered URI unchanged, with the code appended to it.

    ``_append_query_params`` appends rather than decomposing and reassembling the URI, so a
    URI already carrying a query keeps it and keeps its order, and nothing about the
    original bytes is re-encoded on the way out. Both separator cases are here because the
    ``?``/``&`` choice is the only decision appending makes.
    """
    monkeypatch.setattr("src.routes.oauth._load_oauth_client", _registered_client([registered_redirect_uri]))
    monkeypatch.setattr("src.routes.oauth._store_authorization_code", lambda **kwargs: None)
    monkeypatch.setattr("src.routes.oauth.generate_authorization_code", lambda: "mcp_code_test")

    response = _test_client().get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": registered_redirect_uri,
            "code_challenge": _pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
            "code_challenge_method": "S256",
            "state": "client-state",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(f"{registered_redirect_uri}{expected_separator}")
    params = parse_qs(urlsplit(location).query)
    assert params["code"] == ["mcp_code_test"]
    assert params["state"] == ["client-state"]


def test_oauth_authorization_code_pkce_flow_stores_the_token_and_rejects_reuse(monkeypatch, minted_rows):
    """The authorization-code grant mints the same kind of row, from the CODE's facts.

    The two grants read their facts from different places — this one from the stored
    authorization code, including the resource the authorization request bound — so the row
    is asserted again rather than assumed from the client-credentials case. The replay at
    the end is what proves the code was consumed: a second exchange mints nothing.
    """
    redirect_uri = "https://client.example.com/oauth/callback"
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    challenge = _pkce_challenge(verifier)
    stored_codes: dict[str, dict] = {}

    _authenticating_client(monkeypatch, [redirect_uri])
    monkeypatch.setattr("src.routes.oauth.generate_authorization_code", lambda: "mcp_code_test")

    def store_code(**kwargs):
        stored_codes[kwargs["code_hash"]] = kwargs

    def load_code(code):
        stored = stored_codes.get(hashlib.sha256(code.encode("utf-8")).hexdigest())
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
            "client_id": CLIENT_ID,
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "client-state",
            "resource": AUDIENCE,
        },
        follow_redirects=False,
    )

    assert authorize_response.status_code == 302
    callback = urlsplit(authorize_response.headers["location"])
    callback_params = parse_qs(callback.query)
    assert f"{callback.scheme}://{callback.netloc}{callback.path}" == redirect_uri
    assert callback_params["code"] == ["mcp_code_test"]
    assert callback_params["state"] == ["client-state"]

    token_response = client.post("/oauth/token", data=_code_exchange(redirect_uri, verifier))

    assert token_response.status_code == 200
    access_token = token_response.json()["access_token"]
    assert access_token.startswith("oat_")
    assert token_response.json()["token_type"] == "Bearer"
    assert token_response.json()["scope"] == "mcp:principal"

    assert len(minted_rows) == 1
    row = minted_rows[0]
    assert row.token_hash == hash_token(access_token)
    assert row.tenant_id == TENANT_ID
    assert row.client_id == CLIENT_ID
    assert row.principal_id == PRINCIPAL_ID
    assert row.scopes == ["mcp:principal"]
    assert row.resource == AUDIENCE
    assert row.revoked_at is None
    assert row.expires_at > datetime.now(UTC)

    reuse_response = client.post("/oauth/token", data=_code_exchange(redirect_uri, verifier))

    assert reuse_response.status_code == 400
    assert reuse_response.json()["error"] == "invalid_grant"
    assert len(minted_rows) == 1, "a replayed authorization code must mint no second token"


def test_oauth_authorization_code_token_requires_client_secret(monkeypatch, minted_rows):
    monkeypatch.setattr(
        "src.routes.oauth._load_oauth_client",
        _registered_client(["https://client.example.com/oauth/callback"]),
    )

    response = _test_client().post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": "mcp_code_test",
            "redirect_uri": "https://client.example.com/oauth/callback",
            "code_verifier": "verifier",
            "resource": AUDIENCE,
        },
    )

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"
    assert minted_rows == []


def test_oauth_authorization_code_token_rejects_wrong_pkce_verifier(monkeypatch, minted_rows):
    redirect_uri = "https://client.example.com/oauth/callback"
    _authenticating_client(monkeypatch, [redirect_uri])
    monkeypatch.setattr(
        "src.routes.oauth._load_authorization_code",
        lambda code: _OAuthAuthorizationCodeResult(
            tenant_id=TENANT_ID,
            principal_id=PRINCIPAL_ID,
            client_id=CLIENT_ID,
            redirect_uri=redirect_uri,
            code_challenge=_pkce_challenge("expected-verifier"),
            code_challenge_method="S256",
            scopes=["mcp:principal"],
            resource=AUDIENCE,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            used_at=None,
        ),
    )

    response = _test_client().post("/oauth/token", data=_code_exchange(redirect_uri, "wrong-verifier"))

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"
    assert minted_rows == [], "a failed PKCE check must mint no token"


def _code_exchange(redirect_uri: str, code_verifier: str) -> dict[str, str]:
    return {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": "secret",
        "code": "mcp_code_test",
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
        "resource": AUDIENCE,
    }


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _test_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)
