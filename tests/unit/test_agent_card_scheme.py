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
