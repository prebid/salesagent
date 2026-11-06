"""First-party OAuth helpers for MCP access: the pure functions, and nothing stateful.

Everything here is input-in/value-out — mint a credential, hash one, compare one, and
answer what this deployment's OAuth origin is. The STATE an OAuth exchange produces lives
elsewhere: an authorization code is a row
(``src.core.database.repositories.oauth_client``) and so is an access token
(``src.core.database.repositories.oauth_access_token``), and ``src/routes/oauth.py`` is
what writes both.

That split is what the JWT left behind. ``issue_mcp_access_token`` and
``validate_mcp_access_token`` lived here and were the module's only stateful pair: one
signed a set of claims about the holder, the other verified the signature and then read
every one of those claims off the ``oauth_clients`` and ``principals`` rows anyway to check
the client was still active and the principal still existed. So the claims were never the
authority, only a cache of it that could disagree — and could outlive a revocation until
``exp``. An access token is now an opaque reference (:func:`generate_access_token`) whose
row holds the facts, which leaves this module with no signing key, no claim vocabulary and
nothing to validate.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from urllib.parse import urlsplit

from src.core.config import get_settings
from src.core.domain_config import (
    get_mcp_server_url,
    get_protocol_for_domain,
    get_sales_agent_domain,
    get_sales_agent_url,
)
from src.core.errors.details import ConfigurationDetails
from src.core.exceptions import AdCPConfigurationError

DEFAULT_MCP_OAUTH_SCOPE = "mcp:principal"
DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS = 3600
DEFAULT_AUTHORIZATION_CODE_LIFETIME_SECONDS = 300
_SECRET_HASH_ITERATIONS = 390_000

#: Bytes of randomness behind an access token, matching ``src.core.credentials``: the token
#: is a reference to a stored row, so its only job is to be unguessable.
_ACCESS_TOKEN_ENTROPY_BYTES = 32


@dataclass(frozen=True)
class OAuthClientCredentials:
    """Generated OAuth client credentials.

    The plaintext client_secret is returned once to the admin UI. Only
    client_secret_hash should be persisted.
    """

    client_id: str
    client_secret: str
    client_secret_hash: str


def generate_access_token() -> str:
    """Generate an opaque first-party MCP OAuth access token.

    A reference, not an assertion: what makes the string usable is the
    ``oauth_access_tokens`` row the token endpoint mints beside it, keyed by
    ``src.core.credentials.hash_token`` of this value. So there is nothing to sign and
    nothing for a holder to read — the client, the principal, the scopes and the RFC 8707
    resource are columns the verifier reads from the authoritative row, which is also what
    makes revoking one exact rather than a wait for ``exp``.

    Returned to the client ONCE, in the token response. The plaintext is never logged and
    never stored, the same rule the buyer tokens in ``src.core.credentials`` keep.
    """
    return f"oat_{secrets.token_urlsafe(_ACCESS_TOKEN_ENTROPY_BYTES)}"


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
    if re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", code_verifier) is None or not valid_pkce_challenge(code_challenge):
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    actual = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return hmac.compare_digest(actual, code_challenge)


def valid_pkce_challenge(code_challenge: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9_-]{43}", code_challenge) is not None


def mcp_resource_for_host(virtual_host: str) -> str:
    return f"{get_protocol_for_domain(virtual_host)}://{virtual_host.lower()}/mcp"


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


def get_mcp_oauth_issuer_or_none() -> str | None:
    """The issuer URL for first-party MCP OAuth tokens, or ``None`` if there is none.

    THE one place the issuer is decided. Two sources, and no third. ``MCP_OAUTH_ISSUER``
    states the issuer outright and WINS, the same precedence every other
    explicit-over-derived pair in this deployment keeps (``google_oauth_redirect_uri`` over
    the derived callback in ``domain_config``, ``admin_domain`` over
    ``admin.<sales_agent_domain>``, ``publisher_auto_verify`` over the production
    derivation) — an override that a derivation outranks could never override anything.
    Otherwise it is DERIVED from ``SALES_AGENT_DOMAIN``, through the same
    ``get_sales_agent_url`` every other published origin comes from, so the issuer stays
    the origin a client can actually reach.

    ``None`` exists for the ONE caller that needs the answer without needing it to exist:
    ``AuthChallengeResponder`` attaches the RFC 9728 ``resource_metadata`` pointer to a
    ``WWW-Authenticate`` header, and a deployment that cannot name itself has no document
    to point at. Omitting that parameter leaves an RFC 7235-legal ``Bearer`` challenge, so
    the 401 still says "authenticate"; refusing there would answer a credential refusal
    with a configuration error instead, which says nothing about the credential and hides
    what actually happened. Every caller that PUBLISHES or BINDS the issuer wants
    :func:`get_mcp_oauth_issuer`.
    """
    if issuer := get_settings().runtime.mcp_oauth_issuer:
        return issuer.rstrip("/")
    if url := get_sales_agent_url(_get_public_protocol()):
        return url.rstrip("/")
    return None


def get_mcp_oauth_issuer() -> str:
    """The issuer URL for first-party MCP OAuth tokens, or a refusal.

    The form for a caller about to CLAIM the issuer: publish it in discovery metadata, or
    bind a minted token to the resource derived from it. Resolution is
    :func:`get_mcp_oauth_issuer_or_none`; this adds only what happens when there is none.

    Raises:
        AdCPConfigurationError: The deployment names neither source. There is deliberately
            no ``http://localhost:8000`` fallback: a deployment that cannot name itself must
            not claim to be an OAuth issuer, because the issuer is published in discovery
            metadata and compared by every client that holds a token — so a guess is not a
            degraded answer, it is a wrong one. CONFIGURATION_ERROR is this repo's one code
            for a seller-side deployment fault only an operator can repoint, which is the
            same refusal ``_addressed_tenant`` answers a request that names no tenant with.
    """
    if issuer := get_mcp_oauth_issuer_or_none():
        return issuer
    raise AdCPConfigurationError(
        details=ConfigurationDetails(
            capability="mcp_oauth",
            # The knob names live HERE: CODE_TABLE's sentence names none, so an operator
            # reading only the rendered message would have nothing to set (the shape
            # src/core/signing/keys.py uses for its missing-KEK refusal).
            tracked_by=(
                "This deployment cannot name itself, so it must not claim to be an OAuth issuer: "
                "set SALES_AGENT_DOMAIN (the issuer is derived from it, as every other published "
                "origin is) or MCP_OAUTH_ISSUER to state the issuer outright."
            ),
        ),
    )


def get_mcp_oauth_audience() -> str:
    """The canonical MCP resource audience for OAuth access tokens, or a refusal.

    The RFC 8707 resource identifier an access token is bound to, stored on the token's row
    and compared at lookup. Same precedence as the issuer, with one extra step: a
    deployment that states only the issuer gets ``<issuer>/mcp``, because that is where this
    app mounts MCP. A deployment that states neither reaches
    :func:`get_mcp_oauth_issuer`'s refusal — ONE refusal site for one missing fact.
    """
    if audience := get_settings().runtime.mcp_oauth_audience:
        return audience.rstrip("/")
    if url := get_mcp_server_url(_get_public_protocol()):
        return url.rstrip("/")
    return f"{get_mcp_oauth_issuer()}/mcp"


def _get_public_protocol() -> str:
    return get_protocol_for_domain(get_sales_agent_domain())
