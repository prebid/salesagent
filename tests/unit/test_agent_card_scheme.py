"""Unit coverage for the A2A agent-card URL scheme derivation.

Guards PR #1420 review finding #3: ``_create_dynamic_agent_card`` derives the
advertised scheme from ``X-Forwarded-Proto`` (the scheme nginx terminated)
instead of a localhost heuristic, so an http-only reverse proxy stops getting
an https agent card. That production change shipped untested — a one-line
revert reddened nothing. These cover all four branches of the scheme logic.

Since #1291 (trust-root work item A3) that header ladder is the FALLBACK, not the
primary path: when the Host routes to a tenant, the card advertises that
tenant's canonical agent URL (derived from its STORED host), so the card and
brand.json's A2A ``agents[].url`` are the same string byte-for-byte. These cases
therefore pin the no-tenant branch explicitly — which is also what keeps them a
unit test, since the resolved branch reads the database.

The host is fixed to a non-localhost value, whose heuristic is https; a
resulting http scheme therefore proves the forwarded header won (a revert to
the heuristic would yield https and fail).
"""

from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse

import pytest

from src.app import _create_dynamic_agent_card
from tests.helpers.agent_card import host_routes_to_no_tenant


def _scheme(headers: dict) -> str:
    with host_routes_to_no_tenant(headers["Apx-Incoming-Host"]):
        card = _create_dynamic_agent_card(SimpleNamespace(headers=headers))
    return urlparse(card.supported_interfaces[0].url).scheme


@pytest.mark.parametrize(
    "host, xfp, expected",
    [
        ("tenant.example.com", "http", "http"),  # valid header wins over the https heuristic
        ("tenant.example.com", "http, https", "http"),  # proxy chain: first (client-facing) hop
        ("tenant.example.com", "ftp", "https"),  # invalid scheme -> fall back to heuristic
        ("tenant.example.com", None, "https"),  # header absent -> heuristic (non-localhost -> https)
        ("localhost", None, "http"),  # header absent -> heuristic (localhost -> http)
    ],
)
def test_agent_card_scheme(host, xfp, expected):
    headers = {"Apx-Incoming-Host": host}
    if xfp is not None:
        headers["X-Forwarded-Proto"] = xfp
    assert _scheme(headers) == expected


def test_resolved_tenant_url_wins_over_the_header_ladder():
    """A Host that routes to a tenant takes the tenant's STORED identity.

    Without this the card keeps echoing whichever host the caller used, so a
    tenant reachable at more than one host publishes more than one identity and
    brand.json's byte-equal match becomes a coin flip.
    """
    headers = {"Apx-Incoming-Host": "proxy-front.example.com", "X-Forwarded-Proto": "http"}

    with patch("src.app._canonical_a2a_url", return_value="https://seller.example.com/a2a"):
        card = _create_dynamic_agent_card(SimpleNamespace(headers=headers))

    assert card.supported_interfaces[0].url == "https://seller.example.com/a2a"
