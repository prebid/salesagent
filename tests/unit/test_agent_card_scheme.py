"""Unit coverage for the A2A agent-card URL scheme derivation.

Guards PR #1420 review finding #3: ``_create_dynamic_agent_card`` derives the
advertised scheme from ``X-Forwarded-Proto`` (the scheme nginx terminated)
instead of a localhost heuristic, so an http-only reverse proxy stops getting
an https agent card. That production change shipped untested — a one-line
revert reddened nothing. These cover all four branches of the scheme logic.

The host is fixed to a non-localhost value, whose heuristic is https; a
resulting http scheme therefore proves the forwarded header won (a revert to
the heuristic would yield https and fail).
"""

from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from src.app import _create_dynamic_agent_card


def _scheme(headers: dict) -> str:
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


def test_agent_card_adcp_extension_follows_v2_convention():
    """The A2A agent card's AdCP extension follows the pinned A2A guide's convention.

    Per ``dist/docs/3.1.1/building/by-layer/L0/a2a-guide.mdx`` § "AdCP
    Extension": the extension URI is the STABLE
    ``https://adcontextprotocol.org/extensions/adcp`` (the versioned
    ``adcp-extension.json`` schema was a v2 artifact, removed in v3, so a
    ``/schemas/<version>/...`` URI addresses nothing), and the card's
    ``adcp_version`` is a v2 static-metadata CONVENTION emitted at full patch
    precision (e.g. ``3.1.1``). It is explicitly NOT subject to the v3
    envelope release-precision rule — that rule governs the envelope-root
    adcp_version on request/response wire, not this card. Release-precision here
    would also fail the retained deprecated three-component version pattern.
    Normative v3 discovery is get_adcp_capabilities, not this card. #1544.
    """
    from adcp import get_adcp_spec_version

    from src.a2a_server.adcp_a2a_server import create_agent_card

    stable_uri = "https://adcontextprotocol.org/extensions/adcp"
    card = create_agent_card()
    ext = next(e for e in card.capabilities.extensions if e.uri == stable_uri)

    # Stable extension URI, never a versioned schema path.
    assert ext.uri == stable_uri
    # Patch-precision v2 convention — the raw pin, not release-precision.
    assert ext.params["adcp_version"] == get_adcp_spec_version()
