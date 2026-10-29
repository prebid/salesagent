"""The pure functions in ``src.core.oauth_service``.

What is left in that module after the JWT went is a set of pure functions — generate a
credential, hash one, compare one, and answer what this deployment's OAuth origin is — so
this file is a unit test by the rule in ``tests/unit/CLAUDE.md`` rather than by habit. The
behaviour of a MINTED token (which row it writes, which facts the verifier then reads off
it) is not here: that is a stored fact, graded in ``tests/unit/test_mcp_oauth_routes.py``
through the repository and on the wire.

The issue/validate tests this file used to carry — signature, audience, expiry,
missing-secret — were deleted with the machinery they graded. An access token is now an
opaque reference to a row, so there is no signature to forge, no ``aud`` claim to mismatch
and no secret to leave unset; every one of those four properties is now a column, and the
``exp`` one is decided in SQL by ``OAuthAccessTokenRepository.find_live``.
"""

import base64
import hashlib

import pytest
from pydantic import ValidationError

from src.core.config import RuntimeSettings, load_settings
from src.core.exceptions import AdCPConfigurationError
from src.core.oauth_service import (
    generate_access_token,
    generate_oauth_client_credentials,
    get_mcp_oauth_audience,
    get_mcp_oauth_issuer,
    hash_authorization_code,
    validate_oauth_redirect_uri,
    verify_client_secret,
    verify_pkce_s256,
)


