"""Unit tests for ``core.main._resolve_public_url``.

Replaces the bytes-rewriting ``AgentCardPublicUrlMiddleware`` removed in
the adcp 5.1 #650 migration. The SDK now invokes this resolver per
request to ``/.well-known/agent-card.json`` and renders the result into
the card's ``supportedInterfaces[].url``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from core.main import _resolve_public_url


def _request(*, headers: dict[str, str], scheme: str = "http"):
    """Build a duck-typed Starlette request with case-insensitive headers."""

    class _Headers:
        def __init__(self, mapping: dict[str, str]) -> None:
            self._mapping = {k.lower(): v for k, v in mapping.items()}

        def get(self, key: str, default: str = "") -> str:
            return self._mapping.get(key.lower(), default)

    return SimpleNamespace(headers=_Headers(headers), url=SimpleNamespace(scheme=scheme))


def test_prefers_x_forwarded_host_over_host():
    request = _request(headers={"x-forwarded-host": "tenant.example.com", "host": "internal"})
    assert _resolve_public_url(request) == "https://tenant.example.com/"


def test_falls_back_to_host_header_when_no_xff():
    request = _request(headers={"host": "tenant.example.com"})
    assert _resolve_public_url(request) == "https://tenant.example.com/"


def test_uses_x_forwarded_proto():
    request = _request(headers={"host": "tenant.example.com", "x-forwarded-proto": "http"})
    assert _resolve_public_url(request) == "http://tenant.example.com/"


def test_defaults_to_https_when_no_proto_header_and_scheme_unset():
    request = _request(headers={"host": "tenant.example.com"}, scheme="")
    assert _resolve_public_url(request) == "https://tenant.example.com/"


def test_strips_extra_xff_entries():
    request = _request(headers={"x-forwarded-host": "tenant.example.com, internal-lb"})
    assert _resolve_public_url(request) == "https://tenant.example.com/"


def test_falls_back_to_public_url_env_when_no_host_headers():
    request = _request(headers={})
    with patch.dict("os.environ", {"PUBLIC_URL": "https://fixed.example.com"}, clear=False):
        assert _resolve_public_url(request) == "https://fixed.example.com"


def test_falls_back_to_loopback_when_no_host_and_no_env(monkeypatch):
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    monkeypatch.setenv("ADCP_PORT", "9999")
    request = _request(headers={})
    assert _resolve_public_url(request) == "http://localhost:9999/"


def test_loopback_host_preserves_request_scheme():
    request = _request(headers={"host": "localhost:8080"}, scheme="http")
    assert _resolve_public_url(request) == "http://localhost:8080/"
