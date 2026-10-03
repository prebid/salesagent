"""The pure host check in front of the on-demand TLS gate's tenant lookup.

``GET /tls/ask`` is unauthenticated, so a host that fails ``normalize_hostname`` must be
refused before any query runs. The endpoint itself, over real tenant rows, is graded by
``tests/integration/test_tls_ask_endpoint.py``.
"""

from __future__ import annotations

import pytest

from src.core.domain_routing import normalize_hostname


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Acme.Agent.Example.COM", "acme.agent.example.com"),
        ("acme.agent.example.com.", "acme.agent.example.com"),
        ("  ads.publisher.example ", "ads.publisher.example"),
        ("xn--bcher-kva.example", "xn--bcher-kva.example"),
        ("a" * 63 + ".example", "a" * 63 + ".example"),
    ],
)
def test_hostname_is_normalized(raw, expected):
    assert normalize_hostname(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        ".",
        "a..b",
        "a b.example",
        "host.example:443",
        "evil.example/path",
        "*.example.com",
        "-lead.example",
        "trail-.example",
        "under_score.example",
        "a" * 64 + ".example",
        ("a" * 60 + ".") * 5 + "example",  # 312 characters, over the 253 limit
        "Kelvin.example",  # KELVIN SIGN: str.lower() folds it to "k"
    ],
)
def test_malformed_hostname_is_rejected(raw):
    assert normalize_hostname(raw) is None