@pytest.fixture
def deployment_env(monkeypatch):
    """Name this deployment the way a deployment does, through the settings loader.

    ``get_mcp_oauth_issuer`` reads ``get_settings()``, which caches, so setting an
    environment variable is only half of what a deployment does — ``load_settings()`` is
    the other half, and it is what each composition root calls at startup. The fixture
    restores the ambient settings afterwards so a cached object built from a test's
    environment does not leak into the next file on the worker.
    """

    def _configure(**env: str) -> None:
        for name in ("SALES_AGENT_DOMAIN", "MCP_OAUTH_ISSUER", "MCP_OAUTH_AUDIENCE"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        load_settings()

    yield _configure
    monkeypatch.undo()
    load_settings()


def test_generate_oauth_client_credentials_returns_one_time_secret_and_hash():
    credentials = generate_oauth_client_credentials()

    assert credentials.client_id.startswith("mcp_client_")
    assert credentials.client_secret.startswith("mcp_secret_")
    assert credentials.client_secret_hash != credentials.client_secret
    assert verify_client_secret(credentials.client_secret, credentials.client_secret_hash)
    assert not verify_client_secret("mcp_secret_wrong", credentials.client_secret_hash)


def test_generate_access_token_is_prefixed_and_carries_256_bits():
    """The shape the token endpoint returns, and the entropy that is its whole security.

    An opaque token asserts nothing, so being unguessable is the only property it has: the
    verifier's answer is a row keyed by ``sha256`` of this string, and anything that can
    produce the string IS the holder. 32 random bytes is what ``src.core.credentials``
    mints a buyer token with, and ``token_urlsafe`` encodes them base64url — so the body is
    at least 43 characters, and fewer would mean fewer bytes were asked for.
    """
    token = generate_access_token()

    assert token.startswith("oat_")
    body = token.removeprefix("oat_")
    assert len(body) >= 43, f"{len(body)} base64url characters is under 256 bits of entropy"
    assert body.replace("-", "").replace("_", "").isalnum()


def test_generate_access_token_never_repeats():
    """Two mints are two tokens.

    ``token_hash`` is the table's primary key, so a generator that could repeat would
    make one grant collide with another's row rather than merely be guessable. Sampling
    cannot prove 256-bit uniqueness; it does catch a generator wired to a constant or to a
    seeded module-level RNG, which is the way this breaks in practice.
    """
    assert len({generate_access_token() for _ in range(256)}) == 256


def test_mcp_oauth_public_urls_are_derived_from_the_deployment_domain(deployment_env):
    """The issuer and the audience come from the host this deployment answers on.

    ``SALES_AGENT_DOMAIN`` is where every other published origin comes from, so the OAuth
    layer derives from it too rather than carrying a second spelling. The loopback case
    also pins the scheme: ``get_protocol_for_domain`` answers ``http`` for a host no public
    CA will certify, and an issuer claiming ``https`` there would name a URL nothing can
    reach.
    """
    deployment_env(SALES_AGENT_DOMAIN="[::1]:8080")

    assert get_mcp_oauth_issuer() == "http://[::1]:8080"
    assert get_mcp_oauth_audience() == "http://[::1]:8080/mcp"


def test_explicit_issuer_wins_over_the_derived_one(deployment_env):
    """``MCP_OAUTH_ISSUER`` states the issuer outright and outranks the derivation.

    That precedence is what makes it an override at all — a value a derivation outranked
    could never override anything — and it is the order every other explicit-over-derived
    pair in this deployment keeps (``ADMIN_DOMAIN`` over ``admin.<domain>``,
    ``GOOGLE_OAUTH_REDIRECT_URI`` over the derived callback). The audience follows the
    issuer when only the issuer is stated, because ``/mcp`` is where this app mounts MCP.
    """
    deployment_env(SALES_AGENT_DOMAIN="served.example.com", MCP_OAUTH_ISSUER="https://public.example.com/")

    assert get_mcp_oauth_issuer() == "https://public.example.com"


def test_explicit_audience_wins_over_the_derived_one(deployment_env):
    deployment_env(SALES_AGENT_DOMAIN="served.example.com", MCP_OAUTH_AUDIENCE="https://public.example.com/mcp/")

    assert get_mcp_oauth_audience() == "https://public.example.com/mcp"


def test_issuer_without_a_domain_falls_back_to_the_stated_issuer(deployment_env):
    """A deployment that states only the issuer still has an audience: ``<issuer>/mcp``."""
    deployment_env(MCP_OAUTH_ISSUER="https://agent.example.com")

    assert get_mcp_oauth_issuer() == "https://agent.example.com"
    assert get_mcp_oauth_audience() == "https://agent.example.com/mcp"


def test_a_deployment_that_cannot_name_itself_refuses_to_be_an_issuer(deployment_env):
    """No domain and no stated issuer is a CONFIGURATION_ERROR, not a localhost guess.

    ``http://localhost:8000`` used to be returned here. The issuer is PUBLISHED — it is the
    ``authorization_servers`` entry in the discovery document and the origin every client
    holding a token compares against — so a guess is not a degraded answer but a wrong one,
    and it is wrong in the direction of silently claiming an identity. CONFIGURATION_ERROR
    is this repo's code for a seller-side fault only an operator can repoint, carrying
    ``terminal`` recovery because a buyer has no lever on it; it is the same refusal
    ``_addressed_tenant`` answers a request that names no tenant with.
    """
    deployment_env()

    with pytest.raises(AdCPConfigurationError) as refusal:
        get_mcp_oauth_issuer()

    assert refusal.value.error_code == "CONFIGURATION_ERROR"
    assert refusal.value.recovery == "terminal"
    # The knob names reach the operator: CODE_TABLE's sentence names none, so a refusal
    # that did not carry them would leave nothing to set.
    assert refusal.value.details is not None
    assert "SALES_AGENT_DOMAIN" in (refusal.value.details.tracked_by or "")
    assert "MCP_OAUTH_ISSUER" in (refusal.value.details.tracked_by or "")


def test_the_or_none_form_answers_without_refusing(deployment_env):
    """``get_mcp_oauth_issuer_or_none`` resolves identically and answers None instead.

    Its one caller is the ``WWW-Authenticate`` renderer, which adds the RFC 9728
    ``resource_metadata`` pointer to a refused credential. That surface needs the answer
    without needing it to exist: an unnameable deployment has no document to point at, and
    refusing there would replace a 401 about the CREDENTIAL with a configuration error
    about the deployment. Both forms are pinned together so the pair cannot drift —
    ``get_mcp_oauth_issuer`` must be the same resolution plus a refusal, not a second one.
    """
    from src.core.oauth_service import get_mcp_oauth_issuer_or_none

    deployment_env(MCP_OAUTH_ISSUER="https://agent.example.com/")
    assert get_mcp_oauth_issuer_or_none() == get_mcp_oauth_issuer() == "https://agent.example.com"

    deployment_env(SALES_AGENT_DOMAIN="served.example.com")
    assert get_mcp_oauth_issuer_or_none() == get_mcp_oauth_issuer() == "https://served.example.com"

    deployment_env()
    assert get_mcp_oauth_issuer_or_none() is None


def test_an_unnameable_deployment_refuses_an_audience_too(deployment_env):
    """One missing fact, one refusal site.

    The audience's last resort is ``<issuer>/mcp``, so asking for it on a deployment that
    cannot name itself reaches the issuer's refusal rather than returning a guess of its
    own. Without this, a token could be minted bound to ``http://localhost:8000/mcp``
    while discovery refused to say who issued it.
    """
    deployment_env()

    with pytest.raises(AdCPConfigurationError):
        get_mcp_oauth_audience()


def test_authorization_code_hash_is_stable_and_not_plaintext():
    code = "mcp_code_test"

    digest = hash_authorization_code(code)

    assert digest == hashlib.sha256(code.encode("utf-8")).hexdigest()
    assert digest != code


def test_verify_pkce_s256_accepts_matching_verifier_and_rejects_wrong_value():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")

    assert verify_pkce_s256(verifier, challenge) is True
    assert verify_pkce_s256("wrong-verifier", challenge) is False


@pytest.mark.parametrize("verifier", ["", "x", "a" * 129, "!" * 43, "\u00e9" * 43])
def test_pkce_rejects_verifiers_outside_rfc7636_syntax(verifier):
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("utf-8")).digest()).decode().rstrip("=")

    assert verify_pkce_s256(verifier, challenge) is False


@pytest.mark.parametrize("challenge", ["short", "!" * 43, "\u00e9" * 43, "a" * 129])
def test_pkce_rejects_malformed_challenges_without_raising(challenge):
    assert verify_pkce_s256("a" * 43, challenge) is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("mcp_oauth_issuer", "https://agent.example.com/prefix"),
        ("mcp_oauth_issuer", "https://agent.example.com?query=1"),
        ("mcp_oauth_audience", "https://agent.example.com/prefix/mcp"),
        ("mcp_oauth_audience", "https://agent.example.com/mcp#fragment"),
    ],
)
def test_oauth_settings_reject_unsupported_prefixes_and_url_components(field, value):
    with pytest.raises(ValidationError):
        RuntimeSettings(**{field: value})


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
