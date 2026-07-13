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


def test_agent_card_emits_release_precision_adcp_version():
    """The A2A agent card must emit a RELEASE-precision ``adcp_version`` on the wire.

    The AdCP version-negotiation contract (``core/version-envelope.json``) says
    wire ``adcp_version`` values are release-precision — the patch component and
    build metadata are not valid wire values (only the pre-release tag is kept).
    Our pin is patch-precision (``3.1.0-beta.3``); the card must normalize it to
    ``3.1-beta.3``. This pins the CARD output (a revert to emitting the raw pin
    reddens it) and cross-checks that the value is accepted, unchanged, by the
    adcp SDK's own resolver — the client that parses the card. #1544.
    """
    from adcp import get_adcp_spec_version
    from adcp._version import normalize_to_release_precision, resolve_adcp_version

    from src.a2a_server.adcp_a2a_server import create_agent_card

    card = create_agent_card()
    ext = next(e for e in card.capabilities.extensions if "adcp-extension" in e.uri)
    wire = ext.params["adcp_version"]

    raw = get_adcp_spec_version()
    assert wire == normalize_to_release_precision(raw), (
        f"card must emit release-precision adcp_version, got {wire!r} for pin {raw!r}"
    )
    # The SDK (the client parsing the card) accepts the wire value unchanged.
    assert resolve_adcp_version(wire) == wire
    # The extension URI still references the FULL versioned schema path.
    assert raw in ext.uri
