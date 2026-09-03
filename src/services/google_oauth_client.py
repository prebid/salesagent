"""Google OAuth token-exchange client.

Every other operator-configured vendor this repo talks to (Kevel, Triton,
Xandr, GAM, Approximated) routes through ``src/adapters/`` or a service, never
a Flask blueprint. The Google OAuth token exchange was the one remaining
exception: a hardcoded endpoint, a hand-built form body, and an untyped
response dict lived inside ``src/admin/blueprints/auth.py``'s ``gam_callback``
view. Moved here so the layer choice matches every other vendor, following the
precedent ``src/services/approximated_client.py`` set for the same move.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from src.core.security.egress.destination import VendorConstant
from src.core.security.outbound_http import send

# The Google OAuth token endpoint, hoisted so the exchange can be driven at a
# local origin. Operator-configured infrastructure, not a counterparty URL.
# A VendorConstant, matching APPROXIMATED_BASE_URL's typing (GH #1802).
GOOGLE_TOKEN_URL = VendorConstant(url="https://oauth2.googleapis.com/token")


@dataclass(frozen=True)
class GoogleTokenResponse:
    """The fields this application reads off Google's token endpoint.

    Only ``refresh_token`` is consumed today; the rest are carried for
    completeness and future callers. Not a closed mirror of Google's own
    response shape — Google can add fields this dataclass does not model, and
    a caller that supplied a valid authorization code succeeded regardless, so
    parsing tolerates unknown keys the same way the dict this replaces did.
    """

    refresh_token: str | None
    access_token: str | None = None
    expires_in: int | None = None
    token_type: str | None = None
    scope: str | None = None


def exchange_authorization_code(
    code: str, *, client_id: str, client_secret: str, redirect_uri: str
) -> GoogleTokenResponse:
    """Exchange an OAuth authorization code for tokens.

    Raises ``OutboundError`` for anything other than success — the caller
    reads ``exc.http_status`` to distinguish a rejected code (400) from an
    upstream failure, the same split ``approximated_client.get_dns_token``
    documents.
    """
    # Form-encoded, explicitly: the seam has no ``data=`` shortcut, and the
    # token endpoint requires application/x-www-form-urlencoded. Building the
    # body here keeps what goes on the wire visible instead of implied by a
    # client default.
    token_body = urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    ).encode()

    # max_attempts=1: an authorization code is single-use, so a retried
    # exchange cannot succeed and only burns the code. Not a behaviour
    # change — this call never retried.
    result = send(
        GOOGLE_TOKEN_URL.url,
        method="POST",
        content=token_body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        max_attempts=1,
        timeout=30.0,
    )

    data = result.json()
    return GoogleTokenResponse(
        refresh_token=data.get("refresh_token"),
        access_token=data.get("access_token"),
        expires_in=data.get("expires_in"),
        token_type=data.get("token_type"),
        scope=data.get("scope"),
    )
